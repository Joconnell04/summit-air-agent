"""The tools the model can call (docs/tools.md). Each handler checks its gates in server code and
returns a Result or raises Refused; app.main logs every call.

The model asks; the server decides. A gate here is never a flag the model sets: read-back counts
only after the server returned the sentence and a caller line followed, the safety gate reads the
call's own state, and ownership is checked against the customers matched on this call.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from . import booking, lookup, outbound, records, safety, weather
from . import capacity as capacity_rules
from . import priority as prio
from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .lifecycle import approval_unclear
from .schemas import (
    BookIn,
    CallbackTime,
    CancelIn,
    DoNotCallIn,
    EndCallIn,
    FindCustomerIn,
    FindSlotsIn,
    FlagIn,
    HistoryIn,
    LeadIn,
    NoteIn,
    ReadBackIn,
    RescheduleIn,
    ServiceAreaIn,
    Slot,
)
from .settings import BOOKING_DAYS_AHEAD, LIMITS
from .tenant import Tenant
from .text import clock_time, has_phrase, phone_key, phone_spoken, street_key, unit_key, words

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


def _out_of_area(ctx: Ctx, zip_code: str) -> None:
    if zip_code not in ctx.tenant.service_area:
        _record_out_of_area(ctx, zip_code)
        raise Refused("out_of_area", ctx.tenant.line("out_of_area"), zip=zip_code)


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


def _priority(ctx: Ctx, job_type: str, extra_text: str, inside_temp_f: float | None):
    outside = weather.current(ctx.conn, ctx.tenant, ctx.now)
    caller_text = records.caller_text(ctx.conn, ctx.call["id"])
    facts = prio.Facts(
        job_type,
        caller_text + "\n" + extra_text,
        inside_temp_f,
        ctx.call["safety_state"] in ("signal", "lines_said"),
        outside.now_f,
        outside.coldest_f,
        heating_indoors=safety.heating_signal(caller_text, ctx.tenant.safety),
        caller_text=caller_text,
    )
    return facts, prio.decide(facts, ctx.tenant.priority, ctx.side_model)


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
    local = ctx.tenant.local(parse_iso(window["start"]))
    spec = ctx.tenant.window(window["window"])
    today = ctx.tenant.local(ctx.now).date()
    day_word = (
        "today"
        if local.date() == today
        else "tomorrow"
        if local.date() == today + timedelta(days=1)
        else local.strftime("%A")
    )
    return {
        "date": window["date"],
        "window": window["window"],
        "weekday": local.strftime("%A"),
        "day": day_word,
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


# ---- tools ---------------------------------------------------------------------------------


def find_customer(ctx: Ctx, args: FindCustomerIn) -> Result:
    _limit(ctx, "find_customer", records.count_tool(ctx.conn, ctx.call["id"], "find_customer"))
    needs_unit = False
    if args.phone:
        if len(phone_key(args.phone)) != 10:
            raise Refused("invalid_arguments", "Could I get that number again, all ten digits?")
        customers = ctx.fsm.customers_by_phone(args.phone)
        matched_by = "phone"
    else:
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
    return ok(matches=bundles, needs_unit=needs_unit)


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
    _out_of_area(ctx, args.zip)
    zone = ctx.tenant.service_area[args.zip]
    return ok(zip=args.zip, in_area=True, area=zone["name"])


def find_slots(ctx: Ctx, args: FindSlotsIn) -> Result:
    job_type = _job_type(ctx, args.job_type)
    facts, decided = _priority(
        ctx, args.job_type, f"{args.facts.issue}\n{args.facts.who_home}", args.facts.inside_temp_f
    )
    windows = _open_windows(ctx, job_type, decided.level)
    priority = {"level": decided.level, "reasons": decided.reasons, "requested": args.priority}
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
    return (street_key(value["street"]), unit_key(value["unit"]), value["zip"], value["phone"])


def read_back(ctx: Ctx, args: ReadBackIn) -> Result:
    """Two steps. Without confirmed, returns the exact sentence to read and records that it was
    asked. With confirmed, accepts only if that same sentence was returned on this call and a caller
    line followed it (spec S-26)."""
    phone = _resolve_phone(ctx, args.phone)
    address = args.address.model_dump()
    place = address["street"] + (f", unit {address['unit']}" if address["unit"] else "")
    city = f", {address['city']}" if address["city"] else ""
    # The sentence names the address as a home, so every booking has the caller confirm it's
    # residential (assignment; spec S-07). A caller who corrects it is caught by the turn check.
    sentence = ctx.tenant.line(
        "read_back",
        address=f"{place}{city}, {address['zip']}",
        phone=phone_spoken(phone),
    )
    asked = records.readback(ctx.call)
    current = {**address, "phone": phone, "sentence": sentence}
    same = asked is not None and _readback_key(asked) == _readback_key(current)
    if (
        args.confirmed
        and same
        and records.caller_turn_after(ctx.conn, ctx.call["id"], ctx.call["readback_asked_seq"])
    ):
        with tx(ctx.conn):
            records.update_call(ctx.conn, ctx.call["id"], readback_confirmed_at=iso(ctx.now))
        return ok(confirmed=True, address=address, phone=phone)
    if not (args.confirmed and same):
        with tx(ctx.conn):
            records.update_call(
                ctx.conn,
                ctx.call["id"],
                readback_json=json.dumps(current),
                readback_asked_seq=ctx.call["turn_seq"],
                readback_confirmed_at=None,
            )
    if args.confirmed:
        raise Refused(
            "gate_read_back",
            sentence,
            need="read the sentence to the caller and let them answer first",
            sentence=sentence,
        )
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
    _out_of_area(ctx, readback["zip"])
    if not args.separate_problem:
        _open_job_here(ctx, readback)
    account, contact_name = _new_address_account(ctx, readback, args)
    job_type = _job_type(ctx, args.job_type)
    details = args.details.model_dump()
    facts, decided = _priority(
        ctx, args.job_type, f"{details['issue']}\n{details['who_home']}", details["inside_temp_f"]
    )
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
        "scoped_customer_ids": [
            cid
            for cid, row in scoped.items()
            if row["customer_type"] not in ctx.tenant.rejected_customer_types
        ]
        or list(scoped),
        "priority": decided.level,
        "priority_reasons": decided.reasons,
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
        f"You're all set for {when['day']}, with the technician arriving between "
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
    result = ok(
        say,
        booking_id=sent["job_id"],
        appointment_id=sent["appointment_id"],
        window=when,
        priority={"level": decided.level, "reasons": decided.reasons},
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


def _new_address_account(
    ctx: Ctx, readback: dict, args: BookIn
) -> tuple[str | None, str | None]:
    """A caller on file booking an address that isn't on file anywhere is asked whose account it
    goes on before it books: its own account with the caller as a contact (a parent's house, the
    usual case), or the caller's (eval round 2, seed 5). Returns the choice and, for its own
    account, the caller's name for the contact. A lookup that fails leaves the booking to go ahead
    under the caller, as before."""
    scoped = [
        (cid, row)
        for cid, row in records.scoped_customers(ctx.conn, ctx.call["id"]).items()
        if row["customer_type"] not in ctx.tenant.rejected_customer_types
    ]
    if len(scoped) != 1:
        return None, None
    customer_id = scoped[0][0]
    try:
        if any(booking.same_address(loc, readback) for loc in ctx.fsm.locations(customer_id)):
            return None, None
        on_file = ctx.fsm.locations_by_address(
            readback["street"], readback["zip"], readback["unit"] or None
        )
        if any(booking.same_address(loc, readback) for loc in on_file):
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


def _owned_job(ctx: Ctx, job_id: int) -> dict:
    """The job, if it was booked on this thread, belongs to a customer matched by the number the
    call came from, or is at the address the caller confirmed in the read-back. A number or address
    the caller only states is not enough to change someone's visit."""
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
    owned = bool(booked_here)
    if job and not owned and job["customerId"] in scoped:
        owned = scoped[job["customerId"]]["matched_by"] == "caller_id"
        readback = records.readback(ctx.call) if ctx.call["readback_confirmed_at"] else None
        if not owned and readback:
            owned = any(
                loc["id"] == job["locationId"] and booking.same_address(loc, readback)
                for loc in ctx.fsm.locations(job["customerId"])
            )
    if not job or not owned:
        raise Refused(
            "not_owner", "I can only change a visit that's booked for you.", gate="ownership"
        )
    return job


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
    job = _owned_job(ctx, args.booking_id)
    appointments = [a for a in ctx.fsm.appointments(job["id"]) if a["status"] == "Scheduled"]
    if job["jobStatus"] != "Scheduled" or not appointments:
        raise Refused(
            "invalid_state",
            "That visit is already underway, so let me get someone to help with it.",
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
        f"Done, I've moved it to {when['day']} between {between}.",
        booking_id=job["id"],
        window=when,
    )


def cancel_appointment(ctx: Ctx, args: CancelIn) -> Result:
    """Cancels, and returns two windows the agent can offer instead: a cancel is often a rebooking
    (journey 7)."""
    _safety_gate(ctx)
    _limit(ctx, "cancel", records.count_actions(ctx.conn, ctx.call["id"], "cancel"))
    job = _owned_job(ctx, args.booking_id)
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
    job_type = ctx.tenant.job_types.get(ctx.tenant.job_type_codes.get(job["jobTypeId"], ""))
    alternatives = _alternatives(ctx, job_type, job["priority"]) if job_type else []
    return ok(
        "That visit is cancelled.",
        booking_id=job["id"],
        reason=args.reason,
        alternatives=alternatives,
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

    if earliest and latest:
        spoken = tenant.line("callback_between", start=clock(earliest), end=clock(latest))
    elif earliest:
        spoken = tenant.line("callback_after", time=clock(earliest))
    else:
        spoken = tenant.line("callback_before", time=clock(latest))
    if days:
        spoken = f"tomorrow {spoken}"
    day = tenant.local(now).date() + timedelta(days=days)
    opens, closes = (
        datetime.combine(day, time.fromisoformat(t), tenant.tz)
        for t in tenant.callbacks["outbound_hours"]
    )
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


def flag_person(ctx: Ctx, args: FlagIn) -> Result:
    """The sentence comes back only after the flag and the callback are stored (spec S-23)."""
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
        if ctx.call["after_hours"] and args.priority == "Urgent":
            minutes = ctx.tenant.after_hours_cutoffs["on_call_confirms_within_minutes"]
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
    with tx(ctx.conn):
        flag_id = records.add_flag(
            ctx.conn, ctx.call["id"], kind, args.reason, ctx.now, args.priority
        )
        callback_id = records.add_callback(
            ctx.conn, ctx.tenant, ctx.call, due, _with_window(args.reason, args, when), ctx.now
        )
        if callback_id and system:
            outbound.add_callback_dial(
                ctx.conn, ctx.tenant, ctx.call, callback_id, when.dial_at, ctx.now
            )
    spoken = when.spoken
    if callback_id is None:
        say = ctx.tenant.line("flag_stored_no_callback")
    elif args.priority == "Urgent":
        say = ctx.tenant.line("flag_stored_urgent", when=spoken)
    else:
        say = ctx.tenant.line("flag_stored", when=spoken)
    return ok(
        say, flag_id=flag_id, callback_id=callback_id, callback_at=iso(due), callback_spoken=spoken
    )


def record_lead(ctx: Ctx, args: LeadIn) -> Result:
    """One write, no extra lookups (spec S-08). A commercial lead marks the call, so it can never
    become a booking."""
    details = args.details
    due = when = None
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
        # A caller saying goodbye after the sign-off doesn't hear it a second time.
        if hang_up and records.agent_said(ctx.conn, ctx.call["id"], sign_off):
            return ok("", hang_up=True)
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
    "find_slots": (find_slots, FindSlotsIn, False),
    "read_back": (read_back, ReadBackIn, False),
    "book_appointment": (book_appointment, BookIn, True),
    "reschedule_appointment": (reschedule_appointment, RescheduleIn, True),
    "cancel_appointment": (cancel_appointment, CancelIn, True),
    "flag_person": (flag_person, FlagIn, True),
    "record_lead": (record_lead, LeadIn, True),
    "add_note": (add_note, NoteIn, True),
    "mark_do_not_call": (mark_do_not_call, DoNotCallIn, True),
    "end_call": (end_call, EndCallIn, True),
}
