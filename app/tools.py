"""The tools the model can call (docs/tools.md). Each handler checks its gates in server code and
returns a Result or raises Refused; app.main logs every call.

The model asks; the server decides. A gate here is never a flag the model sets: read-back counts
only after the server returned the sentence and a caller line followed, the safety gate reads the
call's own state, and ownership is checked against the customers matched on this call.
"""

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise

from . import booking, geocode, heads_up, lookup, outbound, pace, records, safety, transfer, weather
from . import capacity as capacity_rules
from . import priority as prio
from .db import iso, tx
from .envelope import Refused, Result, ok
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .lifecycle import approval_unclear
from .schemas import (
    BookIn,
    CallbackTime,
    CancelIn,
    DoNotCallIn,
    EndCallIn,
    FindAddressIn,
    FindCustomerIn,
    FindSlotsIn,
    FlagIn,
    HistoryIn,
    LeadIn,
    MoveIn,
    NoteIn,
    ReadBackIn,
    RescheduleIn,
    ServiceAreaIn,
    Slot,
)
from .settings import BOOKING_DAYS_AHEAD, LIMITS
from .tenant import Tenant
from .text import (
    SPELLED_RUN,
    clock_time,
    digit_runs,
    digits_spoken,
    has_phrase,
    number_tokens,
    phone_display,
    phone_key,
    phone_said,
    says_goodbye,
    street_key,
    unit_key,
    words,
)
from .text import (
    STREET_WORDS as STREET_KEY_WORDS,
)

MAX_MATCHES = 5
SHAPE_DAYS = 3


@dataclass
class Ctx:
    conn: sqlite3.Connection
    call: sqlite3.Row
    tenant: Tenant
    fsm: FsmAdapter
    cache: SlotCache
    now: datetime
    side_model: prio.SideModel | None = None
    idempotency_key: str | None = None
    # Where the transfer numbers are read from (app/transfer.py): the server's environment, or a
    # scratch mapping in evals so a run never reads a real number.
    numbers: Mapping[str, str] = field(default_factory=lambda: os.environ)
    # The address lookup (app/geocode.py); None when GEOCODER is off, and the agent asks for ZIPs.
    geocoder: geocode.Geocoder | None = None
    # The numbers a heads-up call may dial (app/heads_up.py); empty dials none.
    heads_up_allowlist: frozenset[str] = frozenset()

    def reload(self) -> None:
        self.call = records.get_call(self.conn, self.call["id"])


# ---- shared checks -------------------------------------------------------------------------


def _flag_and_refuse(ctx: Ctx, code: str, say: str, kind: str, reason: str, **data) -> None:
    """Store a flag in its own transaction, then refuse, so the refusal can't roll the flag back."""
    with tx(ctx.conn):
        records.add_flag(ctx.conn, ctx.call["id"], kind, reason, ctx.now, "High")
    raise Refused(code, say, **data)


def _limit(ctx: Ctx, kind: str, count: int) -> None:
    if count < LIMITS[kind]:
        return
    reason = f"Per-call limit reached: {kind}"
    flagged = ctx.conn.execute(
        "SELECT 1 FROM flags WHERE call_id = ? AND kind = 'limit' AND reason = ?",
        (ctx.call["id"], reason),
    ).fetchone()
    if flagged:  # one flag per limit per call, however often the model retries
        raise Refused("limit", ctx.tenant.line("limit"), limit=kind)
    _flag_and_refuse(ctx, "limit", ctx.tenant.line("limit"), "limit", reason, limit=kind)


def _safety_gate(ctx: Ctx) -> bool:
    """Booking tools wait until the safety lines were said and a person flagged (spec S-18). After
    that the agent may offer a visit for once the problem is shut off, without the caller having to
    insist (eval round 2). Returns True when this is a safety call past that point, so the booking
    is logged as made on a safety call."""
    state, safety = ctx.call["safety_state"], ctx.tenant.safety
    if state == "signal":
        lines = " ".join(safety["kinds"][ctx.call["safety_kind"]]["lines"].split())
        raise Refused("gate_safety", lines, need="safety_lines")
    if state == "checking":
        raise Refused(
            "gate_safety",
            safety["vague"][ctx.call["safety_kind"]]["question"],
            need="safety_check_answer",
        )
    return state == "lines_said" and ctx.call["safety_flag_id"] is not None


def _record_out_of_area(ctx: Ctx, zip_code: str) -> None:
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT OR IGNORE INTO out_of_area (call_id, zip, at) VALUES (?, ?, ?)",
            (ctx.call["id"], zip_code, iso(ctx.now)),
        )


def _zip_known(ctx: Ctx, zip_code: str) -> None:
    """A ZIP the server acts on came from the caller or their record, never from the model: it was
    said in a caller line on this call or one linked to it ("one one two two six" counts), or it is
    on a location of a customer matched on this call. Otherwise the agent asks for it, so a guessed
    ZIP can't turn a caller away as out of area or put a booking at the wrong place. A ZIP the
    address lookup found for a house and street the caller said counts too (find_address)."""
    ids = records.thread(ctx.conn, ctx.call["id"])
    lines = ctx.conn.execute(
        f"SELECT text FROM turns WHERE speaker = 'caller' "
        f"AND call_id IN ({','.join('?' * len(ids))})",
        ids,
    )
    # The whole run, or ZIP plus four: "718 112 2600" is a phone number, not 11226.
    said = {run for line in lines for run in digit_runs(line["text"])}
    if zip_code in said or any(len(run) == 9 and run.startswith(zip_code) for run in said):
        return
    for customer_id in records.scoped_customers(ctx.conn, ctx.call["id"]):
        try:
            on_file = ctx.fsm.locations(customer_id)
        except (FsmUnavailable, FsmRejected):
            continue
        if any(loc["address"]["zip"] == zip_code for loc in on_file):
            return
    # A ZIP the lookup found for a house and street the caller said (find_address).
    found = ctx.conn.execute(
        f"SELECT zips_json FROM address_lookups WHERE status = 'one' "
        f"AND call_id IN ({','.join('?' * len(ids))})",
        ids,
    )
    if any(zip_code in json.loads(row["zips_json"]) for row in found):
        return
    raise Refused(
        "zip_not_heard",
        "What's the ZIP code there?",
        need="ask the caller for the ZIP; use only one they say",
        zip=zip_code,
    )


# ---- the address lookup --------------------------------------------------------------------


def _caller_lines(ctx: Ctx) -> list[sqlite3.Row]:
    """Caller lines on this call and the calls linked to it, oldest first."""
    ids = records.thread(ctx.conn, ctx.call["id"])
    return ctx.conn.execute(
        f"SELECT call_id, seq, text FROM turns WHERE speaker = 'caller' "
        f"AND call_id IN ({','.join('?' * len(ids))}) ORDER BY at, seq",
        ids,
    ).fetchall()


# How many words may sit between a house number and its street ("248 on Duffield").
STREET_GAP = 2
STREET_GAP_WORDS = {"on", "at", "the", "off", "um", "uh", "is", "its", "spelled"}
# Words around a bare house number given as an answer: "it's 248", "um, two forty eight".
ANSWER_FILLER = {"its", "it", "is", "um", "uh", "the", "number", "house", "okay", "ok", "yeah",
                 "yes", "so", "oh", "thats", "building", "were", "at", "im"}


def _address_said(ctx: Ctx, house: str, street: str, rows: list | None = None) -> bool:
    """The caller said this house number right before this street, so a lookup can't turn a
    street the model made up, or a number said about something else ("out for 12 days", "I
    called 2 times about Union Street", "my zip is 11201 and I live on Union"), into a ZIP.

    Numbers count however they were said ("two forty eight", "two hundred forty eight", digits
    one by one), and the street's words are whole words ("Court", not "electrician"). The
    number may also be the caller's whole answer to the agent asking for it, with the street
    said in another line, or come before a street the caller then spelled letter by letter.
    rows limits it to those caller lines (call_id, seq, text); all of the thread's by default."""
    rows = _caller_lines(ctx) if rows is None else rows
    want = geocode.house_key(house)
    names = geocode.street_name_words(street)
    if not want or not names:
        return False
    lines = [_keyed(number_tokens(r["text"])) for r in rows]
    for i, tokens in enumerate(lines):
        for k, token in enumerate(tokens):
            if token != want or "zip" in tokens[max(0, k - 2) : k] or "code" in tokens[max(0, k - 2) : k]:
                continue
            after = [t for t in tokens[k + 1 :] if t.isalnum()]
            # Only small words may sit between them: "248 on Duffield", not "2 times about Union".
            skip = 0
            while skip < min(STREET_GAP, len(after)) and after[skip] in STREET_GAP_WORDS:
                skip += 1
            if after[skip : skip + len(names)] == names:
                return True
            # "52 Dufield Street", then the street spelled in one of the next lines.
            if any(t in geocode.STREET_TYPE_WORDS for t in after):
                for later in rows[i + 1 : i + 3]:
                    if SPELLED_RUN.search(later["text"]) and _street_in(later["text"], names):
                        return True
    return any(
        _answers_number(ctx, row, want) for row in rows
    ) and any(_street_in(row["text"], names) for row in rows)


def _keyed(tokens: list[str]) -> list[str]:
    """Tokens as the street key writes them: "court" is "ct", "avenue" is "ave"."""
    return [STREET_KEY_WORDS.get(t, t) for t in tokens]


def _answers_number(ctx: Ctx, row: sqlite3.Row, want: str) -> bool:
    """The line is the house number alone, said right after the agent asked for a number."""
    said = [t for t in number_tokens(row["text"]) if t.isalnum() and t not in ANSWER_FILLER]
    if said != [want]:
        return False
    asked = ctx.conn.execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'agent' AND seq < ? "
        "ORDER BY seq DESC LIMIT 1",
        (row["call_id"], row["seq"]),
    ).fetchone()
    return bool(asked) and "number" in words(asked["text"])


def _street_in(line: str, names: list[str]) -> bool:
    tokens = set(_keyed(number_tokens(line)))
    return all(name in tokens for name in names)


def _record_lookup(ctx: Ctx, house: str, street: str, hint: str, found: geocode.Found) -> None:
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO address_lookups (call_id, house, street, hint, status, zips_json, "
            "source, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.call["id"],
                geocode.house_key(house),
                geocode.street_match_key(street),
                hint,
                found.status,
                json.dumps(found.zips()),
                found.source,
                iso(ctx.now),
            ),
        )


def _places_spoken(places: tuple[geocode.Place, ...]) -> str:
    """ "Brooklyn or Manhattan", or the neighborhoods when the boroughs are the same."""
    boroughs = [p.borough for p in places]
    if all(boroughs) and len(set(boroughs)) == len(boroughs):
        return _join(boroughs, "or")
    areas = [p.area for p in places]
    if all(areas) and len(set(areas)) == len(areas):
        return _join(areas, "or")
    return "ZIP code " + _join([digits_spoken(p.zip) for p in places], "or")


def _on_file_here(ctx: Ctx, house: str, street: str, zip_code: str) -> bool:
    """This address is on a matched customer's record with this ZIP: the company has been there,
    so the record wins over the lookup."""
    want = (geocode.house_key(house), geocode.street_match_key(street))
    for customer_id in records.scoped_customers(ctx.conn, ctx.call["id"]):
        try:
            on_file = ctx.fsm.locations(customer_id)
        except (FsmUnavailable, FsmRejected):
            continue
        for loc in on_file:
            split = geocode.split_address(loc["address"]["street"])
            if split and loc["address"]["zip"] == zip_code and (
                geocode.house_key(split[0]),
                geocode.street_match_key(split[1]),
            ) == want:
                return True
    return False


def _zip_mismatch(ctx: Ctx, zip_code: str, street: str | None = None, hint: str = "") -> None:
    """A ZIP the caller gave that the lookup of their street disagrees with is refused until the
    caller settles it: the agent reads back the address the lookup found and asks which ZIP is
    right. Their own ZIP counts again once they say it after that question; the lookup's ZIP
    counts once they pick it in the read-back. `street` is the address being read back; without
    it, the addresses in the caller's own lines are checked. A lookup that is slow, down, finds
    nothing or finds several places leaves the caller's ZIP as it is."""
    if ctx.geocoder is None:
        return
    if street:
        split = geocode.split_address(street)
        addresses = [split] if split else []
    else:
        addresses = []
        for row in reversed(_caller_lines(ctx)):
            for found_address in geocode.addresses_in(row["text"]):
                if found_address not in addresses:
                    addresses.append(found_address)
    for house, name in addresses[:2]:
        found = ctx.geocoder.find(house, name, hint, wait_s=geocode.CHECK_WAIT_S)
        if found.status != "one" or found.places[0].zip == zip_code:
            continue
        if _on_file_here(ctx, house, name, zip_code):
            continue
        _ask_mismatch(ctx, house, name, found.places[0], zip_code, hint)


def _ask_mismatch(ctx: Ctx, house: str, street: str, place: geocode.Place, said_zip: str,
                  hint: str = "") -> None:
    """Refuse the caller's ZIP for this street until they settle it, unless they already have:
    said it again after the question. Their own ZIP then stands."""
    key = (ctx.call["id"], geocode.house_key(house), geocode.street_match_key(street), said_zip)
    row = ctx.conn.execute(
        "SELECT * FROM address_checks WHERE call_id = ? AND house = ? AND street = ? "
        "AND said_zip = ?",
        key,
    ).fetchone()
    if row and row["settled_at"]:
        return
    if row and _said_since(ctx, said_zip, row["asked_seq"]):
        with tx(ctx.conn):
            ctx.conn.execute(
                "UPDATE address_checks SET settled_at = ? WHERE call_id = ? AND house = ? "
                "AND street = ? AND said_zip = ?",
                (iso(ctx.now), *key),
            )
        return
    # The lookup's ZIP counts as known from here, as a find_address answer would, when the street
    # came from the caller: the read-back that follows is where they pick it.
    if _address_said(ctx, house, street):
        _record_lookup(ctx, house, street, hint, geocode.Found("one", (place,), "check"))
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO address_checks (call_id, house, street, said_zip, found_zip, "
            "asked_seq, at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (call_id, house, street, said_zip) DO UPDATE SET "
            "asked_seq = excluded.asked_seq, asks = asks + 1",
            (*key, place.zip, ctx.call["turn_seq"], iso(ctx.now)),
        )
    raise Refused(
        "zip_mismatch",
        ctx.tenant.line(
            "zip_mismatch",
            street=place.spoken_street(),
            where=place.area or place.borough,
            found=digits_spoken(place.zip),
            said=digits_spoken(said_zip),
        ),
        need="the ZIP the caller gave doesn't match the street; use the ZIP they pick",
        found_zip=place.zip,
        said_zip=said_zip,
        street=place.spoken_street(),
    )


def _zip_said_with(ctx: Ctx, house: str) -> str | None:
    """The last ZIP the caller said on the call: with the address, before it ("I'm in 11205, at
    248 Duffield") or as a correction after it."""
    want = geocode.house_key(house)
    said = None
    for row in _caller_lines(ctx):
        for run in digit_runs(row["text"]):
            if run.startswith(want) and len(run) - len(want) in (5, 9):
                run = run[len(want):]  # the house number and ZIP said as one run
            if len(run) in (5, 9) and run != want and _plausible_zip(ctx, run[:5]):
                said = run[:5]
    return said


def _plausible_zip(ctx: Ctx, zip_code: str) -> bool:
    """A five-digit run that can be a ZIP here: one the company serves, or one in the lookup's
    area. Numbers said one by one run together ("ten sixty two one oh six two" holds 21062), and
    a run like that must never decline a caller as out of area (eval run 20261104-174038)."""
    if zip_code in ctx.tenant.service_area:
        return True
    prefixes = (ctx.tenant.address_lookup or {}).get("zip_boroughs") or {}
    return zip_code[:3] in {str(p) for p in prefixes}


def _said_since(ctx: Ctx, zip_code: str, seq: int) -> bool:
    rows = ctx.conn.execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq > ?",
        (ctx.call["id"], seq),
    )
    return any(
        run == zip_code or (len(run) == 9 and run.startswith(zip_code))
        for row in rows
        for run in digit_runs(row["text"])
    )


def _out_of_area(ctx: Ctx, zip_code: str) -> None:
    if zip_code not in ctx.tenant.service_area:
        _record_out_of_area(ctx, zip_code)
        # A customer who moved out of the area still has visits at the old address to cancel, so
        # the decline waits for record_move, whose sentence says it with the cancellation.
        if _mover_pending(ctx, zip_code=zip_code):
            raise Refused(
                "out_of_area",
                "",
                zip=zip_code,
                need="the caller moved out of the area: don't decline yet. Read back the new "
                "address, then call record_move; its sentence tells the caller",
            )
        raise Refused("out_of_area", ctx.tenant.line("out_of_area"), zip=zip_code)


def _mover_pending(ctx: Ctx, address: dict | None = None, zip_code: str | None = None) -> bool:
    """A caller on file said they moved, no move is recorded on the call yet, and the address (or
    ZIP) in hand isn't one already on their record: "we just moved in two years ago" from a
    caller booking their own home is no move."""
    scoped = [
        cid for cid, row in records.scoped_customers(ctx.conn, ctx.call["id"]).items()
        if row["customer_type"] not in ctx.tenant.rejected_customer_types
    ]
    if not scoped or not _said_moved(ctx) or _moved_on_call(ctx):
        return False
    try:
        on_file = [loc for cid in scoped for loc in ctx.fsm.locations(cid)]
    except (FsmUnavailable, FsmRejected):
        return False
    if address is not None and any(booking.same_address(loc, address) for loc in on_file):
        return False
    return not (zip_code and any(loc["address"]["zip"] == zip_code for loc in on_file))


def _commercial(ctx: Ctx) -> None:
    """A caller who said they're a business, or whose number matches only commercial accounts,
    can't book, whatever the agent asks for (spec S-05)."""
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    by_caller_id = [r for r in scoped.values() if r["matched_by"] == "caller_id"]
    rejected = ctx.tenant.rejected_customer_types
    only_commercial = by_caller_id and all(r["customer_type"] in rejected for r in by_caller_id)
    if ctx.call["commercial"] or only_commercial:
        raise Refused("customer_type", ctx.tenant.line("commercial"))


def _approval_unclear(ctx: Ctx, approver: str) -> bool:
    """Who can approve the work isn't settled: the caller said a co-op board or a landlord has to
    approve, on this call or one linked to it, or the approver the agent recorded says so."""
    cfg = ctx.tenant.approval
    if any(has_phrase(words(approver), p) for p in cfg["unclear_approver"]):
        return True
    thread = records.thread(ctx.conn, ctx.call["id"])
    rows = ctx.conn.execute(
        f"SELECT text FROM turns WHERE speaker = 'caller' "
        f"AND call_id IN ({','.join('?' * len(thread))})",
        thread,
    )
    return any(approval_unclear(r["text"], cfg) for r in rows)


def approval_call_time(tenant: Tenant, window_start: datetime, now: datetime) -> datetime:
    """When to call about approval: a set time before the window opens, moved back into calling
    hours (the evening before, for an early window). If that time has passed, the next time inside
    calling hours, which can be after the window opens; the caller of this checks for that."""
    opens, closes = (time.fromisoformat(t) for t in tenant.callbacks["outbound_hours"])
    # Calling hours end before `closes` (records.in_outbound_hours), so aim half an hour inside.
    last = (datetime.combine(datetime.min, closes) - timedelta(minutes=30)).time()
    due = tenant.local(
        window_start - timedelta(hours=tenant.approval["callback_hours_before_window"])
    )
    if due.time() > last:
        due = datetime.combine(due.date(), last, tenant.tz)
    elif due.time() < opens:
        due = datetime.combine(due.date() - timedelta(days=1), last, tenant.tz)
    if due >= now:
        return due
    if records.in_outbound_hours(tenant, now):
        return now
    local = tenant.local(now)
    day = local.date() if local.time() < opens else local.date() + timedelta(days=1)
    return datetime.combine(day, opens, tenant.tz)


def _job_type(ctx: Ctx, code: str) -> dict:
    job_type = ctx.tenant.job_types.get(code)
    if not job_type:
        raise Refused(
            "invalid_arguments",
            "Let me check what kind of visit that is.",
            errors={"job_type": f"one of {', '.join(ctx.tenant.job_types)}"},
        )
    return job_type


@dataclass
class Decided:
    """The server's priority for a job and the facts it came from. raised_after_wait: the level is
    higher than the caller's lines before they heard the wait would give, so the dispatcher can
    judge a fact first said after it. A note for a person, never a gate; nothing is lowered."""

    facts: prio.Facts
    priority: prio.Priority
    raised_after_wait: bool = False


def _earlier_temps(ctx: Ctx) -> list[float]:
    """Inside temperatures the agent passed on earlier find_slots and booking calls, newest first,
    so a booking that leaves the number out still starts from the one the caller gave."""
    found = []
    for row in ctx.conn.execute(
        "SELECT args_json FROM tool_calls WHERE call_id = ? AND tool IN ('find_slots', "
        "'book_appointment') ORDER BY id DESC",
        (ctx.call["id"],),
    ):
        args = json.loads(row["args_json"] or "{}")
        temp = (args.get("facts") or args.get("details") or {}).get("inside_temp_f")
        if temp is not None:
            found.append(temp)
    return found


def _facts(ctx: Ctx, job_type: str, turns: list, temps: list[float], outside) -> prio.Facts:
    """Facts from these lines of the thread alone: the caller's words, the inside temperature only
    if a caller line carries it, and the server's own readings."""
    pairs = [(t["speaker"], t["text"]) for t in turns]
    caller = "\n".join(text for speaker, text in pairs if speaker == "caller")
    cfg = ctx.tenant.priority.get("inside_temp", {})
    temp, line = None, ""
    for value in temps:
        heard = prio.heard_inside_temp(pairs, value, cfg)
        if heard is not None:
            temp, line = value, heard
            break
    return prio.Facts(
        job_type,
        caller,
        temp,
        ctx.call["safety_state"] in ("signal", "lines_said"),
        outside.now_f,
        outside.coldest_f,
        heating_indoors=safety.heating_signal(caller, ctx.tenant.safety),
        caller_text=caller,
        inside_temp_line=line,
    )


def _priority(ctx: Ctx, job_type: str, inside_temp_f: float | None) -> Decided:
    """The server's priority from the caller's own lines on the thread and its readings (spec
    S-21, DECISIONS.md 072). What the model wrote in `issue` and `who_home` goes to the job notes
    and raises nothing; its inside temperature counts only when the caller said that number."""
    outside = weather.current(ctx.conn, ctx.tenant, ctx.now)
    turns = records.thread_turns(ctx.conn, ctx.call["id"])
    temps = ([inside_temp_f] if inside_temp_f is not None else []) + _earlier_temps(ctx)
    facts = _facts(ctx, job_type, turns, temps, outside)
    # The side model's cache is keyed on the text the turn handler prefetched with.
    facts.caller_text = records.caller_text(ctx.conn, ctx.call["id"])
    decided = prio.decide(facts, ctx.tenant.priority, ctx.side_model)
    wait = records.wait_named(ctx.conn, ctx.call["id"])
    if wait is None:
        return Decided(facts, decided)
    calls = list(reversed(records.thread(ctx.conn, ctx.call["id"])))
    wait_at = (calls.index(wait[0]), wait[1])
    cut = next(
        (i for i, t in enumerate(turns) if (calls.index(t["call_id"]), t["seq"]) > wait_at),
        len(turns),
    )
    if not any(t["speaker"] == "caller" for t in turns[cut:]):
        return Decided(facts, decided)
    # Rules alone on both sides: a second side model call on the shorter text would have nothing
    # prefetched and could stall the tool (PR #126 review).
    before = prio.decide(_facts(ctx, job_type, turns[:cut], temps, outside), ctx.tenant.priority)
    now = prio.decide(facts, ctx.tenant.priority)
    raised = prio.LEVELS.index(now.level) > prio.LEVELS.index(before.level)
    return Decided(facts, decided, raised)


def _key(ctx: Ctx, tool: str, args: dict) -> str:
    """The key sent to the FSM: the voice layer's Idempotency-Key when it sent one."""
    if ctx.idempotency_key:
        return f"{ctx.call['id']}:{tool}:{ctx.idempotency_key}"
    digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()
    return f"{ctx.call['id']}:{tool}:{digest[:16]}"


def _attempt_key(ctx: Ctx, tool: str, args: dict) -> str:
    """The key for a new booking, reschedule or cancel row. A request refused or failed before
    (a taken slot, an FSM error) may be sent again with the same Idempotency-Key once the cause is
    gone, so each new attempt gets its own key."""
    base = _key(ctx, tool, args)
    retry = f"{base}:retry"
    # substr, not LIKE: an Idempotency-Key may contain _ or %.
    tried = ctx.conn.execute(
        "SELECT COUNT(*) FROM bookings WHERE fsm_key = ? OR substr(fsm_key, 1, ?) = ?",
        (base, len(retry), retry),
    ).fetchone()[0]
    return f"{base}:retry{tried}" if tried else base


def _callback(ctx: Ctx, minutes_key: str, reason: str) -> tuple[int | None, datetime]:
    due = ctx.now + timedelta(minutes=ctx.tenant.callbacks[minutes_key])
    with tx(ctx.conn):
        callback_id = records.add_callback(ctx.conn, ctx.tenant, ctx.call, due, reason, ctx.now)
    return callback_id, due


def _fsm_down_say(ctx: Ctx, callback_id: int | None, due: datetime) -> str:
    if callback_id is None:
        return ctx.tenant.line("fsm_unavailable_no_callback")
    return ctx.tenant.line("fsm_unavailable", time=ctx.tenant.time_spoken(due, ctx.now))


# ---- slots ---------------------------------------------------------------------------------


def open_windows(
    tenant: Tenant,
    fsm: FsmAdapter,
    cache: SlotCache,
    now: datetime,
    job_type: dict,
    level: str,
) -> list[dict]:
    """Open windows from now to the end of the booking range, earliest first. ServiceTitan's time
    frames come through the cache, and the company's rules (urgent holds, after-hours) are applied
    for this priority (app/capacity.py). Also used by the job that offers earlier openings, so
    both read the same cached capacity."""
    today = tenant.local(now).date()
    end_day = today + timedelta(days=BOOKING_DAYS_AHEAD)
    unit = job_type["businessUnitIds"][0]
    # Frames must end by the range's end; the last day's night window ends at 8 the next morning.
    range_end = datetime.combine(end_day + timedelta(days=1), time(8), tenant.tz)

    def fetch():
        return fsm.capacity(now, range_end, unit, job_type["id"])

    frames = cache.get((tenant.slug, job_type["code"], today), fetch)
    return capacity_rules.open_windows(tenant, frames, level, today, end_day)


def _open_windows(ctx: Ctx, job_type: dict, level: str) -> list[dict]:
    return open_windows(ctx.tenant, ctx.fsm, ctx.cache, ctx.now, job_type, level)


def _describe(ctx: Ctx, window: dict) -> dict:
    spec = ctx.tenant.window(window["window"])
    # "Today" or "tomorrow" comes from the visit's own date against the local date now, never from
    # a start time that may sit on another day (a live call just after midnight heard "tomorrow"
    # for that day's visit).
    visit = date.fromisoformat(window["date"])
    day_word = ctx.tenant.day_spoken(visit, ctx.now)
    weekday = visit.strftime("%A")
    # The confirmation names the day and the date ("today, Monday, November 2"), so a caller can
    # catch a wrong day (eval round 3).
    dated = f"{weekday}, {visit.strftime('%B')} {visit.day}"
    return {
        "date": window["date"],
        "window": window["window"],
        "weekday": weekday,
        "day": day_word,
        "day_and_date": dated if day_word == weekday else f"{day_word}, {dated}",
        "label": spec.label() if spec else window["window"],
        "after_hours": window["after_hours"],
    }


def _join(items: list[str], word: str) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {word} {items[-1]}"


def _alternatives(ctx: Ctx, job_type: dict, level: str, n: int = 2) -> list[dict]:
    try:
        windows = _open_windows(ctx, job_type, level)
    except (FsmUnavailable, FsmRejected):
        return []
    return [_describe(ctx, w) for w in windows[:n]]


def _job_alternatives(ctx: Ctx, job: dict) -> list[dict]:
    """Other windows for an existing job. A job whose type the company no longer books (inactive
    in the tenant config) gets none, rather than failing the tool."""
    job_type = ctx.tenant.job_types.get(ctx.tenant.job_type_codes.get(job["jobTypeId"]))
    return _alternatives(ctx, job_type, job["priority"]) if job_type else []


def _slot_bounds(ctx: Ctx, slot: Slot, level: str) -> tuple[datetime, datetime]:
    window = ctx.tenant.window(slot.window)
    today = ctx.tenant.local(ctx.now).date()
    if window is None or not today <= slot.date <= today + timedelta(days=BOOKING_DAYS_AHEAD):
        raise Refused(
            "invalid_slot",
            "Let me look at the open times again.",
            errors={"slot": "not a window the company offers in the booking range"},
        )
    if window.after_hours and level != "Urgent":
        raise Refused(
            "invalid_slot", "That time is only for urgent visits.", errors={"slot": "urgent only"}
        )
    return ctx.tenant.window_bounds(slot.date, window)


def _window_over(ctx: Ctx, window_id: str, start: datetime, end: datetime) -> bool:
    """Has the window started (or, for the night window, ended)? Offered a moment ago, it may have
    gone by since."""
    window = ctx.tenant.window(window_id)
    return end <= ctx.now if window.after_hours else start < ctx.now


# ---- who the caller is ------------------------------------------------------------------------
#
# A change to a customer's visit or account from a number that isn't on that account waits until
# the caller has said a name and an address that match it (issue #128). Only the caller's own
# words count, and only words they said before the agent did, so the agent can never read the
# details out for a yes. Name and address is a low bar (anyone in the household knows both), so
# a change made this way leaves a note for the office.

IDENTITY_ASKS = 2
NOTE_UNKNOWN_NUMBER = "Changed from an unknown number after a name and address check."


def _name_words(text: str) -> list[str]:
    return words(text).split()


def _surname(name: str) -> str:
    named = [w for w in _name_words(name) if len(w) > 1]
    return named[-1] if named else ""


def _has_word(text: str, word: str) -> bool:
    """The word, or the word split in two by speech to text ("Del Gado")."""
    tokens = _name_words(text)
    return word in tokens or any(a + b == word for a, b in pairwise(tokens))


def _address_tokens_in(text: str, house: str, names: list[str]) -> bool:
    tokens = _keyed(number_tokens(text))
    return house in tokens and all(n in tokens for n in names)


def _said_before_agent(turns: list, said_by_agent) -> list:
    """The caller's lines before the first agent line that holds the detail."""
    rows = []
    for turn in turns:
        if turn["speaker"] == "agent" and said_by_agent(turn["text"]):
            break
        if turn["speaker"] == "caller":
            rows.append(turn)
    return rows


def _identity_said(ctx: Ctx, name: str, addresses: list[dict]) -> tuple[bool, bool]:
    """Whether the caller said the customer's surname and one of these addresses (house number and
    street), each before the agent said it."""
    turns = records.thread_turns(ctx.conn, ctx.call["id"])
    surname = _surname(name)
    name_ok = bool(surname) and any(
        _has_word(r["text"], surname)
        for r in _said_before_agent(turns, lambda t: _has_word(t, surname))
    )
    address_ok = False
    for address in addresses:
        split = geocode.split_address(address["street"])
        if not split:
            continue
        house, names = geocode.house_key(split[0]), geocode.street_name_words(split[1])
        rows = _said_before_agent(turns, lambda t, h=house, n=names: _address_tokens_in(t, h, n))
        if rows and _address_said(ctx, split[0], split[1], rows):
            address_ok = True
            break
    return name_ok, address_ok


def _identity_row(ctx: Ctx, customer_id: int) -> sqlite3.Row | None:
    thread = records.thread(ctx.conn, ctx.call["id"])
    return ctx.conn.execute(
        f"SELECT * FROM identity_checks WHERE customer_id = ? "
        f"AND call_id IN ({','.join('?' * len(thread))}) ORDER BY rowid DESC LIMIT 1",
        (customer_id, *thread),
    ).fetchone()


def _on_file_phone(ctx: Ctx, customer_id: int) -> str:
    try:
        phones = [c for c in ctx.fsm.contacts(customer_id) if c["type"] in ("Phone", "MobilePhone")]
    except (FsmUnavailable, FsmRejected):
        return ""
    return phone_key(phones[0]["value"]) if phones else ""


def _identity_gate(ctx: Ctx, customer_id: int, addresses: list[dict], thing: str) -> bool:
    """Refuses with identity_check until the caller has said a name and address matching the
    customer, when the call's number isn't on that customer's account. After two answers that
    don't match, the change is refused for the call (identity_mismatch) and the agent offers a
    callback to the number on file. Returns True when the check was needed and passed, so the
    change is noted for the office."""
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    if customer_id in scoped and scoped[customer_id]["matched_by"] == "caller_id":
        return False
    customer = ctx.fsm.customer(customer_id)
    check = _identity_row(ctx, customer_id)
    if check is not None and check["status"] == "verified":
        return True
    # A question asked on a call that dropped is asked again on this one.
    if check is not None and check["status"] == "asked" and check["call_id"] != ctx.call["id"]:
        check = None
    if check is not None and check["status"] == "mismatch":
        raise Refused(
            "identity_mismatch",
            ctx.tenant.line("identity_mismatch"),
            need="no change can be made on this call; offer the callback, and flag_person if "
            "the caller wants it",
        )
    name_ok, address_ok = _identity_said(ctx, customer["name"], addresses)
    if name_ok and address_ok:
        with tx(ctx.conn):
            ctx.conn.execute(
                "INSERT OR REPLACE INTO identity_checks (call_id, customer_id, status, asked_seq, "
                "asks, at) VALUES (?, ?, 'verified', ?, ?, ?)",
                (ctx.call["id"], customer_id, ctx.call["turn_seq"],
                 check["asks"] if check else 0, iso(ctx.now)),
            )
        return True
    note = (
        "this question was said for you. When the caller answers, call this tool again: the "
        "server checks the answer. Never say whether it matches, and never say the name or "
        "address on file"
    )
    if check is None:
        with tx(ctx.conn):
            ctx.conn.execute(
                "INSERT INTO identity_checks (call_id, customer_id, status, asked_seq, at) "
                "VALUES (?, ?, 'asked', ?, ?)",
                (ctx.call["id"], customer_id, ctx.call["turn_seq"], iso(ctx.now)),
            )
        raise Refused("identity_check", ctx.tenant.line("identity_check", thing=thing), need=note)
    if not records.caller_turn_after(ctx.conn, check["call_id"], check["asked_seq"]):
        raise Refused("identity_check", ctx.tenant.line("identity_check", thing=thing), need=note)
    if check["asks"] < IDENTITY_ASKS:
        line = (
            "identity_check_address" if name_ok
            else "identity_check_name" if address_ok
            else "identity_check_again"
        )
        with tx(ctx.conn):
            ctx.conn.execute(
                "UPDATE identity_checks SET asks = asks + 1, asked_seq = ? "
                "WHERE call_id = ? AND customer_id = ?",
                (ctx.call["turn_seq"], check["call_id"], customer_id),
            )
        raise Refused("identity_check", ctx.tenant.line(line, thing=thing), need=note)
    phone = _on_file_phone(ctx, customer_id)
    with tx(ctx.conn):
        ctx.conn.execute(
            "UPDATE identity_checks SET status = 'mismatch', on_file_phone = ? "
            "WHERE call_id = ? AND customer_id = ?",
            (phone, check["call_id"], customer_id),
        )
        records.add_flag(
            ctx.conn,
            ctx.call["id"],
            "identity_mismatch",
            f"A caller from {ctx.call['caller_id']} asked to change the {thing} of customer "
            f"{customer_id} and didn't give the name and address on file. Nothing was changed.",
            ctx.now,
            "High",
        )
    raise Refused(
        "identity_mismatch",
        ctx.tenant.line("identity_mismatch"),
        need="no change can be made on this call; offer the callback, and flag_person if the "
        "caller wants it",
    )


def _settle_unanswered_check(ctx: Ctx) -> None:
    """A check whose question the caller answered without a match, when the agent moves on to a
    callback instead of trying the change again, is a mismatch: nothing was changed, and the
    callback goes to the number on file (eval run 20261104-200759, unknown-number-wrong-name)."""
    rows = ctx.conn.execute(
        "SELECT * FROM identity_checks WHERE call_id = ? AND status = 'asked'", (ctx.call["id"],)
    ).fetchall()
    for check in rows:
        if not records.caller_turn_after(ctx.conn, ctx.call["id"], check["asked_seq"]):
            continue
        phone = _on_file_phone(ctx, check["customer_id"])
        with tx(ctx.conn):
            ctx.conn.execute(
                "UPDATE identity_checks SET status = 'mismatch', on_file_phone = ? "
                "WHERE call_id = ? AND customer_id = ?",
                (phone, check["call_id"], check["customer_id"]),
            )
            records.add_flag(
                ctx.conn, ctx.call["id"], "identity_mismatch",
                f"A caller from {ctx.call['caller_id']} asked to change something for customer "
                f"{check['customer_id']} and didn't give the name and address on file. Nothing "
                "was changed.",
                ctx.now, "High",
            )


def _mismatch_callback(ctx: Ctx) -> sqlite3.Row | None:
    """An identity check this call failed whose callback to the number on file isn't set yet."""
    _settle_unanswered_check(ctx)
    thread = records.thread(ctx.conn, ctx.call["id"])
    return ctx.conn.execute(
        f"SELECT * FROM identity_checks WHERE status = 'mismatch' AND callback_id IS NULL "
        f"AND on_file_phone <> '' AND call_id IN ({','.join('?' * len(thread))}) "
        f"ORDER BY rowid DESC LIMIT 1",
        thread,
    ).fetchone()


def _note_unknown_number(
    ctx: Ctx, customer_id: int, job_id: int | None, what: str, change: str
) -> None:
    """After a change made from a number not on the account: the office's note, and a heads-up
    call to the number on file once this call ends (app/heads_up.py). `change` is what the
    heads-up says happened. A note that fails leaves the change standing."""
    try:
        ctx.fsm.add_note(
            _key(ctx, f"identity_note:{what}", {"customer": customer_id, "job": job_id}),
            customer_id,
            f"{NOTE_UNKNOWN_NUMBER} {what} Caller's number: {phone_display(ctx.call['caller_id'])}.",
            job_id,
        )
    except (FsmUnavailable, FsmRejected):
        pass
    heads_up.queue(
        ctx.conn, ctx.tenant, ctx.fsm, ctx.call, customer_id, job_id,
        _on_file_phone(ctx, customer_id), change, ctx.heads_up_allowlist, ctx.now,
    )


def _visit_spoken(ctx: Ctx, start: str, end: str) -> str:
    """ "Thursday between 8 and 12", for the heads-up sentence."""
    window = lookup.window_text(ctx.tenant, start, end)
    return f"{window['weekday']} between {window['label'].replace(' to ', ' and ')}"


# ---- two accounts on one number ---------------------------------------------------------------


def _distinct_names(names: dict[int, str]) -> dict[int, set[str]]:
    """Each customer's name words that no other one on the number has: "hector" and "marisol"
    for Hector and Marisol Ruiz."""
    sets = {cid: {w for w in _name_words(n) if len(w) > 1} for cid, n in names.items()}
    return {
        cid: own - set().union(*(o for other, o in sets.items() if other != cid))
        for cid, own in sets.items()
    }


# Words right before a name that make it the one the visit goes under: "this is Hector", "I'm
# Marisol", "put it under Marisol", "for Hector". Any other mention ("my husband Hector handles
# that", "Hector might have changed the thermostat") says nothing about whose visit it is.
SELF_BEFORE = (
    ("this", "is"), ("i", "am"), ("name", "is"), ("im",), ("its",), ("under",), ("for",),
    ("name",),
)
NAME_ALONE_MAX_WORDS = 3


def _account_said(ctx: Ctx, distinct: dict[int, set[str]]) -> int | None:
    """The account whose own name word the caller gave as theirs or as the visit's, in the
    latest caller line that does ("I'm Marisol", "under Hector", or the name said alone as an
    answer). None when no line does, and the server asks."""
    for row in reversed(_caller_lines(ctx)):
        tokens = _name_words(row["text"])
        for i, token in enumerate(tokens):
            found = [cid for cid, own in distinct.items() if token in own]
            if len(found) != 1:
                continue
            before = tuple(tokens[max(0, i - 2):i])
            alone = len(tokens) <= NAME_ALONE_MAX_WORDS
            if alone or any(before[-len(p):] == p for p in SELF_BEFORE if len(before) >= len(p)):
                return found[0]
    return None


def _number_accounts(ctx: Ctx) -> list[int]:
    """The residential customers the call's own number matched, when there are several."""
    found = [
        cid
        for cid, row in records.scoped_customers(ctx.conn, ctx.call["id"]).items()
        if row["matched_by"] == "caller_id"
        and row["customer_type"] not in ctx.tenant.rejected_customer_types
    ]
    return found if len(found) > 1 else []


def _account_named(ctx: Ctx, readback: dict) -> int | None:
    """When the number matched several accounts and the address doesn't settle which one (it's on
    file for both, or for neither), the one whose name the caller said: refused with the server's
    question until they say one. The address on file for exactly one of them books under that one,
    as before (issue #128, from #127)."""
    accounts = _number_accounts(ctx)
    if not accounts:
        return None
    try:
        here = [
            cid for cid in accounts
            if any(booking.same_address(loc, readback) for loc in ctx.fsm.locations(cid))
        ]
        if len(here) == 1:
            return here[0]
        names = {cid: ctx.fsm.customer(cid)["name"] for cid in accounts}
    except (FsmUnavailable, FsmRejected):
        return None
    said = _account_said(ctx, _distinct_names(names))
    if said is not None:
        return said
    # Asked twice with no one name in the answers: the first account, for a person to check.
    asked = ctx.conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE call_id = ? AND refused = 'account_which'",
        (ctx.call["id"],),
    ).fetchone()[0]
    if asked >= 2:
        return accounts[0]
    raise Refused(
        "account_which",
        ctx.tenant.line("account_which"),
        need="this question was said for you; when the caller answers with a name, call again",
    )


# ---- tools ---------------------------------------------------------------------------------


def find_customer(ctx: Ctx, args: FindCustomerIn) -> Result:
    _limit(ctx, "find_customer", records.count_tool(ctx.conn, ctx.call["id"], "find_customer"))
    needs_unit = False
    customers = []
    if args.phone:
        if len(phone_key(args.phone)) != 10:
            raise Refused("invalid_arguments", "Could I get that number again, all ten digits?")
        customers = ctx.fsm.customers_by_phone(args.phone)
        matched_by = "phone"
    # A number that matches nobody, with an address too: the address is looked up (eval run
    # 20261104-200759, a caller on a work phone whose visit was under her cell).
    if not customers and args.address:
        address = args.address
        locations = ctx.fsm.locations_by_address(address.street, address.zip, address.unit or None)
        # Only exact matches: without a unit, a building's other units are other people's records.
        exact = [
            loc
            for loc in locations
            if street_key(loc["address"]["street"]) == street_key(address.street)
            and unit_key(loc["address"]["unit"]) == unit_key(address.unit)
        ]
        needs_unit = not exact and bool(locations) and not address.unit
        customers = [
            ctx.fsm.customer(cid) for cid in dict.fromkeys(loc["customerId"] for loc in exact)
        ]
        matched_by = "address"
    customers = customers[:MAX_MATCHES]
    with tx(ctx.conn):
        for customer in customers:
            records.add_scope(
                ctx.conn, ctx.call["id"], customer["id"], matched_by, customer["type"]
            )
    bundles = [lookup.customer_bundle(ctx.fsm, ctx.tenant, ctx.conn, c, ctx.now) for c in customers]
    return ok(matches=[_unsaid_withheld(ctx, b) for b in bundles], needs_unit=needs_unit)


def _unsaid_withheld(ctx: Ctx, bundle: dict) -> dict:
    """A customer found from what a caller said, not from the number they called from: the name
    and numbers on file are left out until the caller has said the surname, so the agent can't
    read them out for a yes or tell a stranger whose account it is (issue #128)."""
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"]).get(bundle["customer_id"])
    if scoped is not None and scoped["matched_by"] == "caller_id":
        return bundle
    surname = _surname(bundle["name"])
    if surname and _has_word(records.caller_text(ctx.conn, ctx.call["id"]), surname):
        return bundle
    return {
        **bundle,
        "name": "",
        "phones": [],
        "withheld": "the name and numbers on file are not shown until the caller says them; "
        "a change asks for them first",
    }


def get_customer_history(ctx: Ctx, args: HistoryIn) -> Result:
    if args.customer_id not in records.scoped_customers(ctx.conn, ctx.call["id"]):
        raise Refused("not_owner", "I can only look at the records for this call.", gate="scope")
    _limit(
        ctx,
        "get_customer_history",
        records.count_tool(ctx.conn, ctx.call["id"], "get_customer_history"),
    )
    items = lookup.history(
        ctx.fsm, ctx.tenant, args.customer_id, args.what, ctx.now, args.months_back
    )
    return ok(customer_id=args.customer_id, what=args.what, items=items)


def check_service_area(ctx: Ctx, args: ServiceAreaIn) -> Result:
    _zip_known(ctx, args.zip)
    _zip_mismatch(ctx, args.zip)
    _out_of_area(ctx, args.zip)
    zone = ctx.tenant.service_area[args.zip]
    return ok(zip=args.zip, in_area=True, area=zone["name"])


def find_address(ctx: Ctx, args: FindAddressIn) -> Result:
    """The ZIP for a house and street the caller said. One place: its ZIP, checked against the
    service area, for the read-back to confirm. Several (the same street in two boroughs): which
    one. None: the caller repeats or spells the street, then a second miss asks for the ZIP. A
    lookup that is down or slow asks for the ZIP."""
    _limit(ctx, "find_address", records.count_tool(ctx.conn, ctx.call["id"], "find_address"))
    split = geocode.split_address(args.street)
    if not split:
        raise Refused(
            "invalid_arguments",
            "What's the house number there?",
            errors={"street": "needs the house number"},
        )
    house, street = split
    if not _address_said(ctx, house, street):
        raise Refused(
            "address_not_heard",
            "What's the street address there?",
            need="look up only a house number and street the caller said",
        )
    found = (
        ctx.geocoder.find(house, street, args.borough)
        if ctx.geocoder
        else geocode.Found("unavailable")
    )
    _record_lookup(ctx, house, street, args.borough, found)
    # A ZIP the caller already gave goes on when the lookup can't settle one, so nobody is asked
    # for a ZIP they just said; with several places, it picks one of them.
    said = _zip_said_with(ctx, house)
    if found.status == "several" and said in found.zips():
        found = geocode.Found("one", tuple(p for p in found.places if p.zip == said), found.source)
    if said in ctx.tenant.service_area and found.status in ("unavailable", "none"):
        return ok(
            zip=said,
            street=args.street,
            from_caller=True,
            in_area=True,
            area=ctx.tenant.service_area[said]["name"],
            note="The lookup couldn't confirm the street; the caller's ZIP stands.",
        )
    if found.status == "unavailable":
        raise Refused(
            "address_lookup_down", ctx.tenant.line("address_ask_zip"), need="ask for the ZIP"
        )
    if found.status == "none":
        thread = records.thread(ctx.conn, ctx.call["id"])
        misses = ctx.conn.execute(
            f"SELECT COUNT(*) FROM address_lookups WHERE status = 'none' "
            f"AND call_id IN ({','.join('?' * len(thread))})",
            thread,
        ).fetchone()[0]
        if misses >= 2:
            raise Refused(
                "address_not_found", ctx.tenant.line("address_ask_zip"), need="ask for the ZIP"
            )
        raise Refused(
            "address_not_found",
            ctx.tenant.line("address_not_found"),
            need="ask the caller to spell the street, then look it up again with spelled",
        )
    if found.status == "several":
        raise Refused(
            "address_several",
            ctx.tenant.line("address_which", places=_places_spoken(found.places)),
            need="ask which one, then look it up again with borough",
            places=[
                {"borough": p.borough, "neighborhood": p.area, "zip": p.zip} for p in found.places
            ],
        )
    place = found.places[0]
    # A ZIP the caller gave with this address that the lookup disagrees with is asked about.
    if said and said != place.zip and not _on_file_here(ctx, house, street, said):
        _ask_mismatch(ctx, house, street, place, said)
    _out_of_area(ctx, place.zip)
    zone = ctx.tenant.service_area[place.zip]
    return ok(
        street=place.spoken_street(),
        zip=place.zip,
        borough=place.borough,
        neighborhood=place.area,
        in_area=True,
        area=zone["name"],
    )


def find_slots(ctx: Ctx, args: FindSlotsIn) -> Result:
    job_type = _job_type(ctx, args.job_type)
    found = _priority(ctx, args.job_type, args.facts.inside_temp_f)
    facts, decided = found.facts, found.priority
    windows = _open_windows(ctx, job_type, decided.level)
    priority = {
        "level": decided.level,
        "reasons": decided.reasons,
        "requested": args.priority,
        "raised_after_wait": found.raised_after_wait,
    }
    data: dict = {"priority": priority}
    # A caller who can only be home part of the day gets only windows inside those hours. The
    # technician may come any time in a window, so a window that runs past them is never offered
    # as if it fit ("by 9" inside 8 to 12, eval round 2, seed 7).
    arrival_say = ""
    if args.arrive_after or args.arrive_before:
        after, before = _arrival_hours(args)
        fitting = [w for w in windows if _window_inside(ctx, w, after, before)]
        data["arrival"] = {
            "after": after.strftime("%H:%M") if after else None,
            "before": before.strftime("%H:%M") if before else None,
            "fits": bool(fitting),
        }
        if fitting:
            windows = fitting
        else:
            labels = _join([w.label() for w in ctx.tenant.windows if not w.after_hours], "and")
            arrival_say = ctx.tenant.line("arrival_no_fit", windows=labels) + " "
    # From here the caller hears a window or the wait; a fact they first give after it is flagged.
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT OR IGNORE INTO waits (call_id, turn_seq, at) VALUES (?, ?, ?)",
            (ctx.call["id"], ctx.call["turn_seq"], iso(ctx.now)),
        )

    cutoffs = ctx.tenant.after_hours_cutoffs
    if ctx.call["after_hours"] and args.job_type in (
        cutoffs["heating_job_types"] + cutoffs["cooling_job_types"]
    ):
        data["after_hours_outcome"] = prio.after_hours_outcome(
            facts, decided, cutoffs, args.facts.temp_falling_fast
        )
    # The caller hears once that their call went to the top (spec S-21a); below Urgent the agent
    # never names a priority.
    urgent_said = ""
    if decided.level == "Urgent":
        line = ctx.tenant.line("urgent_priority")
        if not records.agent_said(ctx.conn, ctx.call["id"], line):
            urgent_said = line + " "
    if decided.level == "Urgent" and windows:
        first = _describe(ctx, windows[0])
        data["first_urgent"] = first
        # No urgent window today: book the first one and flag a person to move it up (S-25).
        data["seek_earlier"] = first["day"] != "today"

    if args.day:
        on_day = [_describe(ctx, w) for w in windows if w["date"] == args.day.isoformat()]
        data["windows"] = on_day
        if not on_day:
            return ok(urgent_said + arrival_say + "Nothing's open that day.", **data)
        labels = _join([w["label"] for w in on_day], "or")
        return ok(
            f"{urgent_said}{arrival_say}{on_day[0]['day'].capitalize()} I have {labels}.", **data
        )

    by_day: dict[str, list[dict]] = {}
    for window in windows:
        by_day.setdefault(window["date"], []).append(_describe(ctx, window))
    preferred = {d.lower() for d in args.preferred_days}

    def rank(item):
        day, slots = item
        liked_day = bool(preferred) and (
            day in preferred or slots[0]["weekday"].lower() in preferred
        )
        liked_time = args.time_of_day in {s["window"] for s in slots}
        return (not liked_day, not liked_time, day)

    shape = []
    for day, slots in sorted(by_day.items(), key=rank)[:SHAPE_DAYS]:
        if args.time_of_day:
            slots.sort(key=lambda s: s["window"] != args.time_of_day)
        shape.append(
            {
                "date": day,
                "weekday": slots[0]["weekday"],
                "day": slots[0]["day"],
                "windows": [s["window"] for s in slots],
                "labels": [s["label"] for s in slots],
            }
        )
    data["days"] = shape
    if not shape:
        return ok(
            urgent_said + arrival_say + "I don't have anything open in the next two weeks.", **data
        )
    parts = []
    for d in shape:
        names = _join([w.replace("_", " ") for w in d["windows"]], "or")
        parts.append(f"this {names}" if d["day"] == "today" else f"{d['day']} {names}")
    return ok(f"{urgent_said}{arrival_say}I've got openings {_join(parts, 'and')}.", **data)


def _arrival_hours(args: FindSlotsIn) -> tuple[time | None, time | None]:
    """The hours the caller can be home, read as daytime: "8" to "2" is 8 AM to 2 PM."""
    try:
        after = clock_time(args.arrive_after) if args.arrive_after else None
        before = clock_time(args.arrive_before, after) if args.arrive_before else None
    except ValueError:
        raise Refused(
            "invalid_arguments",
            "What time do you need them there by?",
            errors={"arrive_after": "not a time", "arrive_before": "not a time"},
        ) from None
    if after and before and before <= after:
        raise Refused(
            "invalid_arguments",
            "What hours will someone be home?",
            errors={"arrive_before": "ends before it starts"},
        )
    return after, before


def _window_inside(ctx: Ctx, window: dict, after: time | None, before: time | None) -> bool:
    spec = ctx.tenant.window(window["window"])
    if spec is None or spec.after_hours:
        return False
    return (after is None or spec.start >= after) and (before is None or spec.end <= before)


def _resolve_phone(ctx: Ctx, value: str) -> str:
    value = value.strip()
    if value.lower().replace(" ", "_") == "caller_id":
        if len(ctx.call["caller_key"]) != 10:
            raise Refused(
                "invalid_phone", "The number didn't come through. What's the best number?"
            )
        return ctx.call["caller_key"]
    digits = phone_key(value)
    if len(digits) == 4 and len(value.replace("-", "").strip()) <= 6:
        on_file = set()
        for customer_id in records.scoped_customers(ctx.conn, ctx.call["id"]):
            for contact in ctx.fsm.contacts(customer_id):
                if contact["type"] != "Email" and phone_key(contact["value"]).endswith(digits):
                    on_file.add(phone_key(contact["value"]))
        if len(on_file) == 1:
            return on_file.pop()
        raise Refused(
            "invalid_phone",
            f"I don't see just one number ending in {digits}. What's the full number?",
            matches=len(on_file),
        )
    if len(digits) != 10:
        raise Refused("invalid_phone", "Could I get that number again, all ten digits?")
    return digits


def _readback_key(value: dict) -> tuple:
    return (
        street_key(value["street"]),
        unit_key(value["unit"]),
        value["zip"],
        value["phone"],
        value.get("phone_for", ""),
    )


FOR_FILLER = {"my", "the", "our", "her", "his", "their", "is", "at", "of", "a", "an", "and"}


def _phone_for(ctx: Ctx, value: str) -> str:
    """Whom the read-back's number reaches, when it isn't the caller ("my daughter Ana"), in the
    caller's own words: each word of it must be in a caller line, so the model can't name a
    contact the caller never mentioned."""
    value = " ".join(value.split())
    if not value:
        return ""
    caller = words(records.caller_text(ctx.conn, ctx.call["id"]))
    missing = [w for w in words(value).split() if w not in FOR_FILLER and f" {w} " not in caller]
    if missing:
        raise Refused(
            "invalid_arguments",
            "",
            errors={"phone_for": "only who the caller said the number reaches, in their words"},
        )
    return value


def read_back(ctx: Ctx, args: ReadBackIn) -> Result:
    """Two steps. Without confirmed, returns the exact sentence to read and records that it was
    asked. With confirmed, accepts only if that same sentence was returned on this call and a caller
    line followed it (spec S-26)."""
    phone = _resolve_phone(ctx, args.phone)
    _zip_known(ctx, args.address.zip)
    _zip_mismatch(ctx, args.address.zip, args.address.street, args.address.city)
    address = args.address.model_dump()
    place = address["street"] + (f", unit {address['unit']}" if address["unit"] else "")
    city = f", {address['city']}" if address["city"] else ""
    # The sentence names the address as a home, so every booking has the caller confirm it's
    # residential (assignment; spec S-07). A caller who corrects it is caught by the turn check.
    # The ZIP and the number are written out digit by digit, as the caller should hear them.
    spoken_address = f"{place}{city}, {digits_spoken(address['zip'])}"
    phone_for = _phone_for(ctx, args.phone_for)
    if phone_for:
        sentence = ctx.tenant.line(
            "read_back_for", address=spoken_address, who=phone_for, phone=phone_said(phone)
        )
    else:
        sentence = ctx.tenant.line("read_back", address=spoken_address, phone=phone_said(phone))
    asked = records.readback(ctx.call)
    current = {**address, "phone": phone, "phone_for": phone_for, "sentence": sentence}
    same = asked is not None and _readback_key(asked) == _readback_key(current)
    # Details the caller already confirmed are never read back again on the call (eval rounds 3
    # and 4). Only a change to them reads a new sentence.
    if same and ctx.call["readback_confirmed_at"] and not args.confirmed:
        raise Refused(
            "read_back_confirmed",
            "",
            note="The caller already confirmed this address and number. Don't read them again.",
            address=address,
            phone=phone,
        )
    if (
        args.confirmed
        and same
        and records.caller_turn_after(ctx.conn, ctx.call["id"], ctx.call["readback_asked_seq"])
    ):
        with tx(ctx.conn):
            records.update_call(ctx.conn, ctx.call["id"], readback_confirmed_at=iso(ctx.now))
        # A caller who moved: the new address goes through record_move, not a new account.
        if _mover_pending(ctx, address):
            return ok(
                confirmed=True,
                address=address,
                phone=phone,
                need="the caller moved: call record_move now, before anything else",
            )
        # A number on several accounts: which name the visit is for, asked the same way.
        try:
            chosen = _account_named(ctx, address)
        except Refused as which:
            return ok(
                which.result.say,
                confirmed=True,
                address=address,
                phone=phone,
                account_which=True,
                need="this question was said for you; book_appointment once the caller names one",
            )
        # Whose account a new address goes on is asked here, as the confirmation's own sentence,
        # not by a refused booking: the reply that books then carries its short line and the
        # confirmation together (eval run 20261104-183233, tune-up-known-caller).
        try:
            choose = _new_address_customer(ctx, address, chosen) is not None
        except (FsmUnavailable, FsmRejected):
            choose = False
        if choose:
            return ok(
                ctx.tenant.line("account_choice"),
                confirmed=True,
                address=address,
                phone=phone,
                account_choice=True,
                need="this question was said for you; when the caller answers, book_appointment "
                "with new_address_account own (and details.name for that address) or caller",
            )
        return ok(confirmed=True, address=address, phone=phone)
    parts = None
    if not (args.confirmed and same):
        with tx(ctx.conn):
            records.update_call(
                ctx.conn,
                ctx.call["id"],
                readback_json=json.dumps(current),
                readback_asked_seq=ctx.call["turn_seq"],
                readback_confirmed_at=None,
            )
            if not args.confirmed:
                # The caller asked for the address slower: the worker speaks these parts as given.
                parts = pace.read_back_parts(ctx.conn, ctx.call["id"], sentence, spoken_address)
    if args.confirmed:
        raise Refused(
            "gate_read_back",
            sentence,
            need="read the sentence to the caller and let them answer first",
            sentence=sentence,
        )
    if parts:
        return ok(sentence, confirmed=False, sentence=sentence, parts=parts)
    return ok(sentence, confirmed=False, sentence=sentence)


def book_appointment(ctx: Ctx, args: BookIn) -> Result:
    override = _safety_gate(ctx)
    _commercial(ctx)
    existing = [
        r
        for r in ctx.conn.execute(
            f"SELECT * FROM bookings WHERE kind = 'book' AND status IN ('sending', 'booked', 'queued') "
            f"AND call_id IN ({','.join('?' * len(records.thread(ctx.conn, ctx.call['id'])))})",
            records.thread(ctx.conn, ctx.call["id"]),
        )
    ]
    _limit(ctx, "book", len(existing))
    if not ctx.call["readback_confirmed_at"]:
        raise Refused(
            "gate_read_back",
            "Before I book it, let me read back the address and number.",
            need="read_back",
        )
    readback = records.readback(ctx.call)
    try:
        # A lookup that came back after the read-back can still stop a booking at a ZIP the
        # caller hasn't settled; the address is then read back again.
        _zip_mismatch(ctx, readback["zip"], readback["street"], readback["city"])
    except Refused:
        with tx(ctx.conn):
            records.update_call(ctx.conn, ctx.call["id"], readback_confirmed_at=None)
        raise
    # A caller who said they moved books the new address only once the move is recorded, so the
    # old address stops getting visits (issue #128).
    if _mover_pending(ctx, readback):
        raise Refused(
            "move_first",
            "",
            need="the caller moved: call record_move first, then book if they still need a visit",
        )
    _out_of_area(ctx, readback["zip"])
    chosen = _account_named(ctx, readback)
    if not args.separate_problem:
        _open_job_here(ctx, readback)
    account, contact_name = _new_address_account(ctx, readback, args, chosen)
    job_type = _job_type(ctx, args.job_type)
    details = args.details.model_dump()
    found = _priority(ctx, args.job_type, details["inside_temp_f"])
    facts, decided = found.facts, found.priority
    if not override:
        _on_call_first(ctx, facts, decided)
    window_start, window_end = _slot_bounds(ctx, args.slot, decided.level)
    if _window_over(ctx, args.slot.window, window_start, window_end):
        ctx.cache.invalidate()
        raise Refused(
            "slot_taken",
            ctx.tenant.line("slot_taken"),
            alternatives=_alternatives(ctx, job_type, decided.level),
        )
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    if not scoped and not details["name"]:
        raise Refused(
            "invalid_arguments", "Can I get a name for the booking?", errors={"name": "needed"}
        )

    thread = records.thread(ctx.conn, ctx.call["id"])
    pending_notes = [
        r["text"]
        for r in ctx.conn.execute(
            f"SELECT text FROM notes WHERE attach_to = 'job' AND fsm_note_id IS NULL "
            f"AND call_id IN ({','.join('?' * len(thread))}) ORDER BY id",
            thread,
        )
    ]
    check_approval = _approval_unclear(ctx, details["approver"])
    if check_approval:
        pending_notes.append("Confirm who can approve the work before dispatch.")
    request = {
        "call_id": ctx.call["id"],
        "job_type": args.job_type,
        "details": details,
        "address": {k: readback[k] for k in ("street", "unit", "city", "zip")},
        "phone": readback["phone"],
        "scoped_customer_ids": [chosen] if chosen else [
            cid
            for cid, row in scoped.items()
            if row["customer_type"] not in ctx.tenant.rejected_customer_types
        ]
        or list(scoped),
        # Whom the number reaches when it isn't the caller, and the account's own number beside it.
        "contact_for": readback.get("phone_for", ""),
        "account_phone": _account_phone(ctx, chosen) if readback.get("phone_for") else "",
        "priority": decided.level,
        "priority_reasons": decided.reasons,
        "priority_rules": decided.rules,
        "raised_after_wait": found.raised_after_wait,
        "requested_priority": args.priority,
        "notes": pending_notes,
        "account": account,
        "contact_name": contact_name,
        "window_start": iso(window_start),
        "window_end": iso(window_end),
    }
    key = _attempt_key(ctx, "book", args.model_dump(mode="json"))
    seek_earlier = decided.level == "Urgent" and args.slot.date > ctx.tenant.local(ctx.now).date()
    with tx(ctx.conn):
        booking_id = ctx.conn.execute(
            """INSERT INTO bookings (call_id, kind, status, fsm_key, request_json, window_start,
                   window_end, job_type, priority, priority_reasons, safety_override, seek_earlier,
                   turn_seq, created_at, updated_at)
               VALUES (?, 'book', 'sending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ctx.call["id"],
                key,
                json.dumps(request),
                request["window_start"],
                request["window_end"],
                args.job_type,
                decided.level,
                "; ".join(decided.reasons),
                int(override),
                int(seek_earlier),
                ctx.call["turn_seq"],
                iso(ctx.now),
                iso(ctx.now),
            ),
        ).lastrowid

    try:
        sent = booking.send(ctx.fsm, ctx.tenant, request, key)
    except FsmUnavailable as exc:
        with tx(ctx.conn):
            booking.mark_attempt(ctx.conn, booking_id, "queued", str(exc), ctx.now)
        callback_id, due = _callback(
            ctx, "fsm_down_minutes", "Booking queued while the FSM was down"
        )
        result = Result(False, _fsm_down_say(ctx, callback_id, due), refused="fsm_unavailable")
        result.data = {"queued": True, "booking_id": None, "callback_at": iso(due)}
        return result
    except FsmRejected as exc:
        with tx(ctx.conn):
            booking.mark_attempt(
                ctx.conn, booking_id, "failed", f"{exc.code}: {exc.detail}", ctx.now
            )
        if exc.code == "slot_taken":
            ctx.cache.invalidate()
            alternatives = _alternatives(ctx, job_type, decided.level)
            raise Refused(
                "slot_taken", ctx.tenant.line("slot_taken"), alternatives=alternatives
            ) from None
        if exc.code == "customer_type_not_supported":  # raised by booking.send, not the FSM
            raise Refused("customer_type", ctx.tenant.line("commercial")) from None
        if exc.code == "location_superseded":  # raised by booking.send: the customer moved out
            raise Refused(
                "location_superseded",
                ctx.tenant.line("moved_from_address"),
                need="this address is one the customer moved out of; ask where the visit is",
            ) from None
        callback_id, due = _callback(
            ctx, "fsm_down_minutes", f"Booking refused by the FSM: {exc.code}"
        )
        with tx(ctx.conn):
            records.add_flag(ctx.conn, ctx.call["id"], "booking_failed", str(exc), ctx.now, "High")
        raise Refused("fsm_rejected", _fsm_down_say(ctx, callback_id, due)) from None

    ctx.cache.invalidate()
    returning = _recent_job_here(ctx, readback, job_type)
    with tx(ctx.conn):
        booking.mark_booked(ctx.conn, booking_id, sent, ctx.now)
        # The same problem back soon after a visit: a person reviews the priority and whether it's
        # covered, so the agent never has to answer that (journey 7 step 3, eval round 2, seed 10).
        if returning:
            records.add_flag(
                ctx.conn,
                ctx.call["id"],
                "person",
                f"Return visit: job {sent['job_id']} is at the address of job {returning['id']}, "
                f"finished {returning['completed']}. Review the priority and whether it's covered.",
                ctx.now,
                "High",
            )
        if seek_earlier:
            records.add_flag(
                ctx.conn,
                ctx.call["id"],
                "seek_earlier",
                f"Urgent job {sent['job_id']} booked for {args.slot.date.strftime('%a %b %-d')}; "
                "look for an earlier slot",
                ctx.now,
                "Urgent",
            )
    when = _describe(
        ctx,
        {
            "date": args.slot.date.isoformat(),
            "window": args.slot.window,
            "after_hours": False,
            "start": request["window_start"],
        },
    )
    say = (
        f"You're all set for {when['day_and_date']}, with the technician arriving between "
        f"{when['label'].replace(' to ', ' and ')}."
    )
    # A visit booked on a safety call goes ahead only once the problem is shut off (eval round 2).
    if override and ctx.tenant.safety.get("booking_note"):
        say += " " + " ".join(ctx.tenant.safety["booking_note"].split())
    # Approval unclear: a person calls before dispatch to confirm who can approve (eval round 2).
    approval_callback_at = None
    if check_approval:
        due = approval_call_time(ctx.tenant, window_start, ctx.now)
        # No calling time left before the window: a person holds the job until approval is settled.
        late = due >= window_start
        reason = f"Confirm who can approve job {sent['job_id']} before dispatch"
        if late:
            reason += "; the call can't happen before the window opens, so hold the technician"
        with tx(ctx.conn):
            records.add_flag(ctx.conn, ctx.call["id"], "approval_check", reason, ctx.now, "High")
            callback_id = records.add_callback(
                ctx.conn, ctx.tenant, ctx.call, due, reason, ctx.now
            )
        if callback_id:
            approval_callback_at = iso(due)
            spoken = ctx.tenant.line("callback_by", time=ctx.tenant.time_spoken(due, ctx.now))
            line = "approval_callback_late" if late else "approval_callback"
            say += " " + ctx.tenant.line(line, when=spoken)
    if returning:
        say += " " + ctx.tenant.line("return_visit_review")
    if seek_earlier:
        say += " " + ctx.tenant.line("seek_earlier_note")
    # The voice layer speaks this as the whole confirmation, with no model step after it, so it
    # ends by asking "Anything else?" itself (spec S-30; eval round 4, the booking turn's delay).
    say += " " + ctx.tenant.line("after_confirmation")
    result = ok(
        say,
        booking_id=sent["job_id"],
        appointment_id=sent["appointment_id"],
        window=when,
        priority={
            "level": decided.level,
            "reasons": decided.reasons,
            "raised_after_wait": found.raised_after_wait,
        },
        seek_earlier=seek_earlier,
        safety_override=override,
        approval_callback_at=approval_callback_at,
        return_visit_review=bool(returning),
    )
    if override:
        result.gate = "safety_override"
    return result


def _on_call_first(ctx: Ctx, facts: prio.Facts, decided: prio.Priority) -> None:
    """After hours, a no-heat or no-cooling call that needs the on-call technician tonight (journey
    11) books only once an urgent person flag has alerted them. The morning visit is then the
    fallback if the technician can't come."""
    cutoffs = ctx.tenant.after_hours_cutoffs
    if not ctx.call["after_hours"] or facts.job_type not in (
        cutoffs["heating_job_types"] + cutoffs["cooling_job_types"]
    ):
        return
    if prio.after_hours_outcome(facts, decided, cutoffs, False) != "tonight_if_on_call_agrees":
        return
    thread = records.thread(ctx.conn, ctx.call["id"])
    alerted = ctx.conn.execute(
        f"SELECT 1 FROM flags WHERE kind = 'person' AND priority = 'Urgent' "
        f"AND call_id IN ({','.join('?' * len(thread))})",
        thread,
    ).fetchone()
    if not alerted:
        raise Refused(
            "on_call_first",
            "Let me get this to our on-call technician first.",
            need="flag_person with priority Urgent",
        )


def needs_on_call_tonight(ctx: Ctx) -> bool:
    """After hours, whether the server's own priority sends this call to the on-call technician
    tonight (journey 11), as `_on_call_first` decides for a booking: the last heating or cooling job
    the agent looked up or booked, scored again from every caller line. The priority the model
    puts on a flag is never read."""
    cutoffs = ctx.tenant.after_hours_cutoffs
    if not ctx.call["after_hours"]:
        return False
    job_types = cutoffs["heating_job_types"] + cutoffs["cooling_job_types"]
    rows = ctx.conn.execute(
        "SELECT tool, args_json FROM tool_calls WHERE call_id = ? "
        "AND tool IN ('find_slots', 'book_appointment') ORDER BY id DESC",
        (ctx.call["id"],),
    )
    for row in rows:
        args = json.loads(row["args_json"])
        if args.get("job_type") not in job_types:
            continue
        heard = args.get("facts") or args.get("details") or {}
        found = _priority(ctx, args["job_type"], heard.get("inside_temp_f"))
        facts, decided = found.facts, found.priority
        falling = bool(heard.get("temp_falling_fast"))
        return prio.after_hours_outcome(facts, decided, cutoffs, falling) == (
            "tonight_if_on_call_agrees"
        )
    return False


# How a caller says the problem is back, so a booking of another job type still counts as a return
# visit ("you fixed the AC last week and now nothing works again").
SAME_PROBLEM_PHRASES = (
    "again",
    "came back",
    "same problem",
    "same issue",
    "still not working",
    "still broken",
    "you were here",
    "you were just here",
    "you just fixed",
    "since you fixed",
)


def _recent_job_here(ctx: Ctx, readback: dict, job_type: dict) -> dict | None:
    """A job finished at this address within the tenant's return-visit window, for a customer
    matched on this call, when the new booking is the same problem: the same job type, or the
    caller says it's back ("it's out again"). A tune-up last week and an unrelated breakdown now is
    not a return visit (PR #75 review). A lookup that fails finds none; the booking stands."""
    days = ctx.tenant.repeat.get("return_visit_days")
    if not days:
        return None
    caller = words(records.caller_text(ctx.conn, ctx.call["id"]))
    said_same = any(has_phrase(caller, p) for p in SAME_PROBLEM_PHRASES)
    since = (ctx.now - timedelta(days=days)).isoformat()
    try:
        for customer_id in records.scoped_customers(ctx.conn, ctx.call["id"]):
            here = {
                loc["id"]
                for loc in ctx.fsm.locations(customer_id)
                if booking.same_address(loc, readback)
            }
            done = [
                j
                for j in ctx.fsm.jobs(customer_id, ["Completed"])
                if j["locationId"] in here
                and (j.get("completedOn") or "") >= since[:19]
                and (said_same or j["jobTypeId"] == job_type["id"])
            ]
            if done:
                last = max(done, key=lambda j: j["completedOn"])
                return {"id": last["id"], "completed": last["completedOn"][:10]}
    except (FsmUnavailable, FsmRejected):
        return None
    return None


def _open_job_here(ctx: Ctx, readback: dict) -> None:
    """A caller with an open visit at this address wants it moved, not a second one, unless the
    agent says it is a separate problem (eval round 1, reschedule-own-booking). Jobs booked on this
    call don't count, and a lookup that fails leaves the booking to go ahead."""
    thread = records.thread(ctx.conn, ctx.call["id"])
    booked_here = {
        r["fsm_job_id"]
        for r in ctx.conn.execute(
            f"SELECT fsm_job_id FROM bookings WHERE fsm_job_id IS NOT NULL "
            f"AND call_id IN ({','.join('?' * len(thread))})",
            thread,
        )
    }
    try:
        for customer_id in records.scoped_customers(ctx.conn, ctx.call["id"]):
            here = {
                loc["id"]
                for loc in ctx.fsm.locations(customer_id)
                if booking.same_address(loc, readback)
            }
            for job in ctx.fsm.jobs(customer_id, lookup.OPEN_JOB_STATUSES):
                if job["locationId"] in here and job["id"] not in booked_here:
                    raise Refused(
                        "open_job",
                        "You already have a visit booked there. Should I move that one instead?",
                        job_id=job["id"],
                        use="reschedule_appointment with this job_id as booking_id, or "
                        "book_appointment with separate_problem true if the caller confirms a "
                        "different problem",
                    )
    except (FsmUnavailable, FsmRejected):
        return


def _account_phone(ctx: Ctx, chosen: int | None) -> str:
    """The account's own number, for a booking whose number reaches someone else: the one on file
    for the one customer matched, otherwise the number the call came from."""
    scoped = [chosen] if chosen else list(records.scoped_customers(ctx.conn, ctx.call["id"]))
    if len(scoped) == 1:
        on_file = _on_file_phone(ctx, scoped[0])
        if on_file:
            return on_file
    return ctx.call["caller_key"] if len(ctx.call["caller_key"]) == 10 else ""


def _new_address_customer(ctx: Ctx, readback: dict, chosen: int | None = None) -> int | None:
    """The one customer matched on this call (or the one the caller named, on a number with
    several accounts) when the address is on file nowhere, so whose account it goes on has to be
    asked; None otherwise. FSM errors are raised for the caller to handle."""
    scoped = [
        (cid, row)
        for cid, row in records.scoped_customers(ctx.conn, ctx.call["id"]).items()
        if row["customer_type"] not in ctx.tenant.rejected_customer_types
        and (chosen is None or cid == chosen)
    ]
    if len(scoped) != 1:
        return None
    customer_id = scoped[0][0]
    if any(booking.same_address(loc, readback) for loc in ctx.fsm.locations(customer_id)):
        return None
    on_file = ctx.fsm.locations_by_address(
        readback["street"], readback["zip"], readback["unit"] or None
    )
    if any(booking.same_address(loc, readback) for loc in on_file):
        return None
    return customer_id


def _new_address_account(
    ctx: Ctx, readback: dict, args: BookIn, chosen: int | None = None
) -> tuple[str | None, str | None]:
    """A caller on file booking an address that isn't on file anywhere is asked whose account it
    goes on before it books: its own account with the caller as a contact (a parent's house, the
    usual case), or the caller's (eval round 2, seed 5). Returns the choice and, for its own
    account, the caller's name for the contact. A lookup that fails leaves the booking to go ahead
    under the caller, as before."""
    try:
        customer_id = _new_address_customer(ctx, readback, chosen)
        if customer_id is None:
            return None, None
        caller = ctx.fsm.customer(customer_id)
    except (FsmUnavailable, FsmRejected):
        return args.new_address_account, None
    if args.new_address_account is None:
        raise Refused(
            "account_choice",
            ctx.tenant.line("account_choice"),
            need="ask, then book_appointment with new_address_account own (and details.name for "
            "that address) or caller",
        )
    if args.new_address_account == "caller":
        return "caller", None
    if not args.details.name.strip():
        raise Refused(
            "invalid_arguments",
            ctx.tenant.line("account_name"),
            errors={"name": "the account holder for that address"},
        )
    return "own", caller["name"]


def _move_first(ctx: Ctx) -> None:
    """A caller who said they moved has their old visit moved or cancelled by record_move, which
    also notes the move and checks the new address (eval run 20261104-200759: a mover's visit was
    cancelled with no note and no decline). Asked once; a caller who won't give the new address
    can still cancel."""
    if not _mover_pending(ctx):
        return
    asked = ctx.conn.execute(
        "SELECT 1 FROM tool_calls WHERE call_id = ? AND refused = 'move_first'", (ctx.call["id"],)
    ).fetchone()
    if not asked:
        raise Refused(
            "move_first",
            "",
            need="the caller moved: ask for the new address, read it back, then call record_move; "
            "it moves this visit there or, outside the area, cancels it",
        )


def _owned_job(ctx: Ctx, job_id: int) -> tuple[dict, bool]:
    """The job, if it was booked on this thread or belongs to a customer matched on this call. A
    customer the call's number isn't on must first pass the identity check (_identity_gate).
    Returns the job and whether the check was needed, so the change is noted for the office."""
    thread = records.thread(ctx.conn, ctx.call["id"])
    booked_here = ctx.conn.execute(
        f"SELECT 1 FROM bookings WHERE fsm_job_id = ? AND call_id IN ({','.join('?' * len(thread))})",
        (job_id, *thread),
    ).fetchone()
    try:
        job = ctx.fsm.job(job_id)
    except FsmRejected:
        job = None
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    if not job or not (booked_here or job["customerId"] in scoped):
        raise Refused(
            "not_owner", "I can only change a visit that's booked for you.", gate="ownership"
        )
    if booked_here or scoped[job["customerId"]]["matched_by"] == "caller_id":
        return job, False
    location = ctx.fsm.location(job["locationId"])
    checked = _identity_gate(ctx, job["customerId"], [location["address"]], "visit")
    return job, checked


def _record_action(ctx: Ctx, kind: str, key: str, job: dict, args: dict, reason: str) -> int:
    with tx(ctx.conn):
        return ctx.conn.execute(
            """INSERT INTO bookings (call_id, kind, status, fsm_key, request_json, fsm_job_id,
                   customer_id, job_type, reason, turn_seq, created_at, updated_at)
               VALUES (?, ?, 'sending', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ctx.call["id"],
                kind,
                key,
                json.dumps(args, default=str),
                job["id"],
                job["customerId"],
                ctx.tenant.job_type_codes.get(job["jobTypeId"]),
                reason,
                ctx.call["turn_seq"],
                iso(ctx.now),
                iso(ctx.now),
            ),
        ).lastrowid


def _action_failed(ctx: Ctx, row_id: int, exc: Exception, what: str) -> None:
    with tx(ctx.conn):
        booking.mark_attempt(ctx.conn, row_id, "failed", str(exc), ctx.now)
        records.add_flag(ctx.conn, ctx.call["id"], f"{what}_failed", str(exc), ctx.now, "High")
    callback_id, due = _callback(ctx, "fsm_down_minutes", f"{what.capitalize()} could not be done")
    raise Refused(
        "fsm_unavailable" if isinstance(exc, FsmUnavailable) else "fsm_rejected",
        _fsm_down_say(ctx, callback_id, due),
    )


def reschedule_appointment(ctx: Ctx, args: RescheduleIn) -> Result:
    _safety_gate(ctx)
    _limit(ctx, "reschedule", records.count_actions(ctx.conn, ctx.call["id"], "reschedule"))
    _move_first(ctx)
    job, checked = _owned_job(ctx, args.booking_id)
    appointments = [a for a in ctx.fsm.appointments(job["id"]) if a["status"] == "Scheduled"]
    if job["jobStatus"] != "Scheduled" or not appointments:
        raise Refused(
            "invalid_state",
            "That visit is already underway, so let me get someone to help with it.",
        )
    # A job still at an address the customer moved out of is never sent a technician again.
    try:
        booking.check_live(ctx.fsm, job["locationId"])
    except FsmRejected:
        _flag_and_refuse(
            ctx, "location_superseded", ctx.tenant.line("location_superseded"), "person",
            f"Job {job['id']} is at a location the customer moved out of; move it before it is "
            "rescheduled.",
        )
    window_start, window_end = _slot_bounds(ctx, args.slot, job["priority"])
    # Checked before the move is recorded, so a window that went by doesn't use up the one move.
    if _window_over(ctx, args.slot.window, window_start, window_end):
        ctx.cache.invalidate()
        raise Refused(
            "slot_taken",
            ctx.tenant.line("slot_taken"),
            alternatives=_job_alternatives(ctx, job),
        )
    key = _attempt_key(ctx, "reschedule", args.model_dump(mode="json"))
    row_id = _record_action(ctx, "reschedule", key, job, args.model_dump(mode="json"), args.reason)
    try:
        technician = capacity_rules.pick_technician(
            ctx.tenant,
            ctx.fsm.capacity(window_start, window_end, job["businessUnitId"], job["jobTypeId"]),
            window_start,
            window_end,
            job["priority"],
        )
        if technician is None:
            raise FsmRejected(409, "slot_taken", "No technician may take that window.")
        ctx.fsm.reschedule(key, appointments[-1]["id"], window_start, window_end, technician)
    except FsmRejected as exc:
        if exc.code == "slot_taken":
            with tx(ctx.conn):
                booking.mark_attempt(ctx.conn, row_id, "failed", exc.code, ctx.now)
            ctx.cache.invalidate()
            raise Refused(
                "slot_taken",
                ctx.tenant.line("slot_taken"),
                alternatives=_job_alternatives(ctx, job),
            ) from None
        _action_failed(ctx, row_id, exc, "reschedule")
    except FsmUnavailable as exc:
        _action_failed(ctx, row_id, exc, "reschedule")
    ctx.cache.invalidate()
    with tx(ctx.conn):
        ctx.conn.execute(
            "UPDATE bookings SET status = 'done', window_start = ?, window_end = ?, updated_at = ? "
            "WHERE id = ?",
            (iso(window_start), iso(window_end), iso(ctx.now), row_id),
        )
    if checked:
        _note_unknown_number(
            ctx, job["customerId"], job["id"], "Visit moved by phone.",
            "Your visit was moved to "
            + _visit_spoken(ctx, iso(window_start), iso(window_end)),
        )
    when = _describe(
        ctx,
        {
            "date": args.slot.date.isoformat(),
            "window": args.slot.window,
            "after_hours": False,
            "start": iso(window_start),
        },
    )
    between = when["label"].replace(" to ", " and ")
    return ok(
        f"Done, I've moved it to {when['day_and_date']}, between {between}. "
        + ctx.tenant.line("after_confirmation"),
        booking_id=job["id"],
        window=when,
    )


def cancel_appointment(ctx: Ctx, args: CancelIn) -> Result:
    """Cancels, and returns two windows the agent can offer instead: a cancel is often a rebooking
    (journey 7)."""
    _safety_gate(ctx)
    _limit(ctx, "cancel", records.count_actions(ctx.conn, ctx.call["id"], "cancel"))
    _move_first(ctx)
    job, checked = _owned_job(ctx, args.booking_id)
    # A dispatched technician is already on the way; a person handles that.
    if job["jobStatus"] != "Scheduled":
        raise Refused(
            "invalid_state",
            "That visit can't be cancelled from here, so let me get someone to help.",
        )
    key = _attempt_key(ctx, "cancel", args.model_dump(mode="json"))
    row_id = _record_action(ctx, "cancel", key, job, args.model_dump(mode="json"), args.reason)
    try:
        ctx.fsm.cancel_job(
            key, job["id"], ctx.tenant.fsm["cancel_reason_id"], f"Cancelled by phone: {args.reason}"
        )
    except (FsmUnavailable, FsmRejected) as exc:
        _action_failed(ctx, row_id, exc, "cancel")
    ctx.cache.invalidate()
    with tx(ctx.conn):
        ctx.conn.execute(
            "UPDATE bookings SET status = 'done', updated_at = ? WHERE id = ?",
            (iso(ctx.now), row_id),
        )
    if checked:
        try:
            was = ctx.fsm.appointments(job["id"])[-1]
            change = "Your visit on " + _visit_spoken(
                ctx, was["arrivalWindowStart"], was["arrivalWindowEnd"]
            ) + " was cancelled"
        except (FsmUnavailable, FsmRejected, IndexError):
            change = "Your upcoming visit was cancelled"
        _note_unknown_number(ctx, job["customerId"], job["id"], "Visit cancelled by phone.", change)
    job_type = ctx.tenant.job_types.get(ctx.tenant.job_type_codes.get(job["jobTypeId"], ""))
    alternatives = _alternatives(ctx, job_type, job["priority"]) if job_type else []
    say = "That visit is cancelled."
    if alternatives:
        # Spoken by the voice layer as it is, so the offer of other times is part of it.
        times = _join([f"{a['day']} {a['label']}" for a in alternatives], "or")
        say += f" If you'd like another time, I have {times}."
    else:
        say += " " + ctx.tenant.line("after_confirmation")
    return ok(
        say,
        booking_id=job["id"],
        reason=args.reason,
        alternatives=alternatives,
    )


# Caller words that say they moved house, not that something else moved ("I moved the
# thermostat"). "Move my appointment" is a reschedule and says none of these.
MOVED_PHRASES = tuple(
    f"{who} {what}"
    for who in ("i", "we", "ive", "weve", "i have", "we have", "i just", "we just", "ive just",
                "weve just", "i recently", "we recently")
    for what in ("moved to", "moved into", "moved out", "moved house", "moved apartments")
) + (
    "i just moved", "we just moved", "ive just moved", "weve just moved", "i recently moved",
    "we recently moved", "i have moved", "we have moved", "ive moved", "weve moved",
    "my new address", "our new address", "i have a new address", "we have a new address",
    "change my address", "update my address", "my address changed", "our address changed",
    "i dont live there", "we dont live there", "i no longer live", "we no longer live",
    "im not living there", "were not living there",
)


def _said_moved(ctx: Ctx) -> bool:
    caller = words(records.caller_text(ctx.conn, ctx.call["id"]))
    return any(has_phrase(caller, phrase) for phrase in MOVED_PHRASES)


def _moved_on_call(ctx: Ctx) -> bool:
    thread = records.thread(ctx.conn, ctx.call["id"])
    return bool(
        ctx.conn.execute(
            f"SELECT 1 FROM address_moves WHERE call_id IN ({','.join('?' * len(thread))})",
            thread,
        ).fetchone()
    )


def _place(address: dict) -> str:
    return address["street"] + (f", unit {address['unit']}" if address.get("unit") else "")


def record_move(ctx: Ctx, args: MoveIn) -> Result:
    """A customer who moved (issue #128). The new address is the one the caller confirmed in the
    read-back. In the service area, it becomes a new location of theirs (ServiceTitan keeps one
    location per address, so the old one is made inactive rather than overwritten, which keeps
    its history and equipment), and every open visit at the old address moves to it. Out of the
    area, nothing changes but a note, and every open visit at the old address is cancelled, so
    nobody is sent there. Either way the caller is told what happened in the server's sentence."""
    _limit(ctx, "move", ctx.conn.execute(
        "SELECT COUNT(*) FROM address_moves WHERE call_id = ?", (ctx.call["id"],)
    ).fetchone()[0])
    if not _said_moved(ctx):
        raise Refused(
            "move_not_said",
            "",
            need="only for a caller who said they moved house; to move a visit, use "
            "reschedule_appointment",
        )
    scoped = {
        cid: row for cid, row in records.scoped_customers(ctx.conn, ctx.call["id"]).items()
        if row["customer_type"] not in ctx.tenant.rejected_customer_types
    }
    customer_id = args.customer_id
    if customer_id is None and len(scoped) == 1:
        customer_id = next(iter(scoped))
    if customer_id not in scoped:
        raise Refused(
            "not_owner", "", gate="scope", errors={"customer_id": "not matched on this call"}
        )
    old = ctx.fsm.locations(customer_id)
    if args.from_location_id is not None:
        old = [loc for loc in old if loc["id"] == args.from_location_id]
        if not old:
            raise Refused(
                "invalid_arguments", "", errors={"from_location_id": "not an active location"}
            )
    elif len(old) > 1:
        raise Refused(
            "which_old_address",
            ctx.tenant.line("move_which"),
            need="ask, then call again with from_location_id",
        )
    checked = _identity_gate(ctx, customer_id, [loc["address"] for loc in old], "account")
    if not ctx.call["readback_confirmed_at"]:
        raise Refused(
            "gate_read_back",
            "Let me read back the new address.",
            need="read_back with the new address, then record_move again",
        )
    new = records.readback(ctx.call)
    if any(booking.same_address(loc, new) for loc in old):
        raise Refused(
            "same_address",
            "",
            need="the read-back is the address already on file; read back the new one",
        )
    try:
        _zip_mismatch(ctx, new["zip"], new["street"], new["city"])
    except Refused:
        with tx(ctx.conn):
            records.update_call(ctx.conn, ctx.call["id"], readback_confirmed_at=None)
        raise
    old_ids = [loc["id"] for loc in old]
    old_place = _place(old[0]["address"]) if old else ""
    jobs = [j for j in ctx.fsm.jobs(customer_id, lookup.OPEN_JOB_STATUSES)
            if j["locationId"] in old_ids]
    in_area = new["zip"] in ctx.tenant.service_area
    key = _key(ctx, "move", {"customer": customer_id, "new": new})
    moved, cancelled, for_person, made = [], [], [], ""
    new_location_id = None
    try:
        if in_area:
            again = [
                loc for loc in ctx.fsm.locations(customer_id, include_inactive=True)
                if booking.same_address(loc, new)
            ]
            if again:
                new_location_id, made = again[0]["id"], "reactivated"
                ctx.fsm.set_location_active(f"{key}:reactivate", new_location_id, True)
            else:
                address = {k: new.get(k, "") for k in ("street", "unit", "city", "zip")}
                created = ctx.fsm.create_location(f"{key}:location", customer_id, address)
                new_location_id, made = created["id"], "added"
            for job in jobs:
                # A technician already on the way is a person's to redirect.
                if job["jobStatus"] != "Scheduled":
                    for_person.append(job["id"])
                    continue
                ctx.fsm.move_job(f"{key}:job:{job['id']}", job["id"], new_location_id)
                ctx.fsm.add_note(
                    f"{key}:job_note:{job['id']}", customer_id,
                    f"Address changed by phone: the customer moved from {old_place} to "
                    f"{_place(new)}, {new['zip']}. Go to the new address.",
                    job["id"], pin=True,
                )
                moved.append(job["id"])
            for location_id in old_ids:
                ctx.fsm.set_location_active(f"{key}:old:{location_id}", location_id, False)
            ctx.fsm.add_note(
                f"{key}:note", customer_id,
                f"Moved from {old_place} to {_place(new)}, {new['zip']} (phone call). The old "
                f"location is inactive; {len(moved)} open visit(s) moved to the new one.",
            )
        else:
            for job in jobs:
                if job["jobStatus"] != "Scheduled":
                    for_person.append(job["id"])
                    continue
                ctx.fsm.cancel_job(
                    f"{key}:cancel:{job['id']}", job["id"], ctx.tenant.fsm["cancel_reason_id"],
                    f"Cancelled by phone: the customer moved to {new['zip']}, outside the "
                    "service area.",
                )
                cancelled.append(job["id"])
            ctx.fsm.add_note(
                f"{key}:note", customer_id,
                f"Caller said they moved from {old_place} to {_place(new)}, {new['zip']}, outside "
                f"the service area. Address not changed; {len(cancelled)} open visit(s) at the old "
                "address cancelled.",
            )
    except (FsmUnavailable, FsmRejected) as exc:
        callback_id, due = _callback(ctx, "fsm_down_minutes", f"Address change could not be made: {exc}")
        with tx(ctx.conn):
            records.add_flag(
                ctx.conn, ctx.call["id"], "move_failed",
                f"Customer {customer_id} moved to {_place(new)}, {new['zip']}; the change stopped "
                f"part way ({exc}). Moved {moved}, cancelled {cancelled}.",
                ctx.now, "High",
            )
        raise Refused(
            "fsm_unavailable" if isinstance(exc, FsmUnavailable) else "fsm_rejected",
            _fsm_down_say(ctx, callback_id, due),
        ) from None
    ctx.cache.invalidate()
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO address_moves (call_id, customer_id, old_location_ids, new_location_id, "
            "new_location_made, street, unit, zip, in_area, jobs_moved, jobs_cancelled, "
            "jobs_for_person, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.call["id"], customer_id, json.dumps(old_ids), new_location_id, made,
                new["street"], new.get("unit", ""), new["zip"], int(in_area), json.dumps(moved),
                json.dumps(cancelled), json.dumps(for_person), iso(ctx.now),
            ),
        )
        if not in_area:
            ctx.conn.execute(
                "INSERT OR IGNORE INTO out_of_area (call_id, zip, at) VALUES (?, ?, ?)",
                (ctx.call["id"], new["zip"], iso(ctx.now)),
            )
        if for_person:
            records.add_flag(
                ctx.conn, ctx.call["id"], "person",
                f"Customer {customer_id} moved to {_place(new)}, {new['zip']}. Job(s) "
                f"{', '.join(map(str, for_person))} already went out to the old address, "
                f"{old_place}; tell the technician.",
                ctx.now, "High",
            )
    if checked:
        _note_unknown_number(ctx, customer_id, None, "Address change by phone.",
                             "The address on your account was changed")
    if in_area:
        say = ctx.tenant.line("move_done", street=_place(new))
        if moved:
            visit = next(j for j in jobs if j["id"] == moved[0])
            try:
                appointment = [a for a in ctx.fsm.appointments(visit["id"])
                               if a["status"] != "Canceled"][-1]
                when = _visit_spoken(
                    ctx, appointment["arrivalWindowStart"], appointment["arrivalWindowEnd"]
                )
            except (FsmUnavailable, FsmRejected, IndexError):
                when = ""
            say += " " + (
                ctx.tenant.line("move_visit_moved_when", when=when) if when
                else ctx.tenant.line("move_visit_moved")
            )
    else:
        say = ctx.tenant.line("move_out_of_area")
        if cancelled:
            say += " " + ctx.tenant.line("move_visit_cancelled")
    if for_person:
        say += " " + ctx.tenant.line("move_visit_person")
    say += " " + ctx.tenant.line("after_confirmation")
    return ok(
        say,
        customer_id=customer_id,
        in_area=in_area,
        new_location_id=new_location_id,
        location=made or None,
        jobs_moved=moved,
        jobs_cancelled=cancelled,
        jobs_for_person=for_person,
    )


@dataclass
class CallbackWhen:
    due: datetime  # when the callback is overdue
    dial_at: datetime  # when the system may place it, for a callback it dials itself
    spoken: str  # "by 4:30 PM", "after 5 PM", "between 1 PM and 3 PM"


def _callback_due(ctx: Ctx, asked: CallbackTime) -> CallbackWhen:
    """The callback time and the words for it. The words always come from here, never the model."""
    tenant, now = ctx.tenant, ctx.now
    days = 1 if asked.callback_day == "tomorrow" else 0
    try:
        if asked.callback_after or asked.callback_before:
            earliest, latest = records.parse_callback_window(
                asked.callback_after, asked.callback_before, tenant, now, days
            )
        else:
            due = records.parse_callback_time(
                asked.callback_time, asked.callback_in_minutes, tenant, now, days
            )
            spoken = tenant.line("callback_by", time=tenant.time_spoken(due, now))
            return CallbackWhen(due, due, spoken)
    except ValueError as exc:
        reason = str(exc)
        if reason == "outside callback hours":
            opens, closes = (
                tenant.time_spoken(
                    datetime.combine(tenant.local(now).date(), time.fromisoformat(t), tenant.tz),
                    now,
                )
                for t in tenant.callbacks["outbound_hours"]
            )
            say = tenant.line("callback_outside_hours", opens=opens, closes=closes)
        else:
            say = "What time should I tell them someone will call by?"
        raise Refused("invalid_callback_time", say, errors={"callback_time": reason}) from None
    # "tomorrow after 12 PM", not "after tomorrow at 12 PM".
    def clock(moment: datetime) -> str:
        return tenant.time_spoken(moment, now).split(" at ")[-1]

    day = tenant.local(now).date() + timedelta(days=days)
    opens, closes = (
        datetime.combine(day, time.fromisoformat(t), tenant.tz)
        for t in tenant.callbacks["outbound_hours"]
    )
    target = None
    if earliest and latest:
        spoken = tenant.line("callback_between", start=clock(earliest), end=clock(latest))
    elif earliest:
        # "After 5" is promised as a time inside it ("around 5:30"), so the caller knows when to
        # expect the call and the office knows when it is late (eval round 3).
        target = min(earliest + timedelta(minutes=tenant.callbacks["after_lead_minutes"]), closes)
        spoken = tenant.line("callback_after", time=clock(target))
    else:
        spoken = tenant.line("callback_before", time=clock(latest))
    if days:
        spoken = f"tomorrow {spoken}"
    if target:
        return CallbackWhen(target, max(earliest, now), spoken)
    # "Tomorrow, before 3" is never dialed today: a window with no start begins when calling hours
    # open on the day asked for (PR #75 review).
    return CallbackWhen(latest or closes, max(earliest or opens, now), spoken)


def _with_window(reason: str, asked: CallbackTime, when: CallbackWhen) -> str:
    """The callback's reason, with the window the caller asked for, so the person calling back
    doesn't call before it ("not before noon, I'm asleep")."""
    if asked.callback_after or asked.callback_before:
        return f"{reason} (caller asked: {when.spoken})"
    return reason


def _no_time(asked: CallbackTime) -> bool:
    return not (
        asked.callback_time
        or asked.callback_in_minutes
        or asked.callback_after
        or asked.callback_before
    )


def _caller_left(ctx: Ctx) -> bool:
    """The caller's last line was a goodbye: they aren't asked for anything more (eval round 4,
    co-op-owner-mentions-board, where asking for a callback time reopened a finished call)."""
    return says_goodbye(records.last_caller_line(ctx.conn, ctx.call["id"]))


def _soon_after_goodbye(ctx: Ctx, asked: CallbackTime) -> CallbackTime:
    """A callback with no time, after the caller said goodbye: due within the tenant's default."""
    minutes = ctx.tenant.callbacks["after_goodbye_minutes"]
    return asked.model_copy(update={"callback_in_minutes": minutes})


def flag_person(ctx: Ctx, args: FlagIn) -> Result:
    """The sentence comes back only after the flag and the callback are stored (spec S-23)."""
    left = _no_time(args) and _caller_left(ctx)
    if _no_time(args):
        # A safety call already has a person flagged who follows up once the caller is safe, so
        # the caller is never asked for a callback time while they leave the house (journey 3).
        if ctx.call["safety_signal_at"] and ctx.call["safety_state"] != "cleared":
            with tx(ctx.conn):
                flag_id = records.add_flag(
                    ctx.conn, ctx.call["id"], "person", args.reason, ctx.now, args.priority
                )
            return ok(ctx.tenant.line("flag_stored_no_callback"), flag_id=flag_id, callback_id=None)
        # After hours the on-call technician calls an urgent caller back within a set time
        # (journey 11), so no time needs to be asked for.
        if left:
            args = _soon_after_goodbye(ctx, args)
        elif ctx.call["after_hours"] and args.priority == "Urgent":
            minutes = ctx.tenant.after_hours_cutoffs["on_call_confirms_within_minutes"]
            args = args.model_copy(update={"callback_in_minutes": minutes})
        # A caller who asked for a person in office hours is about to be put through, so they
        # aren't asked for a time first; this callback is the one kept if nobody answers.
        elif transfer.role_for(ctx.conn, ctx.tenant, ctx.call, False, ctx.numbers) == "office":
            minutes = ctx.tenant.transfer["fallback_callback_minutes"]
            args = args.model_copy(update={"callback_in_minutes": minutes})
    when = _callback_due(ctx, args)
    due = when.due
    # A callback the system places needs no person unless the dial fails (app/outbound.py). The
    # server only allows it for a plain request: never an urgent or high flag, a safety call or a
    # commercial one, which a person must handle whatever the agent asked for.
    system = (
        args.system_calls_back
        and args.priority == "Normal"
        and ctx.call["safety_state"] in ("none", "cleared")
        and not ctx.call["commercial"]
    )
    kind = "callback_requested" if system else "person"
    # After a failed identity check the callback goes to the number on the customer's account,
    # not to this caller, and a person makes it (issue #128).
    mismatch = _mismatch_callback(ctx)
    if mismatch is not None:
        system, kind = False, "person"
    with tx(ctx.conn):
        flag_id = records.add_flag(
            ctx.conn, ctx.call["id"], kind, args.reason, ctx.now, args.priority
        )
        reason = _with_window(args.reason, args, when)
        if mismatch is not None:
            callback_id = records.add_callback(
                ctx.conn, ctx.tenant, ctx.call, due,
                f"Number on file for customer {mismatch['customer_id']}, after a caller from "
                f"{ctx.call['caller_id']} failed the name and address check: {reason}",
                ctx.now, phone=mismatch["on_file_phone"],
            )
            ctx.conn.execute(
                "UPDATE identity_checks SET callback_id = ? WHERE call_id = ? AND customer_id = ?",
                (callback_id, mismatch["call_id"], mismatch["customer_id"]),
            )
        else:
            callback_id = records.add_callback(
                ctx.conn, ctx.tenant, ctx.call, due, reason, ctx.now
            )
        if callback_id and system:
            outbound.add_callback_dial(
                ctx.conn, ctx.tenant, ctx.call, callback_id, when.dial_at, ctx.now
            )
    spoken = when.spoken
    if mismatch is not None and callback_id is not None:
        return ok(
            ctx.tenant.line("flag_stored_on_file", when=spoken),
            flag_id=flag_id, callback_id=callback_id, callback_at=iso(due), callback_spoken=spoken,
        )
    if left:
        # Stored, and nothing more is said: the caller has gone.
        return ok(
            "",
            flag_id=flag_id,
            callback_id=callback_id,
            callback_at=iso(due),
            callback_spoken=ctx.tenant.line("callback_asap"),
            note="The caller said goodbye. End the call now; ask nothing more.",
        )
    if callback_id is None:
        say = ctx.tenant.line("flag_stored_no_callback")
    elif args.priority == "Urgent" and ctx.call["after_hours"]:
        say = ctx.tenant.line("flag_stored_on_call", when=spoken)
    elif args.priority == "Urgent":
        say = ctx.tenant.line("flag_stored_urgent", when=spoken)
    else:
        say = ctx.tenant.line("flag_stored", when=spoken)
    return ok(
        say, flag_id=flag_id, callback_id=callback_id, callback_at=iso(due), callback_spoken=spoken
    )


def demand_handoff(ctx: Ctx, text: str) -> Result | None:
    """A caller who demands a person on this line is sent to one now (app/transfer.py decides who,
    from their words). Not a tool: the turn endpoint asks after the safety check, so the model
    can't start it and can't hold it up. The flag and callback are stored before anything is
    dialed. `say` is the handoff line when there is a number to send the call to, and otherwise
    the stored callback line; `handoff.fallback` is the callback line either way."""
    ctx.reload()
    role = transfer.handoff_role(ctx.conn, ctx.tenant, ctx.call, text)
    if role is None:
        return None
    on_safety = bool(ctx.call["safety_signal_at"]) and ctx.call["safety_state"] != "cleared"
    reason = "Caller demanded a person" + (" after the safety lines" if on_safety else "")
    when = None
    # A safety call is never given a callback time while the caller leaves the house: the safety
    # flag already has a person following up (journey 3), as in flag_person.
    if not on_safety:
        cutoffs = ctx.tenant.after_hours_cutoffs
        minutes = (
            cutoffs["on_call_confirms_within_minutes"]
            if role == "on_call"
            else ctx.tenant.transfer["fallback_callback_minutes"]
        )
        when = _callback_due(ctx, CallbackTime(callback_in_minutes=minutes))
    with tx(ctx.conn):
        flag_id = records.add_flag(
            ctx.conn, ctx.call["id"], "person", reason, ctx.now, "Urgent" if on_safety else "High"
        )
        callback_id = (
            records.add_callback(ctx.conn, ctx.tenant, ctx.call, when.due, reason, ctx.now)
            if when
            else None
        )
        plan = transfer.start_handoff(
            ctx.conn, ctx.tenant, ctx.call, role, flag_id, callback_id, ctx.now, ctx.numbers
        )
    fallback = (
        ctx.tenant.line("flag_stored", when=when.spoken)
        if callback_id
        else ctx.tenant.line("flag_stored_no_callback")
    )
    say = ctx.tenant.line("handoff_now") if plan else fallback
    return ok(
        say,
        handoff={
            "flag_id": flag_id,
            "callback_id": callback_id,
            "transfer": plan,
            "fallback": fallback,
        },
    )


def record_lead(ctx: Ctx, args: LeadIn) -> Result:
    """One write, no extra lookups (spec S-08). A commercial lead marks the call, so it can never
    become a booking."""
    details = args.details
    due = when = None
    if args.kind != "commercial" and _no_time(details) and _caller_left(ctx):
        details = _soon_after_goodbye(ctx, details)
    if args.kind != "commercial":
        when = _callback_due(ctx, details)
        due = when.due
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    summary = "; ".join(
        part
        for part in (
            details.company,
            f"contact {details.contact_name} {ctx.call['caller_id']}",
            details.need,
        )
        if part
    )
    lead = {
        "callReasonId": ctx.tenant.fsm["lead_call_reason_ids"][args.kind],
        "customerId": next(iter(scoped))
        if len(scoped) == 1 and args.kind != "commercial"
        else None,
        "leadCustomerName": details.company or details.contact_name,
        "leadPhone": ctx.call["caller_id"] if len(ctx.call["caller_key"]) == 10 else None,
        "summary": summary,
        "campaignId": ctx.tenant.fsm["campaign_id"],
    }
    key = _key(ctx, "lead", args.model_dump(mode="json"))
    with tx(ctx.conn):
        if args.kind == "commercial":
            records.update_call(ctx.conn, ctx.call["id"], commercial=1)
        callback_id = (
            records.add_callback(
                ctx.conn,
                ctx.tenant,
                ctx.call,
                due,
                _with_window(f"{args.kind} lead: {details.need}", details, when),
                ctx.now,
            )
            if due
            else None
        )
        lead_id = ctx.conn.execute(
            """INSERT INTO leads (call_id, kind, summary, status, fsm_key, request_json, callback_id,
                                  created_at)
               VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)""",
            (ctx.call["id"], args.kind, summary, key, json.dumps(lead), callback_id, iso(ctx.now)),
        ).lastrowid
    stored = send_lead(ctx.conn, ctx.fsm, lead_id, lead, key, ctx.now)
    if args.kind == "commercial":
        say = ctx.tenant.line("commercial")
    elif callback_id is None:
        say = ctx.tenant.line("flag_stored_no_callback")
    else:
        say = ctx.tenant.line("lead_stored", when=when.spoken)
    return ok(say, lead_id=lead_id, queued=not stored, callback_id=callback_id)


def send_lead(
    conn: sqlite3.Connection, fsm: FsmAdapter, lead_id: int, lead: dict, key: str, now: datetime
) -> bool:
    """Send a stored lead to the FSM. False leaves it queued for the retry job."""
    try:
        created = fsm.create_lead(key, lead)
    except FsmUnavailable as exc:
        with tx(conn):
            conn.execute(
                "UPDATE leads SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                (str(exc), lead_id),
            )
        return False
    except FsmRejected as exc:
        with tx(conn):
            conn.execute(
                "UPDATE leads SET status = 'failed', attempts = attempts + 1, last_error = ? WHERE id = ?",
                (str(exc), lead_id),
            )
            records.add_flag(conn, None, "lead_failed", f"Lead {lead_id}: {exc}", now, "High")
        return False
    with tx(conn):
        conn.execute(
            "UPDATE leads SET status = 'stored', fsm_lead_id = ?, attempts = attempts + 1 WHERE id = ?",
            (created["id"], lead_id),
        )
    return True


def add_note(ctx: Ctx, args: NoteIn) -> Result:
    """Append-only free text, stored as text and never read as an instruction. There is no tool
    that edits or deletes a note."""
    text = " ".join(args.text.split())
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    if args.attach_to == "customer":
        customer_id = args.customer_id
        if customer_id is None and len(scoped) == 1:
            customer_id = next(iter(scoped))
        if customer_id not in scoped:
            raise Refused(
                "not_owner", "", gate="scope", errors={"customer_id": "not matched on this call"}
            )
        note = ctx.fsm.add_note(_key(ctx, "note", args.model_dump()), customer_id, text)
        fsm_note_id, where = note.get("id"), "customer"
    else:
        thread = records.thread(ctx.conn, ctx.call["id"])
        job = ctx.conn.execute(
            f"SELECT fsm_job_id, customer_id FROM bookings WHERE kind = 'book' AND status = 'booked' "
            f"AND call_id IN ({','.join('?' * len(thread))}) ORDER BY id DESC LIMIT 1",
            thread,
        ).fetchone()
        customer_id = job["customer_id"] if job else None
        if job:
            note = ctx.fsm.add_note(
                _key(ctx, "note", args.model_dump()), customer_id, text, job["fsm_job_id"]
            )
            fsm_note_id, where = note.get("id"), "job"
        else:
            # Not booked yet: the note goes into the job summary when it is.
            fsm_note_id, where = None, "pending_job"
    with tx(ctx.conn):
        note_id = ctx.conn.execute(
            "INSERT INTO notes (call_id, attach_to, customer_id, fsm_note_id, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ctx.call["id"], args.attach_to, customer_id, fsm_note_id, text, iso(ctx.now)),
        ).lastrowid
    return ok(note_id=note_id, stored_in=where)


def mark_do_not_call(ctx: Ctx, args: DoNotCallIn) -> Result:
    if len(ctx.call["caller_key"]) != 10:
        raise Refused("invalid_phone", ctx.tenant.line("do_not_call"))
    with tx(ctx.conn):
        records.mark_do_not_call(ctx.conn, ctx.call, ctx.now)
    # The caller's own words may already have marked it on their turn, so count what this call
    # cancelled rather than what this request did.
    cancelled = ctx.conn.execute(
        "SELECT COUNT(*) FROM callbacks WHERE phone_key = ? AND status = 'cancelled' AND updated_at >= ?",
        (ctx.call["caller_key"], ctx.call["started_at"]),
    ).fetchone()[0]
    say = ctx.tenant.line("do_not_call_with_callback" if cancelled else "do_not_call")
    return ok(say, cancelled_callbacks=cancelled)


def end_call(ctx: Ctx, args: EndCallIn) -> Result:
    """Refused on a safety call until the lines were said and a person flagged; after that it
    returns the sign-off, and the call ends when the caller goes (spec S-19)."""
    state, safety = ctx.call["safety_state"], ctx.tenant.safety
    if state in ("signal", "checking"):
        _safety_gate(ctx)
    had_signal = ctx.call["safety_signal_at"] is not None and state != "cleared"
    if had_signal and args.reason in ("spam", "wrong_number", "not_a_customer"):
        raise Refused(
            "gate_safety",
            " ".join(safety["sign_off"].split()),
            need="no spam label on a safety call",
        )
    if had_signal:
        hang_up = args.reason == "caller_goodbye"
        if hang_up:
            with tx(ctx.conn):
                records.update_call(ctx.conn, ctx.call["id"], end_requested=1)
        sign_off = " ".join(safety["sign_off"].split())
        # One ending: the sign-off is said once a call, whether the caller is leaving or keeps
        # talking (eval round 3).
        if records.agent_said(ctx.conn, ctx.call["id"], sign_off):
            # signed_off tells the voice layer the goodbye has been said, so it adds none.
            return ok("", hang_up=hang_up, signed_off=True)
        return ok(sign_off, hang_up=hang_up)
    last = ctx.conn.execute(
        "SELECT turn_seq FROM bookings WHERE call_id = ? AND kind = 'book' AND status IN ('sending', 'booked', 'queued') "
        "ORDER BY id DESC LIMIT 1",
        (ctx.call["id"],),
    ).fetchone()
    if (
        last
        and args.reason != "caller_goodbye"
        and not records.caller_turn_after(ctx.conn, ctx.call["id"], last["turn_seq"])
    ):
        # Never end in the same turn as the confirmation (spec S-30).
        raise Refused("anything_else_first", ctx.tenant.line("anything_else_first"))
    with tx(ctx.conn):
        records.update_call(ctx.conn, ctx.call["id"], end_requested=1)
    return ok(hang_up=True)


# Every model tool: (handler, request body, writes). Writes take an Idempotency-Key.
TOOLS = {
    "find_customer": (find_customer, FindCustomerIn, False),
    "get_customer_history": (get_customer_history, HistoryIn, False),
    "check_service_area": (check_service_area, ServiceAreaIn, False),
    "find_address": (find_address, FindAddressIn, False),
    "find_slots": (find_slots, FindSlotsIn, False),
    "read_back": (read_back, ReadBackIn, False),
    "book_appointment": (book_appointment, BookIn, True),
    "reschedule_appointment": (reschedule_appointment, RescheduleIn, True),
    "cancel_appointment": (cancel_appointment, CancelIn, True),
    "record_move": (record_move, MoveIn, True),
    "flag_person": (flag_person, FlagIn, True),
    "record_lead": (record_lead, LeadIn, True),
    "add_note": (add_note, NoteIn, True),
    "mark_do_not_call": (mark_do_not_call, DoNotCallIn, True),
    "end_call": (end_call, EndCallIn, True),
}
