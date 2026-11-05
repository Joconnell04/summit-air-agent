"""The tools the model can call (docs/tools.md). Each handler checks its gates in server code and
returns a Result or raises Refused; app.main logs every call.

The model asks; the server decides. A gate here is never a flag the model sets: read-back counts
only after the server returned the sentence and a caller line followed, the safety gate reads the
call's own state, and ownership is checked against the customers matched on this call.
"""

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise

from . import booking, geocode, heads_up, lookup, outbound, pace, records, safety, transfer, weather
from . import capacity as capacity_rules
from . import priority as prio
from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .lifecycle import approval_unclear
from .readback import change as readback_change
from .readback import units_said
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
    DIGIT_WORDS,
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
    spelled_word,
    street_key,
    unit_key,
    with_spelled_name,
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
        # The turn the caller heard it on, so the call can't end before they answer (end_call).
        ctx.conn.execute(
            "INSERT OR IGNORE INTO lines_handed (call_id, line, at) VALUES (?, ?, ?)",
            (ctx.call["id"], f"{OUT_OF_AREA_TURN}{ctx.call['turn_seq']}", iso(ctx.now)),
        )


OUT_OF_AREA_TURN = "out_of_area_turn:"


def _out_of_area_unanswered(ctx: Ctx) -> bool:
    """The caller was told their address is outside the area and hasn't spoken since."""
    rows = ctx.conn.execute(
        "SELECT line FROM lines_handed WHERE call_id = ? AND line LIKE ?",
        (ctx.call["id"], OUT_OF_AREA_TURN + "%"),
    ).fetchall()
    if not rows:
        return False
    seq = max(int(r["line"].removeprefix(OUT_OF_AREA_TURN)) for r in rows)
    return not records.caller_turn_after(ctx.conn, ctx.call["id"], seq)


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
STREET_GAP = 3
# Small words, and a direction, between a house number and its street: "199 East Fourth", "248 on
# Duffield" (Jackson's call on the Retell number: "One nine nine East fourth Street" was refused
# three times because "e" sat between the number and "fourth").
STREET_GAP_WORDS = {
    "on", "at", "the", "off", "um", "uh", "is", "its", "spelled",
    "e", "w", "n", "s", "east", "west", "north", "south",
}
# Ordinals said as words, as the street key writes them: "fourth" is "4", "forty second" is "42".
ORDINAL_UNITS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9,
}
ORDINAL_WORDS = {
    **ORDINAL_UNITS, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
    "sixtieth": 60, "seventieth": 70, "eightieth": 80, "ninetieth": 90, "hundredth": 100,
}


def _ordinals_as_numbers(tokens: list[str], merge: bool = True) -> list[str]:
    """ "fourth" as "4", and with `merge`, a tens number before an ordinal unit as one ("40
    second" is "42"), so a street said either way matches the street written either way. Without
    `merge` the tens number stays a house number ("fifty fourth avenue" is 50 Fourth Avenue)."""
    out: list[str] = []
    for token in tokens:
        if merge and token in ORDINAL_UNITS and out and out[-1].isdigit() and (
            out[-1].endswith("0") and len(out[-1]) == 2
        ):
            out[-1] = str(int(out[-1]) + ORDINAL_UNITS[token])
            continue
        out.append(str(ORDINAL_WORDS[token]) if token in ORDINAL_WORDS else token)
    return out


def _address_tokens(text: str, merge: bool = True) -> list[str]:
    """A caller's line as address tokens: numbers as digits however they were said ("one nine
    nine", "one ninety nine", "a hundred ninety nine", "one hundred and ninety nine"), street words
    keyed, and ordinals as numbers."""
    text = re.sub(r"\ba hundred\b", "one hundred", text or "", flags=re.IGNORECASE)
    return _ordinals_as_numbers(_keyed(number_tokens(text)), merge)


def _street_names(street: str) -> list[str]:
    """The words that name a street, ordinals as numbers: "East Fourth Street" is ["4"]."""
    return _ordinals_as_numbers(geocode.street_name_words(street))
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
    names = _street_names(street)
    if not want or not names:
        return False
    # Both readings of a tens number before an ordinal: "fifty fourth avenue" is 50 Fourth Avenue
    # or 54th Avenue; "forty second street" is 42nd Street (PR #158 review).
    return any(
        _address_said_as(ctx, want, names, rows, merge) for merge in (True, False)
    )


def _address_said_as(ctx: Ctx, want: str, names: list[str], rows: list, merge: bool) -> bool:
    lines = [
        _split_run_on(_address_tokens(r["text"], merge), want, names, r["text"]) for r in rows
    ]
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


def _split_run_on(tokens: list[str], want: str, names: list[str], text: str) -> list[str]:
    """A house number and a numbered street said digit by digit with no break, "one one eight 24
    one nine seven th street", read as one number (11824197): split back into the house and the
    street when the run is exactly those two (eval run 20261104-201607,
    caller-declines-safety-advice)."""
    street = names[0] if names and names[0].isdigit() else ""
    run = want + street
    # Only a run said digit by digit: "112 2nd Avenue" and "one twelve 2nd avenue" are house 112.
    if not street or not _digit_by_digit(text, run):
        return tokens
    return [part for token in tokens for part in ([want, street] if token == run else [token])]


SINGLE_DIGITS = {"zero", "oh", "o", "one", "two", "three", "four", "five", "six", "seven", "eight",
                 "nine"}


def _digit_by_digit(text: str, run: str) -> bool:
    """The run was said mostly one digit at a time: a stretch of digit words and short numbers
    whose digits are the run, at least half of them single digits ("one one eight 24 one nine
    seven" is 11824197)."""
    tokens = words(text).split()
    for start in range(len(tokens)):
        digits, singles = "", 0
        for count, token in enumerate(tokens[start:], start=1):
            if token in SINGLE_DIGITS:
                digits += DIGIT_WORDS[token]
                singles += 1
            elif token.isdigit() and len(token) <= 2:
                digits += token
            else:
                break
            if digits == run:
                return singles * 2 >= count >= 4
            if not run.startswith(digits):
                break
    return False


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


def _with_spelling(ctx: Ctx, street: str, spelled: str) -> str:
    """The street with the name the caller spelled in it, only when one of the caller's lines on
    this call spells those letters, and never over a street the caller said as it is. Slate call 4
    (Retell call_f8a3ad3177fa2c62fc9db00dcfa): the model passed spelled "E, A, S, T, 4, T, H, S, T,
    R, E, E, T" for "199 East 4th Street", which the caller never spelled, and the booking went to
    "199 Eastthstreet Street"."""
    word = spelled_word(spelled or "")
    if not word:
        return street
    lines = _caller_lines(ctx)
    spelled_by_caller = any(
        spelled_word(run.group()).lower() == word.lower()
        for row in lines
        for run in SPELLED_RUN.finditer(row["text"])
    )
    if not spelled_by_caller:
        return street
    split = geocode.split_address(street)
    if split and _address_said(ctx, split[0], split[1], lines):
        return street
    return with_spelled_name(street, spelled)


def _street_in(line: str, names: list[str]) -> bool:
    return any(
        all(name in set(_address_tokens(line, merge)) for name in names) for merge in (True, False)
    )


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
        electrical_line=safety.electrical_line(caller, ctx.tenant.safety) or "",
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
    minutes: int | None = None,
    start_at: time | None = None,
) -> list[dict]:
    """Open windows from now to the end of the booking range, earliest first: runs of `minutes`
    (the tenant's window length by default) on the company's 30-minute blocks, or with `start_at`,
    the run starting then (app/capacity.py). ServiceTitan's time
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
    found = capacity_rules.open_windows(tenant, frames, level, today, end_day, minutes, start_at)
    # A daytime window that starts this minute is no window to offer: there is no time to get
    # there, and a call before it (an approval check) would have no room.
    return [w for w in found if w["after_hours"] or parse_iso(w["start"]) > now]


def _open_windows(
    ctx: Ctx, job_type: dict, level: str, minutes: int | None = None, start_at: time | None = None
) -> list[dict]:
    return open_windows(
        ctx.tenant, ctx.fsm, ctx.cache, ctx.now, job_type, level, minutes, start_at
    )


def _tiled(windows: list[dict]) -> list[dict]:
    """Windows that don't overlap, earliest first, for a day said as a list: "8 to 10, 10 to 12
    or 12 to 2", not every half-hour start."""
    kept: list[dict] = []
    for window in sorted(windows, key=lambda w: (w["start"], w["end"])):
        if not kept or window["after_hours"] or window["start"] >= kept[-1]["end"]:
            kept.append(window)
    return kept


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
        "part": spec.part if spec else "",
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
    tokens = _address_tokens(text)
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


# Words right before a surname that make it a name the caller is giving: "this is", "my name
# is", "I'm", "it's", "under". The customer's first name before it counts too ("Maria Delgado").
NAME_LEADS = (("this", "is"), ("name", "is"), ("im",), ("i", "am"), ("its",), ("under",))
# What a line that is only the name may also hold: "Um, Delgado." or "Sure, Maria Delgado."
NAME_LINE_FILLER = {"um", "uh", "its", "sure", "yes", "yeah", "ok", "okay", "thats", "the",
                    "name", "is", "mr", "mrs", "ms", "miss", "dr"}
NAME_LINE_MAX_WORDS = 5
STREET_AFTER = geocode.STREET_TYPE_WORDS | {
    "street", "avenue", "road", "place", "boulevard", "drive", "lane", "court", "terrace",
    "parkway", "broadway",
}
NUMBER_WORDS = {"oh", "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine", "ten", "hundred"}


# Surnames that are also everyday words: "This is price gouging", "It's long overdue". These count
# only with the first name before them, or as a line that is just the name.
COMMON_WORD_SURNAMES = {
    "price", "long", "young", "king", "brown", "green", "white", "black", "gray", "grey", "rose",
    "bell", "hall", "hill", "wood", "stone", "rice", "lane", "may", "day", "page", "love", "hope",
    "cook", "baker", "best", "fair", "good", "small", "little", "short", "strong", "rich", "wise",
    "sharp", "frost", "snow", "field", "ford", "park", "west", "north", "south", "east", "bush",
    "street", "case", "cash", "hunt", "law", "fox", "wolf", "lamb", "march", "april", "june",
    "summer", "winter", "bond", "chase", "cross", "major", "mason", "miller", "parker", "porter",
    "power", "sweet", "true", "bright", "early", "free", "hart", "house", "moon", "shore",
}


def _name_given(text: str, name: str) -> bool:
    """The caller gave this customer's surname as a name in this line: after the customer's first
    name, after a name lead ("this is", "under"), at the start of the line ("Delgado, 620
    Bedford"), or as a line that is just the name. A surname inside the address ("620 Bedford
    Avenue") or in passing ("what's the price") is no name, and a surname that is an everyday
    word needs the first name or a line of its own (PR #134 review)."""
    surname = _surname(name)
    first = _name_words(name)[0] if len(_name_words(name)) > 1 else ""
    common = surname in COMMON_WORD_SURNAMES
    tokens = _name_words(text)
    # (where the surname starts, where it ends): a surname split in two by speech to text ("Del
    # Gado") is read as one word.
    hits = [(i, i) for i, t in enumerate(tokens) if t == surname]
    hits += [(i, i + 1) for i, (a, b) in enumerate(pairwise(tokens)) if a + b == surname]
    lead_at = next((i for i, t in enumerate(tokens) if t not in NAME_LINE_FILLER), 0)
    for begin, end in hits:
        after = tokens[end + 1] if end + 1 < len(tokens) else ""
        before = tokens[max(0, begin - 2):begin]
        if after in STREET_AFTER or (before and (before[-1].isdigit() or before[-1] in NUMBER_WORDS)):
            continue
        if first and before and before[-1] == first:
            return True
        if common:
            continue
        if begin == lead_at or any(
            tuple(before[-len(lead):]) == lead for lead in NAME_LEADS if len(before) >= len(lead)
        ):
            return True
    rest = "".join(t for t in tokens if t not in NAME_LINE_FILLER)
    return bool(hits) and len(tokens) <= NAME_LINE_MAX_WORDS and rest in (
        surname, "".join(_name_words(name))
    )


UNIT_CUES = {"apartment", "apt", "unit", "number", "no", "#", "suite", "floor", "fl"}


def _unit_said(rows: list, unit: str) -> bool:
    """The location's unit, as the caller said it: "3B", "apartment three B". A unit only the
    model gave is not enough, the same as a ZIP (DECISIONS.md 058). A unit that isn't both letters
    and digits ("A", "12") needs a cue before it ("apartment A", "unit 12"), so the article "a"
    is no unit (PR #134 review)."""
    want = unit_key(unit)
    if not want:
        return True
    mixed = any(c.isdigit() for c in want) and any(c.isalpha() for c in want)
    for row in rows:
        raw = [t for t in number_tokens(row["text"]) if t.strip()]
        for size in (1, 2, 3):
            for i in range(len(raw) - size + 1):
                window = raw[i:i + size]
                if not all(t.isalnum() for t in window) or "".join(window) != want:
                    continue
                if mixed or (i > 0 and raw[i - 1] in UNIT_CUES):
                    return True
    return False


def _identity_said(ctx: Ctx, name: str, addresses: list[dict]) -> tuple[bool, bool]:
    """Whether the caller gave the customer's surname as a name (_name_given) and said one of
    these addresses (house number, street and any unit), each before the agent said it."""
    turns = records.thread_turns(ctx.conn, ctx.call["id"])
    surname = _surname(name)
    name_ok = bool(surname) and any(
        _name_given(r["text"], name)
        for r in _said_before_agent(turns, lambda t: _has_word(t, surname))
    )
    address_ok = False
    for address in addresses:
        split = geocode.split_address(address["street"])
        if not split:
            continue
        house, names = geocode.house_key(split[0]), _street_names(split[1])
        rows = _said_before_agent(turns, lambda t, h=house, n=names: _address_tokens_in(t, h, n))
        if rows and _address_said(ctx, split[0], split[1], rows) and _unit_said(
            rows, address.get("unit") or ""
        ):
            address_ok = True
            break
    return name_ok, address_ok


def _identity_row_status(ctx: Ctx, customer_id: int) -> str | None:
    row = _identity_row(ctx, customer_id)
    return row["status"] if row else None


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


# Words that are no name, after a name lead ("it's the visit at...", "under my wife's name") or in
# a short line ("No.", "What?", "hold on").
NOT_NAMES = {
    "i", "no", "nope", "yes", "yeah", "yep", "what", "why", "who", "sorry", "hello", "hi", "hey",
    "okay", "ok", "huh", "wait", "hold", "on", "sure", "um", "uh", "please", "thanks", "thank",
    "just", "it", "its", "the", "a", "an", "my", "is", "that", "thats", "this", "you", "your",
    "hmm", "right", "for", "under", "me", "mine", "our", "his", "her", "their", "same", "not",
    "already", "told", "said", "name", "names", "booked", "booking", "visit", "appointment",
    "account", "here", "there", "today", "tomorrow", "now", "fine", "good", "cold", "hot", "out",
    "broken", "leaking", "off", "calling", "about", "again", "really", "correct", "wrong", "too",
    "and", "or", "but", "so", "to", "of", "in", "at", "with", "do", "did", "can", "cant", "dont",
    "im", "am", "was", "be", "been", "have", "has", "had", "need", "want", "know", "guess",
    "think", "maybe", "still", "also", "all", "one", "number", "phone", "address", "street",
}


# A name lead strong enough that the word after it is a name in any case ("this is pat"), and ones
# that need more: "it's", "I'm" ("it's working", "I'm home") count before a capitalized word, or
# before two words that are neither common nor numbers ("its joanne price").
STRONG_NAME_LEADS = (("this", "is"), ("name", "is"), ("names",), ("under",), ("call", "me"))
WEAK_NAME_LEADS = (("its",), ("im",), ("i", "am"))
VERBISH = {
    "working", "running", "leaking", "getting", "making", "going", "coming", "doing", "been",
    "worse", "better", "really", "pretty", "very", "still", "back", "off", "on", "up", "down",
}


def _not_a_name(key: str, things: frozenset[str] = frozenset()) -> bool:
    """A word that is no person's name: a common word, a number, a verb, a street word, or one of
    the tenant's things and places (Tenant.not_names: "It's the furnace", "It's Brooklyn")."""
    return (
        (not key.isalpha()) or key in NOT_NAMES or key in NUMBER_WORDS or key in VERBISH
        or key in things or key in STREET_AFTER
    )


def _gave_a_name(text: str, things: frozenset[str] = frozenset()) -> bool:
    """The caller said some name, whoever's: after a name lead ("this is uh pat rivera", "my name
    is Sam", "under Rivera", "It's Sam Rivera") or as a short line of nothing else ("Sam Rivera.",
    "joanne price"). Lowercase counts too (the text harness's lines are), so common words, numbers
    and verbs are never taken for a name, and "I just gave you the name" gives none."""
    raw = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    keys = [w.lower().replace("'", "").replace("-", "") for w in raw]
    if any(k in STREET_AFTER for k in keys):
        # A line with a street in it is an address, whatever else it has ("It's Bedford Avenue").
        named_lead = any(tuple(keys[i:i + len(lead)]) == lead
                         for lead in STRONG_NAME_LEADS for i in range(len(keys)))
        if not named_lead:
            return False
    for leads, strong in ((STRONG_NAME_LEADS, True), (WEAK_NAME_LEADS, False)):
        for i in range(len(keys)):
            for lead in leads:
                if tuple(keys[i:i + len(lead)]) != lead:
                    continue
                j = i + len(lead)
                while j < len(keys) and keys[j] in ("um", "uh"):
                    j += 1
                if j >= len(keys) or _not_a_name(keys[j], things):
                    continue
                if strong or raw[j][0].isupper():
                    return True
                if j + 1 < len(keys) and not _not_a_name(keys[j + 1], things):
                    return True
    named = [k for k in keys if k not in NAME_LINE_FILLER]
    return 0 < len(named) <= 3 and not any(_not_a_name(k, things) for k in named)


def _gave_an_address(text: str) -> bool:
    """The caller said an address: a house number (digits or number words, "one four two") and a
    street word after it ("Main Street"). A number on its own ("give me one second") is none (PR
    #155 review)."""
    tokens = _name_words(text)
    numbered = [i for i, t in enumerate(tokens) if t.isdigit() or t in NUMBER_WORDS]
    return any(t in STREET_AFTER for i, t in enumerate(tokens) if numbered and i > numbered[0])


def _answered_check(ctx: Ctx, check: sqlite3.Row, name_ok: bool, address_ok: bool) -> bool:
    """Since the check was asked, the caller gave what was still missing (a name, or an address),
    and it didn't match."""
    lines = [
        r["text"] for r in records.thread_turns(ctx.conn, ctx.call["id"])
        if r["call_id"] == check["call_id"] and r["speaker"] == "caller"
        and r["seq"] > check["asked_seq"]
    ]
    surname = _surname(ctx.fsm.customer(check["customer_id"])["name"])
    gave_name = any(
        _gave_a_name(t, ctx.tenant.not_names) and not (surname and _has_word(t, surname))
        for t in lines
    )
    gave_address = any(_gave_an_address(t) for t in lines)
    return (not name_ok and gave_name) or (not address_ok and gave_address)


def _identity_gate(ctx: Ctx, customer_id: int, addresses: list[dict], thing: str) -> bool:
    """Refuses with identity_check until the caller has said a name and address matching the
    customer, when the call's number isn't on that customer's account. After an answer that
    doesn't match, the change is refused for the call (identity_mismatch) and the agent offers a
    callback to the number on file; only an answer with no name or address in it is asked again. Returns True when the check was needed and passed, so the
    change is noted for the office."""
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    if customer_id in scoped and scoped[customer_id]["matched_by"] == "caller_id":
        return False
    customer = ctx.fsm.customer(customer_id)
    check = _identity_row(ctx, customer_id)
    if check is not None and check["status"] == "verified":
        return True
    # A question asked on an earlier call is asked again on this one, and a failed check there
    # doesn't refuse this call: live call 2 (issue #149) was linked to call 1, whose wrong name
    # ("Sam Rivera") refused Pat Rivera's move before they were asked anything.
    if check is not None and check["status"] in ("asked", "mismatch") and (
        check["call_id"] != ctx.call["id"]
    ):
        check = None
    if check is not None and check["status"] == "mismatch":
        raise Refused(
            "identity_mismatch",
            ctx.tenant.line("identity_mismatch"),
            need="no change can be made on this call; offer the callback, and flag_person if "
            "the caller wants it. The callback always goes to the number on file: never offer "
            "to call another number, and never say what the number on file is",
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
    if check is None and not name_ok and _named_on_call(ctx, customer["name"]):
        # The caller already gave a name that isn't the one on file (they were asked by the agent,
        # or said it): asking again is the repeat issue #149 is about, so it is the one refusal.
        _record_mismatch(ctx, customer_id, ctx.call["id"], thing, insert=True)
    if check is None:
        with tx(ctx.conn):
            ctx.conn.execute(
                "INSERT INTO identity_checks (call_id, customer_id, status, asked_seq, at) "
                "VALUES (?, ?, 'asked', ?, ?)",
                (ctx.call["id"], customer_id, ctx.call["turn_seq"], iso(ctx.now)),
            )
        # Only what the caller hasn't said yet is asked for (eval run 20261104-201343: asking
        # again for an address the caller opened with read as a repeat).
        line = (
            "identity_check_name_only" if address_ok
            else "identity_check_address_only" if name_ok
            else "identity_check"
        )
        raise Refused("identity_check", ctx.tenant.line(line, thing=thing), need=note)
    if not records.caller_turn_after(ctx.conn, check["call_id"], check["asked_seq"]):
        raise Refused("identity_check", ctx.tenant.line("identity_check", thing=thing), need=note)
    # An answer that doesn't match is final: one polite refusal and the callback, never the same
    # question again (live call 1, issue #149: "Sam Rivera", then "And what's the name on the
    # visit?" twice). Only a caller who gave no name or address at all is asked once more.
    answered = _answered_check(ctx, check, name_ok, address_ok)
    if check["asks"] < IDENTITY_ASKS and not answered:
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
    _record_mismatch(ctx, customer_id, check["call_id"], thing)


def _caller_named_anyone(ctx: Ctx) -> bool:
    """The caller said some name on this call."""
    return any(
        _gave_a_name(r["text"], ctx.tenant.not_names)
        for r in records.thread_turns(ctx.conn, ctx.call["id"])
        if r["call_id"] == ctx.call["id"] and r["speaker"] == "caller"
    )


SELF_LEADS = (("this", "is"), ("name", "is"), ("names",), ("im",), ("i", "am"))


def _someone_else(ctx: Ctx, customer_id: int) -> bool:
    """The caller introduced themself by a name that isn't the matched customer's, in a line with
    no word of that customer's name in it ("No, I'm Jordan Lee. I'm new." on a number on file under
    Pat Rivera, Retell call call_1840576e622e3d729b760085142). That customer isn't the caller: no
    question about whose account a new address goes on, and the visit doesn't go on theirs. A
    failed lookup counts the caller as the customer, as before."""
    try:
        own = {w for w in _name_words(ctx.fsm.customer(customer_id)["name"]) if len(w) > 1}
    except (FsmUnavailable, FsmRejected):
        return False
    things = ctx.tenant.not_names
    for row in records.thread_turns(ctx.conn, ctx.call["id"]):
        if row["call_id"] != ctx.call["id"] or row["speaker"] != "caller":
            continue
        raw = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", row["text"])
        keys = [w.lower().replace("'", "").replace("-", "") for w in raw]
        if own & set(keys):
            continue
        for i in range(len(keys)):
            for lead in SELF_LEADS:
                if tuple(keys[i:i + len(lead)]) != lead:
                    continue
                j = i + len(lead)
                while j < len(keys) and keys[j] in ("um", "uh"):
                    j += 1
                if j >= len(keys) or _not_a_name(keys[j], things):
                    continue
                if raw[j][0].isupper() or (j + 1 < len(keys) and not _not_a_name(keys[j + 1], things)):
                    return True
    return False


def _callers_accounts(ctx: Ctx) -> dict:
    """The customers matched on this call, less one the caller said they aren't, and less one
    ServiceTitan no longer has: a scripted call after the live world was reseeded carried customer
    301 from the old world, skipped the name question, and the new customer was sent with no name
    (call-_+16466240418_ECUCgf3cyyQP, a 400 from ServiceTitan)."""
    found = {}
    for cid, row in records.scoped_customers(ctx.conn, ctx.call["id"]).items():
        try:
            ctx.fsm.customer(cid)
        except FsmRejected:
            continue
        except FsmUnavailable:
            pass
        if not _someone_else(ctx, cid):
            found[cid] = row
    return found


def _check_failed_on_call(ctx: Ctx) -> bool:
    """A name and address check failed on this call."""
    return bool(
        ctx.conn.execute(
            "SELECT 1 FROM identity_checks WHERE call_id = ? AND status = 'mismatch'",
            (ctx.call["id"],),
        ).fetchone()
    )


ASKS_FOR_NAME = re.compile(
    r"\bname\b|\bwho am i (?:speaking|talking) (?:with|to)\b|\bwho(?:'s| is) (?:calling|this)\b",
    re.IGNORECASE,
)


def _named_on_call(ctx: Ctx, name: str) -> bool:
    """The caller answered the agent asking for a name with one that isn't the customer's. A
    line with the customer's surname in it is no wrong name, even when it doesn't count because
    the agent said it first. A name the caller offered unasked ("Hi, this is Jordan") is no answer:
    the check is asked first (Retell's builder agent, a reschedule from an unknown number that
    went straight to identity_mismatch)."""
    surname = _surname(name)
    turns = [r for r in records.thread_turns(ctx.conn, ctx.call["id"]) if r["call_id"] == ctx.call["id"]]
    for before, row in pairwise(turns):
        if row["speaker"] != "caller" or before["speaker"] != "agent":
            continue
        if not ASKS_FOR_NAME.search(before["text"]) or "?" not in before["text"]:
            continue
        if _gave_a_name(row["text"], ctx.tenant.not_names) and not (
            surname and _has_word(row["text"], surname)
        ):
            return True
    return False


def _record_mismatch(
    ctx: Ctx, customer_id: int, call_id: str, thing: str, insert: bool = False
) -> None:
    """The change is refused for the rest of the call: the check is a mismatch, the office is
    flagged, and the agent offers a callback to the number on file."""
    phone = _on_file_phone(ctx, customer_id)
    with tx(ctx.conn):
        if insert:
            ctx.conn.execute(
                "INSERT INTO identity_checks (call_id, customer_id, status, asked_seq, at, "
                "on_file_phone) VALUES (?, ?, 'mismatch', ?, ?, ?)",
                (call_id, customer_id, ctx.call["turn_seq"], iso(ctx.now), phone),
            )
        else:
            ctx.conn.execute(
                "UPDATE identity_checks SET status = 'mismatch', on_file_phone = ? "
                "WHERE call_id = ? AND customer_id = ?",
                (phone, call_id, customer_id),
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
        "caller wants it. The callback always goes to the number on file: never offer to call "
        "another number, and never say what the number on file is",
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
    """An identity check this call failed whose callback to the number on file isn't set yet. A
    check an earlier call failed doesn't send this call's callbacks there (issue #149)."""
    _settle_unanswered_check(ctx)
    return ctx.conn.execute(
        "SELECT * FROM identity_checks WHERE status = 'mismatch' AND callback_id IS NULL "
        "AND on_file_phone <> '' AND call_id = ? ORDER BY rowid DESC LIMIT 1",
        (ctx.call["id"],),
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
        address = args.address.model_copy(
            update={"street": _with_spelling(ctx, args.address.street, args.address.spelled)}
        )
        # A ZIP the caller never said is no part of the search: the model guessed 11211 for an old
        # address in 11235 and found nothing (Jackson's call 2 replayed in the eval). The street,
        # house number and unit must still match exactly, and the name check guards any change.
        zip_code = address.zip
        try:
            _zip_known(ctx, zip_code)
        except Refused:
            zip_code = None
        locations = ctx.fsm.locations_by_address(address.street, zip_code, address.unit or None)
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
    if _identity_row_status(ctx, bundle["customer_id"]) == "verified":
        return bundle
    # The name, once the caller has given it as a name; the rest only after the full check.
    named = _name_given(records.caller_text(ctx.conn, ctx.call["id"]), bundle["name"])
    return {
        **bundle,
        "name": bundle["name"] if named else "",
        "phones": [],
        # Only that there is a visit, and its id to ask for a change with, which starts the check:
        # its type, status, street and window told a caller from an unknown number about someone
        # else's visit (Retell's builder agent).
        "has_open_visit": bool(bundle["open_jobs"]),
        "open_jobs": [{"job_id": j["job_id"]} for j in bundle["open_jobs"]],
        "notes": [],
        "withheld": "the numbers, notes and anything about the visit but its id are not shown, "
        "and the name only once the caller gives it, until the server's name and address check "
        "passes; a change asks for them first",
    }


def get_customer_history(ctx: Ctx, args: HistoryIn) -> Result:
    if args.customer_id not in records.scoped_customers(ctx.conn, ctx.call["id"]):
        raise Refused("not_owner", "I can only look at the records for this call.", gate="scope")
    _limit(
        ctx,
        "get_customer_history",
        records.count_tool(ctx.conn, ctx.call["id"], "get_customer_history"),
    )
    # A customer found from what a caller said shows nothing more until the name and address
    # check passes, the same as find_customer (PR #134 review).
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])[args.customer_id]
    if scoped["matched_by"] != "caller_id" and _identity_row_status(
        ctx, args.customer_id
    ) != "verified":
        return ok(
            customer_id=args.customer_id,
            what=args.what,
            items=[],
            withheld="not shown until the server's name and address check passes",
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
    args = args.model_copy(update={"street": _with_spelling(ctx, args.street, args.spelled)})
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
    # A ZIP the caller said in the service area lets the call go on whatever the lookup can do:
    # it comes from the caller, not the lookup, and the read-back confirms the street with them.
    said = _zip_said_with(ctx, house)
    from_caller = ok(
        zip=said,
        street=args.street,
        from_caller=True,
        in_area=True,
        area=ctx.tenant.service_area[said]["name"],
        note="The lookup couldn't confirm the street; the caller's ZIP stands. Go on to the "
        "read-back, which confirms it with them.",
    ) if said in ctx.tenant.service_area else None
    # The ZIP was already asked for and the caller hasn't said one: the lookup isn't tried again,
    # so the caller is asked once (PR #139 review).
    if not said and _zip_asked(ctx, house, street):
        raise Refused(
            "zip_asked",
            "",
            need="the ZIP was asked for already; wait for the caller's answer, then call "
            "check_service_area with the ZIP they say, and don't call find_address again",
        )
    found = (
        ctx.geocoder.find(house, street, args.borough)
        if ctx.geocoder
        else geocode.Found("unavailable")
    )
    _record_lookup(ctx, house, street, args.borough, found)
    # A ZIP the caller already gave goes on when the lookup can't settle one, so nobody is asked
    # for a ZIP they just said; with several places, it picks one of them.
    if found.status == "several" and said in found.zips():
        found = geocode.Found("one", tuple(p for p in found.places if p.zip == said), found.source)
    if from_caller and found.status in ("unavailable", "none"):
        return from_caller
    if found.status == "unavailable":
        _ask_zip(ctx, "address_lookup_down", house, street)
    if found.status == "none":
        thread = records.thread(ctx.conn, ctx.call["id"])
        misses = ctx.conn.execute(
            f"SELECT COUNT(*) FROM address_lookups WHERE status = 'none' "
            f"AND call_id IN ({','.join('?' * len(thread))})",
            thread,
        ).fetchone()[0]
        if misses >= 2:
            _ask_zip(ctx, "address_not_found", house, street)
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


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _day_named(ctx: Ctx, name: str) -> date | None:
    """The date a day word means from today: "today", "tomorrow", or the next such weekday (today
    when it is one). None for anything else."""
    word = name.strip().lower()
    today = ctx.tenant.local(ctx.now).date()
    if word == "today":
        return today
    if word == "tomorrow":
        return today + timedelta(days=1)
    if word in WEEKDAYS:
        return today + timedelta(days=(WEEKDAYS.index(word) - today.weekday()) % 7)
    return None


def _named_days(ctx: Ctx, args: FindSlotsIn) -> FindSlotsIn:
    """`day` and `preferred_days` with "today", "tomorrow" and weekday names as the model passed
    them (Retell call call_3f65a9aea68c561c5c7de616f9f: day "today" was refused as no date, and
    preferred_days ["today"] heard "I don't have Today open")."""
    day = args.day
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day.strip())
        except ValueError:
            day = _day_named(ctx, day)
            if day is None:
                raise Refused(
                    "invalid_arguments",
                    "Which day works for you?",
                    errors={"day": "a date, today, tomorrow or a weekday"},
                ) from None
    preferred = []
    for name in args.preferred_days:
        word = name.strip().lower()
        if word in ("today", "tomorrow"):
            preferred.append(_day_named(ctx, word).isoformat())
        else:
            preferred.append(word)
    return args.model_copy(update={"day": day, "preferred_days": preferred})


def find_slots(ctx: Ctx, args: FindSlotsIn) -> Result:
    args = _named_days(ctx, args)
    job_type = _job_type(ctx, args.job_type)
    found = _priority(ctx, args.job_type, args.facts.inside_temp_f)
    facts, decided = found.facts, found.priority
    length = args.window_minutes
    windows = _open_windows(ctx, job_type, decided.level, length)
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
    named = _caller_named_one_time(ctx)
    if named is not None and args.day is None:
        # "Tomorrow at two PM" with no day passed: the day comes from the caller's words, never
        # today by default (PR #155 check).
        said_day = _caller_named_day(ctx)
        if said_day is not None:
            args = args.model_copy(update={"day": said_day})
    if named is not None:
        # One time the caller named ("tomorrow at two PM") is the window that holds it, not a
        # limit on when the technician may come: live call 2 passed 1 PM to 5 PM, which no 4-hour
        # window fits, and heard that tomorrow had no openings (issue #149).
        # The window starts at the half hour on or before the time, its usual length, cut short at
        # closing time: "7:30" gives 7:30 to 8 (Jackson after Retell call
        # call_9826ff9f0dfa8bc835a3601368b, who asked for 7:30 and heard "4 to 8").
        step = ctx.tenant.block_minutes
        starts = time(named.hour, named.minute - named.minute % step)
        exact = _open_windows(ctx, job_type, decided.level, length, starts)
        holding = exact or [w for w in windows if _window_holds(ctx, w, named)]
        data["arrival"] = {"time": named.strftime("%H:%M"), "fits": bool(holding)}
        if holding:
            windows = holding
        elif windows:
            # Outside the visit hours ("at 8 PM"): say the hours and offer the nearest window.
            nearest = min(windows, key=lambda w: _distance_to(ctx, w, named))
            windows = [w for w in windows if w["window"] == nearest["window"]]
            arrival_say = ctx.tenant.line("arrival_outside_hours", hours=_visit_hours(ctx)) + " "
    elif args.arrive_after or args.arrive_before:
        after, before = _arrival_hours(args)
        fitting = [w for w in windows if _window_inside(ctx, w, after, before)]
        if not fitting:
            # Narrower than a usual window ("before 9"): a shorter run of blocks that fits.
            fitting = _short_windows(ctx, job_type, decided.level, after, before)
        data["arrival"] = {
            "after": after.strftime("%H:%M") if after else None,
            "before": before.strftime("%H:%M") if before else None,
            "fits": bool(fitting),
        }
        if fitting:
            windows = fitting
        else:
            length = _minutes_said(args.window_minutes or ctx.tenant.window_minutes)
            arrival_say = ctx.tenant.line("arrival_no_fit", length=length) + " "
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
    # The voice layer speaks it from the server, word for word, before the model offers times, so
    # it is never paraphrased (asks-agent-to-call-911 kept failing `top priority`).
    urgent_said = ""
    if decided.level == "Urgent":
        line = ctx.tenant.line("urgent_priority")
        if _hand_out_once(ctx, line):
            # Kept in `say` too, so the call's record shows the system said it; the voice layer
            # strips it from what the model sees (prompts/assemble.py for_model).
            urgent_said = line + " "
            data["spoken_first"] = line
            data["note"] = "The top-priority sentence was just said for you; don't say it again."
    if decided.level == "Urgent" and windows:
        first = _describe(ctx, windows[0])
        data["first_urgent"] = first
        # No urgent window today: book the first one and flag a person to move it up (S-25).
        data["seek_earlier"] = first["day"] != "today"

    if args.day:
        on_day = [
            _describe(ctx, w) for w in _tiled(windows) if w["date"] == args.day.isoformat()
        ]
        data["windows"] = on_day
        if not on_day:
            _keep_second_job(ctx, data)
            return ok(urgent_said + arrival_say + "Nothing's open that day.", **data)
        labels = _join([w["label"] for w in on_day], "or")
        return ok(
            f"{urgent_said}{arrival_say}{on_day[0]['day'].capitalize()} I have {labels}.", **data
        )

    by_day: dict[str, list[dict]] = {}
    for window in _tiled(windows):
        by_day.setdefault(window["date"], []).append(_describe(ctx, window))
    preferred = {d.lower() for d in args.preferred_days}
    dates = sorted(by_day)
    # The shape of what's open, in date order, for the model to answer a follow-up from.
    data["days"] = [
        {
            "date": day,
            "weekday": by_day[day][0]["weekday"],
            "day": by_day[day][0]["day"],
            "windows": [s["window"] for s in by_day[day]],
            "labels": [s["label"] for s in by_day[day]],
        }
        for day in dates[:SHAPE_DAYS]
    ]
    if not dates:
        _keep_second_job(ctx, data)
        return ok(
            urgent_said + arrival_say + "I don't have anything open in the next two weeks.", **data
        )
    offer, lead = _one_offer(ctx, by_day, dates, preferred, args.time_of_day)
    data["offer"] = offer
    return ok(f"{urgent_said}{arrival_say}{lead}", **data)


def _day_said(ctx: Ctx, key: str) -> str:
    """A preferred day as said: "today", "tomorrow" or "Friday", never "Today"."""
    try:
        return ctx.tenant.day_spoken(date.fromisoformat(key), ctx.now)
    except ValueError:
        return key.capitalize()


def _one_offer(
    ctx: Ctx, by_day: dict[str, list[dict]], dates: list[str], preferred: set[str],
    time_of_day: str | None,
) -> tuple[dict, str]:
    """One simple choice to offer, and the sentence for it (Jackson after live call 2: "too many
    double booking questions... two sets of multi-options"; and "why didn't it check friday
    first?"). The caller's day comes first when they named one, else the soonest opening; within
    the day, the part of the day they asked for. A later week's day says its date, and a named day
    that is full this week says so in a few words."""
    liked = [
        d for d in dates if d in preferred or by_day[d][0]["weekday"].lower() in preferred
    ]
    day = liked[0] if liked else dates[0]
    slots = by_day[day]
    offer = next((s for s in slots if s["window"] == time_of_day), slots[0])
    visit = date.fromisoformat(day)
    today = ctx.tenant.local(ctx.now).date()
    named = offer["day_and_date"] if (visit - today).days >= 7 else offer["day"]
    lead = ""
    if preferred and not liked:
        asked = _join(sorted(_day_said(ctx, d) for d in preferred), "or")
        if all(d[:1].isdigit() for d in preferred):
            lead = f"I don't have anything open {asked}. "
        else:
            lead = f"I don't have {asked} open in the next two weeks. "
    elif liked:
        weekday = offer["weekday"]
        first = today + timedelta(days=(visit.weekday() - today.weekday()) % 7)
        if visit != first and weekday.lower() in preferred:
            lead = f"This {weekday} is full. "
    if liked:
        named = named[0].upper() + named[1:] + ("," if "," in named else "")
        return offer, f"{lead}{named} I have {offer['label']}. Does that work?"
    return offer, f"{lead}The soonest I have is {named}, {offer['label']}. Does that work?"


def _zip_asked_key(house: str, street: str) -> str:
    """Per house and street, so an address the caller corrects gets its own lookup (PR #139
    re-check: "Sorry, I'm actually at 248 Duffield Street" was stuck on the first one's ask)."""
    return f"address_ask_zip:{geocode.house_key(house)} {' '.join(geocode.street_name_words(street))}"


def _zip_asked(ctx: Ctx, house: str, street: str) -> bool:
    return bool(
        ctx.conn.execute(
            "SELECT 1 FROM lines_handed WHERE call_id = ? AND line = ?",
            (ctx.call["id"], _zip_asked_key(house, street)),
        ).fetchone()
    )


def _ask_zip(ctx: Ctx, code: str, house: str, street: str) -> None:
    """Ask the caller for the ZIP, once for each address; recorded when asked (PR #139 review)."""
    with tx(ctx.conn):
        ctx.conn.execute(
            "INSERT OR IGNORE INTO lines_handed (call_id, line, at) VALUES (?, ?, ?)",
            (ctx.call["id"], _zip_asked_key(house, street), iso(ctx.now)),
        )
    raise Refused(code, ctx.tenant.line("address_ask_zip"), need="ask for the ZIP")


def _hand_out_once(ctx: Ctx, line: str) -> bool:
    """Whether to hand out a line said once a call: not while the safety lines or the check
    question are still due (they come first), and not when it was already handed out or said.
    Recorded when it is handed out, so two calls in one caller turn can't both carry it (the
    transcript is only written after the turn)."""
    if ctx.call["safety_state"] in ("signal", "checking"):
        return False
    if records.agent_said(ctx.conn, ctx.call["id"], line):
        return False
    with tx(ctx.conn):
        inserted = ctx.conn.execute(
            "INSERT OR IGNORE INTO lines_handed (call_id, line, at) VALUES (?, ?, ?)",
            (ctx.call["id"], line, iso(ctx.now)),
        ).rowcount
    return inserted == 1


def _keep_second_job(ctx: Ctx, data: dict) -> None:
    """No opening for a second job on a call that already booked one: it is never dropped, a
    person calls back to book it (DECISIONS.md 085)."""
    booked = ctx.conn.execute(
        "SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' "
        "AND status IN ('sending', 'booked', 'queued')",
        (ctx.call["id"],),
    ).fetchone()
    if booked:
        data["need"] = (
            "a visit is already booked on this call and this second job has no opening; offer a "
            "callback to book it, and if the caller agrees, call flag_person with the job in the "
            "reason and a callback time"
        )


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


def _minutes_said(minutes: int) -> str:
    """ "two hours", "an hour", "an hour and a half", "30 minutes"."""
    hours, rest = divmod(minutes, 60)
    words = {1: "an hour", 2: "two hours", 3: "three hours", 4: "four hours"}
    if not hours:
        return f"{rest} minutes"
    said = words.get(hours, f"{hours} hours")
    return f"{said} and a half" if rest == 30 and hours == 1 else (
        said if not rest else f"{said} and {rest} minutes"
    )


def _short_windows(
    ctx: Ctx, job_type: dict, level: str, after: time | None, before: time | None
) -> list[dict]:
    """Windows inside the caller's arrival hours when no usual-length one fits: from the later of
    `after` and opening, rounded up to a block, to `before` rounded down."""
    regular = [w for w in ctx.tenant.windows if not w.after_hours]
    step = ctx.tenant.block_minutes
    lo = max(after or regular[0].start, regular[0].start)
    hi = min(before or regular[-1].end, regular[-1].end)
    lo_min = -(-(lo.hour * 60 + lo.minute) // step) * step
    hi_min = (hi.hour * 60 + hi.minute) // step * step
    if hi_min - lo_min < step:
        return []
    starts = time(lo_min // 60, lo_min % 60)
    return [
        w for w in _open_windows(ctx, job_type, level, hi_min - lo_min, starts)
        if _window_inside(ctx, w, after, before)
    ]


def _distance_to(ctx: Ctx, window: dict, moment: time) -> float:
    """Hours from a window to a time outside it."""
    spec = ctx.tenant.window(window["window"])
    if spec is None or spec.after_hours:
        return 99.0
    mins = moment.hour * 60 + moment.minute
    start, end = spec.start.hour * 60 + spec.start.minute, spec.end.hour * 60 + spec.end.minute
    return max(start - mins, mins - end, 0) / 60


def _visit_hours(ctx: Ctx) -> str:
    """ "8 AM to 8 PM", from the first and last regular arrival windows."""
    regular = [w for w in ctx.tenant.windows if not w.after_hours]
    return f"{_clock_said(regular[0].start)} to {_clock_said(regular[-1].end)}"


def _window_holds(ctx: Ctx, window: dict, moment: time) -> bool:
    spec = ctx.tenant.window(window["window"])
    return bool(spec) and not spec.after_hours and spec.start <= moment < spec.end


def _window_inside(ctx: Ctx, window: dict, after: time | None, before: time | None) -> bool:
    spec = ctx.tenant.window(window["window"])
    if spec is None or spec.after_hours:
        return False
    return (after is None or spec.start >= after) and (before is None or spec.end <= before)


# The number calling, said as a phrase and passed through as one ("the number I'm calling from" on
# Retell call call_9826ff9f0dfa8bc835a3601368b, which heard "all ten digits" for it).
CALLING_FROM = re.compile(
    r"^(?:the |this |my )?(?:number|one|phone|cell|line)?\s*(?:i'?m|i am)\s+calling\s+(?:you\s+)?"
    r"(?:from|on|with)$|^(?:this|that)(?: (?:number|one|phone|cell|line))?$|^same(?: number| one)?$",
    re.IGNORECASE,
)


def _calling_from(value: str) -> bool:
    phrase = " ".join(re.sub(r"[^\w' ]", " ", value).split())
    phrase = re.sub(r"^(?:it'?s|use|yes|yeah)\s+", "", phrase, flags=re.IGNORECASE)
    return value.lower().replace(" ", "_") == "caller_id" or bool(CALLING_FROM.match(phrase))


def _resolve_phone(ctx: Ctx, value: str) -> str:
    value = value.strip()
    if _calling_from(value):
        if len(ctx.call["caller_key"]) != 10:
            # A web call or a blocked caller ID has no number to read back (Retell's builder).
            raise Refused(
                "no_caller_number",
                "I can't see the number you're calling from. Could you read it out for me?",
                need="ask for the number, then read back with the digits the caller says",
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
    # Ten digits, or eleven with the country code. phone_key keeps the last ten, so an extra digit
    # heard anywhere else would be read back as some other number.
    said = re.sub(r"\D", "", value)
    if len(digits) != 10 or not (len(said) == 10 or (len(said) == 11 and said.startswith("1"))):
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


def _readback_corrected(ctx: Ctx, asked: dict) -> str | None:
    """The read-back field a caller line since the sentence was read gives a new value for."""
    rows = ctx.conn.execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq > ? ORDER BY seq",
        (ctx.call["id"], ctx.call["readback_asked_seq"]),
    )
    return next((f for r in rows if (f := readback_change(r["text"], asked))), None)


def _unit_without_zip(unit: str, zip_code: str) -> str:
    """A unit that ends in the ZIP keeps what comes before it: "Unit 2, 10009" heard as one number
    ("Unit 210009", Retell call call_1840576e622e3d729b760085142) was read back as unit 210009."""
    digits = re.sub(r"\D", "", zip_code or "")
    compact = re.sub(r"[\s,.-]", "", unit or "")
    if len(digits) == 5 and len(compact) > 5 and compact.endswith(digits):
        return compact[:-5]
    return unit


def read_back(ctx: Ctx, args: ReadBackIn) -> Result:
    """Two steps. Without confirmed, returns the exact sentence to read and records that it was
    asked. With confirmed, accepts only if that same sentence was returned on this call and a caller
    line followed it (spec S-26)."""
    phone = _resolve_phone(ctx, args.phone)
    # One unit a read-back: two units are two jobs, each read back and booked on its own
    # (DECISIONS.md 085; eval run 20261104-200546 read back "unit 2A and 5C" as one).
    if re.search(r"\band\b|[,&+/]", args.address.unit or ""):
        raise Refused(
            "invalid_arguments",
            "",
            need="one unit per read-back: read back and book the first unit, then the second",
            errors={"unit": "one unit"},
        )
    _zip_known(ctx, args.address.zip)
    spelled_street = _with_spelling(ctx, args.address.street, args.address.spelled)
    _zip_mismatch(ctx, args.address.zip, spelled_street, args.address.city)
    address = args.address.model_dump()
    address["street"] = spelled_street
    address["spelled"] = ""
    address["unit"] = _unit_without_zip(address["unit"], address["zip"])
    place = address["street"] + (f", unit {address['unit']}" if address["unit"] else "")
    city = f", {address['city']}" if address["city"] else ""
    # The sentence names the address as a home, so every booking has the caller confirm it's
    # residential (assignment; spec S-07). A caller who corrects it is caught by the turn check.
    # The ZIP and the number are written out digit by digit, as the caller should hear them, the ZIP
    # after the words "ZIP code" so a unit before it doesn't run into it ("unit two one zero zero
    # zero nine" on Retell call call_1840576e622e3d729b760085142; Jackson in call 2: "just say the
    # zip code is").
    spoken_address = f"{place}{city}, ZIP code {digits_spoken(address['zip'])}"
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
    answered = args.confirmed and same and records.caller_turn_after(
        ctx.conn, ctx.call["id"], ctx.call["readback_asked_seq"]
    )
    # "Yes, but it's 4B" in the same breath as the yes: the caller changed a detail, so this
    # sentence can't be confirmed (DECISIONS.md 083).
    corrected = _readback_corrected(ctx, asked) if answered else None
    if corrected:
        raise Refused(
            "gate_read_back",
            "",
            need=f"the caller changed the {corrected} in their answer; call read_back with the "
            "corrected details and read the new sentence",
            changed=corrected,
        )
    if answered:
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
                "with new_address_account caller for a yes, or own for a no (with details.name of "
                "whoever the address's account is for)",
            )
        # A caller with no account who hasn't said a name: the name is asked here, right after
        # the read-back, not by a refused booking after "booking that now" (a scripted call on
        # #159, call-_+16466240418_bUwDUCZFrKN8).
        if not _callers_accounts(ctx) and not _caller_named_anyone(ctx):
            return ok(
                ctx.tenant.line("booking_name"),
                confirmed=True,
                address=address,
                phone=phone,
                need="this question was said for you; pass the caller's answer as details.name "
                "when you book",
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
        if _check_failed_on_call(ctx):
            # The new address can't go on the account from this number, so the visit can't be
            # booked there: the caller hears why in plain words, not a callback with no reason
            # (live call 2, issue #149: "The address change still has to go through first").
            raise Refused(
                "move_unverified",
                ctx.tenant.line("move_unverified"),
                need="the visit can't be booked at the new address on this call; offer the "
                "callback to the number on file with flag_person if the caller wants it",
            )
        raise Refused(
            "move_first",
            "",
            need="the caller moved: call record_move first, then book if they still need a visit",
        )
    _out_of_area(ctx, readback["zip"])
    chosen = _account_named(ctx, readback)
    _second_job(ctx, existing, readback, args)
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
    scoped = _callers_accounts(ctx)
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
    # An Urgent call booked without find_slots first still hears the line once, here.
    urgent_line = ctx.tenant.line("urgent_priority")
    if decided.level == "Urgent" and _hand_out_once(ctx, urgent_line):
        say = f"{urgent_line} {say}"
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
    # A unit the caller named that has no booking yet comes next instead, so the second job isn't
    # lost to a goodbye (DECISIONS.md 085; eval run 20261104-201025, super-two-units).
    next_unit = _unit_not_booked(ctx)
    if next_unit:
        say += " " + ctx.tenant.line("next_unit", unit=next_unit)
    else:
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


def _second_job(ctx: Ctx, existing: list[sqlite3.Row], readback: dict, args: BookIn) -> None:
    """A call may book two jobs (DECISIONS.md 085): "my heat's out, and can you tune up the AC?",
    or a super with units 2A and 5C. Each has its own read-back where the address or unit differs
    (a new unit clears the confirmed read-back, app/readback.py). At the same address the second
    has to be a different kind of visit the caller asked for, so a retry or a slip never books the
    same visit twice."""
    here = [
        row
        for row in existing
        if _readback_key({**json.loads(row["request_json"])["address"], "phone": ""})
        == _readback_key({**readback, "phone": ""})
    ]
    if not here:
        return
    if any(row["job_type"] == args.job_type for row in here):
        raise Refused(
            "already_booked",
            "",
            note="That visit is already booked on this call; say nothing more about booking it.",
            booking_id=here[0]["fsm_job_id"],
        )
    if not args.separate_problem:
        raise Refused(
            "second_job",
            "",
            need="a visit is already booked at this address on this call; book this one with "
            "separate_problem true only if the caller asked for a second, different job; to change "
            "the first, use reschedule_appointment",
        )


def _unit_not_booked(ctx: Ctx) -> str | None:
    """A unit the caller named in a line that named two or more, with no booking on this call,
    once at least one booking was made there."""
    booked = {
        unit_key(json.loads(r["request_json"])["address"]["unit"] or "")
        for r in ctx.conn.execute(
            "SELECT request_json FROM bookings WHERE call_id = ? AND kind = 'book' "
            "AND status IN ('booked', 'queued')",
            (ctx.call["id"],),
        )
    }
    for row in ctx.conn.execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' ORDER BY seq",
        (ctx.call["id"],),
    ):
        named = units_said(row["text"])
        if len(named) > 1 and booked & set(named):
            left = [u for u in named if u not in booked]
            if left:
                return left[0].upper()
    return None


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
                    _passed_visit(ctx, job)
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


def _passed_visit(ctx: Ctx, job: dict) -> None:
    """An open visit whose arrival window ended before now is not one to move or keep as booked:
    the caller is asked whether the technician came (a test call at 2 PM heard "you're all set for
    today between eight and twelve")."""
    live = [a for a in ctx.fsm.appointments(job["id"]) if a["status"] != "Canceled"]
    if not live:
        return
    window = lookup.window_text(
        ctx.tenant, live[-1]["arrivalWindowStart"], live[-1]["arrivalWindowEnd"], ctx.now
    )
    if not window["passed"]:
        return
    visit = date.fromisoformat(window["date"])
    yesterday = ctx.tenant.local(ctx.now).date() - timedelta(days=1)
    day = "yesterday" if visit == yesterday else ctx.tenant.day_spoken(visit, ctx.now)
    between = window["label"].replace(" to ", " and ")
    raise Refused(
        "open_job_passed",
        ctx.tenant.line("visit_passed", day=day, between=between),
        job_id=job["id"],
        window=window,
        use="when the caller answers, reschedule_appointment with this job_id as booking_id for a "
        "new time, or flag_person so the team checks what happened; book_appointment with "
        "separate_problem true only for a different problem",
    )


def _account_phone(ctx: Ctx, chosen: int | None) -> str:
    """The account's own number, for a booking whose number reaches someone else: the one on file
    for the one customer matched, otherwise the number the call came from."""
    scoped = [chosen] if chosen else list(_callers_accounts(ctx))
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
        for cid, row in _callers_accounts(ctx).items()
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
            need="ask, then book_appointment with new_address_account caller for a yes, or own "
            "for a no (with details.name of whoever the address's account is for)",
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
            key,
            job["id"],
            ctx.tenant.fsm["cancel_reason_id"],
            f"Cancelled by phone: {args.reason.strip() or 'no reason given'}",
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
# "Moved from" says a move only with a street address in the same line ("I moved from 142 Main
# Street"), so "we moved it from the basement to the attic" doesn't (PR #155 re-check).
MOVED_FROM_PHRASES = ("moved from", "moving from", "recently moved from", "moved here from")


def _said_moved(ctx: Ctx) -> bool:
    caller = words(records.caller_text(ctx.conn, ctx.call["id"]))
    if any(has_phrase(caller, phrase) for phrase in MOVED_PHRASES):
        return True
    lines = [
        r["text"] for r in records.thread_turns(ctx.conn, ctx.call["id"]) if r["speaker"] == "caller"
    ]
    return any(
        _gave_an_address(line) and any(has_phrase(words(line), p) for p in MOVED_FROM_PHRASES)
        for line in lines
    )


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
        # A caller from a number not on the account: their account is found by the old address
        # first (Jackson's call 2 replayed in the eval, where the agent told the caller it couldn't
        # find the account and gave up).
        raise Refused(
            "not_owner", "", gate="scope", errors={"customer_id": "not matched on this call"},
            need="find the caller's account first: call find_customer with the old address they "
            "moved from, then call record_move again; never tell the caller you can't find it "
            "before you have looked",
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


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _days_ahead(tenant: Tenant, now: datetime, day: str) -> int:
    """How many days from today the caller's day is: a weekday is the next one coming, today
    itself when it is today (issue #149: "Friday at two" was booked for tomorrow)."""
    if day == "tomorrow":
        return 1
    if day in WEEKDAYS:
        return (WEEKDAYS.index(day) - tenant.local(now).weekday()) % 7
    return 0


def _on_day(tenant: Tenant, moment: datetime, now: datetime, spoken: str) -> str:
    """The day in front of a callback time: "tomorrow between 1 PM and 3 PM", "Friday around 2
    PM". Plain, with no date and no count of days."""
    days = (tenant.local(moment).date() - tenant.local(now).date()).days
    if days <= 0:
        return spoken
    day = "tomorrow" if days == 1 else tenant.local(moment).strftime("%A")
    return f"{day} {spoken}"


def _nearest_in_hours(tenant: Tenant, moment: datetime, now: datetime) -> datetime:
    """The calling-hours time closest to one outside them, on the same day: when calls start, or
    half an hour before they stop (records.in_outbound_hours ends before `closes`)."""
    opens, closes = (time.fromisoformat(t) for t in tenant.callbacks["outbound_hours"])
    local = tenant.local(moment)
    if local.time() < opens:
        nearest = datetime.combine(local.date(), opens, tenant.tz)
    else:
        nearest = datetime.combine(local.date(), closes, tenant.tz) - timedelta(minutes=30)
    return max(nearest, now)


def _asked_moment(tenant: Tenant, now: datetime, days: int, asked: CallbackTime) -> datetime:
    """The moment the caller asked for, from an ISO time with an offset or a clock time on the
    day asked (PR #155 review: an ISO time outside calling hours was read as a clock time and
    failed)."""
    value = asked.callback_time or asked.callback_after or asked.callback_before or ""
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return records.clock_on(value, tenant, now, days)
    return moment if moment.tzinfo else moment.replace(tzinfo=tenant.tz)


def _local_iso(tenant: Tenant, asked: CallbackTime) -> CallbackTime:
    """An ISO callback time with no offset is the company's local time, so it gets the same
    checks, and the nearest allowed time, as any other (PR #155 re-check: it was refused as
    "an ISO time needs a UTC offset")."""
    value = (asked.callback_time or "").strip()
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return asked
    if moment.tzinfo is not None or "T" not in value:
        return asked
    return asked.model_copy(update={"callback_time": moment.replace(tzinfo=tenant.tz).isoformat()})


# Words that make a caller's line a window, not one time: "between 1 and 3", "after 2", "2 to 4".
# A time the caller named, after "at" or "for", or with AM, PM or minutes: "at 2", "2:30", "2 PM".
NAMED_TIME = re.compile(
    r"(?:\b(?:at|for)\s+)?\b(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(am|pm|a|p)?\b(?!\s*:)"
)
# Two times as a range ("between 1 and 3", "2 to 4", "1 till 3") or a time with a limit word ("after
# 2", "before noon", "by 4"): a window, not one time.
WINDOW_SAID = re.compile(
    r"\bbetween\b[^.?!]*\d[^.?!]*\band\b[^.?!]*\d"
    r"|\d{1,2}(?:\s*:\s*\d{2})?\s*(?:am|pm|a|p)?\s*(?:to|til|till|until|-)\s*\d"
    r"|\b(?:after|before|by|until|till|not before|no later than)\s+\d"
)
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _caller_line_digits(ctx: Ctx) -> str:
    """The caller's last line with spoken numbers as digits ("two thirty" as "2 30")."""
    line = records.last_caller_line(ctx.conn, ctx.call["id"])
    return " ".join(t for t in number_tokens(line.replace("noon", "12 pm")) if t)


def _caller_named_one_time(ctx: Ctx) -> time | None:
    """The one clock time in the caller's last line, minutes kept ("tomorrow at 2:30"), when it
    names exactly one and no window. "To", "and" and "or" alone are no window ("I'd like a call
    tomorrow at 2", PR #155 check); only two times as a range, or a limit word, are."""
    text = _caller_line_digits(ctx)
    if WINDOW_SAID.search(text):
        return None
    found = set()
    for match in NAMED_TIME.finditer(text):
        hour, minute, meridiem = match[1], match[2], match[3]
        lead = match[0].lstrip()[:3] in ("at ", "for")
        if not (lead or minute or meridiem):
            continue
        value = hour + (f":{minute}" if minute else "") + (
            {"am": " AM", "a": " AM", "pm": " PM", "p": " PM"}.get(meridiem or "", "")
        )
        try:
            found.add(clock_time(value))
        except ValueError:
            continue
    return found.pop() if len(found) == 1 else None


def _caller_named_day(ctx: Ctx) -> date | None:
    """The day in the caller's last line: "today", "tomorrow" or a weekday, the next one coming."""
    said = words(records.last_caller_line(ctx.conn, ctx.call["id"]))
    today = ctx.tenant.local(ctx.now).date()
    if " tomorrow " in said:
        return today + timedelta(days=1)
    if " today " in said or " tonight " in said:
        return today
    for i, name in enumerate(WEEKDAY_NAMES):
        if f" {name} " in said:
            return today + timedelta(days=(i - today.weekday()) % 7)
    return None


def _clock_said(moment: time) -> str:
    """ "2 PM", "2:30 PM", "noon"."""
    if moment == time(12, 0):
        return "noon"
    return datetime.combine(date.today(), moment).strftime("%-I:%M %p").replace(":00 ", " ")


def _as_the_caller_said_it(ctx: Ctx, asked: CallbackTime) -> CallbackTime:
    """A window the model built around the one time the caller named is that time: "Tomorrow at
    two PM" was passed as 1 PM to 3 PM and said back as "between 1 PM and 3 PM, not at 2 PM
    exactly" (live call 2, issue #149)."""
    if not (asked.callback_after and asked.callback_before) or asked.callback_time:
        return asked
    named = _caller_named_one_time(ctx)
    if named is None:
        return asked
    try:
        start, end = clock_time(asked.callback_after), clock_time(asked.callback_before)
    except ValueError:
        return asked
    if not start <= named <= end:
        return asked
    spoken = datetime.combine(date.today(), named).strftime("%-I:%M %p")
    return asked.model_copy(
        update={"callback_after": None, "callback_before": None, "callback_time": spoken}
    )


def _callback_due(ctx: Ctx, asked: CallbackTime) -> CallbackWhen:
    """The callback time and the words for it. The words always come from here, never the model."""
    tenant, now = ctx.tenant, ctx.now
    asked = _local_iso(tenant, _as_the_caller_said_it(ctx, asked))
    days = _days_ahead(tenant, now, asked.callback_day)
    try:
        if asked.callback_after or asked.callback_before:
            earliest, latest = records.parse_callback_window(
                asked.callback_after, asked.callback_before, tenant, now, days
            )
        else:
            due = records.parse_callback_time(
                asked.callback_time, asked.callback_in_minutes, tenant, now, days
            )
            if asked.callback_in_minutes is not None:
                spoken = tenant.line("callback_by", time=tenant.time_spoken(due, now))
                return CallbackWhen(due, due, spoken)
            # A time the caller named is said back as theirs ("Friday around 2 PM"), and kept
            # inside calling hours.
            if not records.in_outbound_hours(tenant, due):
                raise ValueError("outside callback hours")
            clock = _clock_said(tenant.local(due).time())
            spoken = _on_day(tenant, due, now, tenant.line("callback_at", time=clock))
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
            asked_at = _asked_moment(tenant, now, days, asked)
            nearest = tenant.time_spoken(_nearest_in_hours(tenant, asked_at, now), now)
            say = tenant.line(
                "callback_outside_hours", opens=opens, closes=closes, nearest=nearest
            )
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
    spoken = _on_day(tenant, earliest or latest, now, spoken)
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


def attach_late_job_notes(ctx: Ctx) -> None:
    """Job notes the server made after the job was booked on this call (a technician asked for by
    name, app/asks.py) go onto that job now; notes made before it went into its summary when it
    was booked. A failed write leaves the note for the technician summary, which reads every note
    on the call."""
    job = ctx.conn.execute(
        "SELECT fsm_job_id, customer_id, request_json FROM bookings WHERE call_id = ? "
        "AND kind = 'book' AND status = 'booked' ORDER BY id DESC LIMIT 1",
        (ctx.call["id"],),
    ).fetchone()
    if not job:
        return
    sent = set(json.loads(job["request_json"]).get("notes", []))
    for note in ctx.conn.execute(
        "SELECT id, text FROM notes WHERE call_id = ? AND attach_to = 'job' AND fsm_note_id IS NULL",
        (ctx.call["id"],),
    ).fetchall():
        if note["text"] in sent:
            continue
        try:
            made = ctx.fsm.add_note(
                f"{ctx.call['id']}:job-note:{note['id']}",
                job["customer_id"],
                note["text"],
                job["fsm_job_id"],
            )
        except (FsmUnavailable, FsmRejected):
            return
        with tx(ctx.conn):
            ctx.conn.execute(
                "UPDATE notes SET fsm_note_id = ?, customer_id = ? WHERE id = ?",
                (made.get("id"), job["customer_id"], note["id"]),
            )


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
    if args.reason != "caller_goodbye" and _out_of_area_unanswered(ctx) and not says_goodbye(
        records.last_caller_line(ctx.conn, ctx.call["id"])
    ):
        # Jackson's call on the Retell number: end_call right after the out-of-area answer, so a
        # caller who gave a wrong address never got to correct it.
        raise Refused(
            "answer_first",
            "",
            need="the caller just heard their address is outside the area: don't hang up. Ask "
            "whether there's another address to check, and wait for their answer",
        )
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
