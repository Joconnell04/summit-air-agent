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
from datetime import datetime, timedelta

from . import booking, lookup, outbound, records
from . import priority as prio
from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable, SlotCache
from .schemas import (
    BookIn,
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
from .text import phone_key, phone_spoken, street_key, unit_key

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
    """Booking tools wait until the safety lines were said and a person flagged (spec S-18).
    Returns True when this is a safety call past that point, so the action is an override."""
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
    facts = prio.Facts(
        job_type,
        records.caller_text(ctx.conn, ctx.call["id"]) + "\n" + extra_text,
        inside_temp_f,
        ctx.call["safety_state"] in ("signal", "lines_said"),
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
    zip_code: str | None,
) -> list[dict]:
    """Open windows from now to the end of the booking range, earliest first, via the cache. Also
    used by the job that offers earlier openings, so both read the same cached capacity."""
    today = tenant.local(now).date()
    end_day = today + timedelta(days=BOOKING_DAYS_AHEAD)
    unit = job_type["businessUnitIds"][0]

    def fetch():
        return fsm.capacity(
            now,
            datetime.combine(end_day, datetime.max.time(), tenant.tz),
            unit,
            job_type["id"],
            level,
            zip_code,
        )

    days = cache.get((tenant.slug, job_type["code"], level, zip_code, today), fetch)
    found = []
    for day in days:
        for slot in day["timeslots"]:
            if slot["isAvailable"]:
                found.append(
                    {
                        "date": day["date"],
                        "window": slot["windowId"],
                        "after_hours": slot["afterHours"],
                        "start": slot["start"],
                        "end": slot["end"],
                    }
                )
    return sorted(found, key=lambda w: w["start"])


def _open_windows(ctx: Ctx, job_type: dict, level: str, zip_code: str | None) -> list[dict]:
    return open_windows(ctx.tenant, ctx.fsm, ctx.cache, ctx.now, job_type, level, zip_code)


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
        windows = _open_windows(ctx, job_type, level, None)
    except (FsmUnavailable, FsmRejected):
        return []
    return [_describe(ctx, w) for w in windows[:n]]


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
    readback = records.readback(ctx.call)
    windows = _open_windows(ctx, job_type, decided.level, readback["zip"] if readback else None)
    priority = {"level": decided.level, "reasons": decided.reasons, "requested": args.priority}
    data: dict = {"priority": priority}

    cutoffs = ctx.tenant.after_hours_cutoffs
    if ctx.call["after_hours"] and args.job_type in (
        cutoffs["heating_job_types"] + cutoffs["cooling_job_types"]
    ):
        data["after_hours_outcome"] = prio.after_hours_outcome(
            facts, decided, cutoffs, args.facts.temp_falling_fast
        )
    if decided.level == "Urgent" and windows:
        first = _describe(ctx, windows[0])
        data["first_urgent"] = first
        # No urgent window today: book the first one and flag a person to move it up (S-25).
        data["seek_earlier"] = first["day"] != "today"

    if args.day:
        on_day = [_describe(ctx, w) for w in windows if w["date"] == args.day.isoformat()]
        data["windows"] = on_day
        if not on_day:
            return ok("Nothing's open that day.", **data)
        labels = _join([w["label"] for w in on_day], "or")
        return ok(f"{on_day[0]['day'].capitalize()} I have {labels}.", **data)

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
        return ok("I don't have anything open in the next two weeks.", **data)
    parts = []
    for d in shape:
        names = _join([w.replace("_", " ") for w in d["windows"]], "or")
        parts.append(f"this {names}" if d["day"] == "today" else f"{d['day']} {names}")
    return ok(f"I've got openings {_join(parts, 'and')}.", **data)


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
            for contact in ctx.fsm.customer(customer_id)["contacts"]:
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
    job_type = _job_type(ctx, args.job_type)
    details = args.details.model_dump()
    _, decided = _priority(
        ctx, args.job_type, f"{details['issue']}\n{details['who_home']}", details["inside_temp_f"]
    )
    window_start, window_end = _slot_bounds(ctx, args.slot, decided.level)
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
        if exc.code in ("slot_taken", "no_capacity", "slot_in_past", "after_hours_urgent_only"):
            ctx.cache.invalidate()
            alternatives = _alternatives(ctx, job_type, decided.level)
            raise Refused(
                "slot_taken", ctx.tenant.line("slot_taken"), alternatives=alternatives
            ) from None
        if exc.code == "customer_type_not_supported":
            raise Refused("customer_type", ctx.tenant.line("commercial")) from None
        if exc.code == "out_of_area":
            _record_out_of_area(ctx, readback["zip"])
            raise Refused("out_of_area", ctx.tenant.line("out_of_area")) from None
        callback_id, due = _callback(
            ctx, "fsm_down_minutes", f"Booking refused by the FSM: {exc.code}"
        )
        with tx(ctx.conn):
            records.add_flag(ctx.conn, ctx.call["id"], "booking_failed", str(exc), ctx.now, "High")
        raise Refused("fsm_rejected", _fsm_down_say(ctx, callback_id, due)) from None

    ctx.cache.invalidate()
    with tx(ctx.conn):
        booking.mark_booked(ctx.conn, booking_id, sent, ctx.now)
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
    result = ok(
        f"You're all set for {when['day']}, with the technician arriving between "
        f"{when['label'].replace(' to ', ' and ')}.",
        booking_id=sent["job_id"],
        appointment_id=sent["appointment_id"],
        window=when,
        priority={"level": decided.level, "reasons": decided.reasons},
        seek_earlier=seek_earlier,
        safety_override=override,
    )
    if override:
        result.gate = "safety_override"
    return result


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
    key = _attempt_key(ctx, "reschedule", args.model_dump(mode="json"))
    row_id = _record_action(ctx, "reschedule", key, job, args.model_dump(mode="json"), args.reason)
    try:
        ctx.fsm.reschedule(key, appointments[-1]["id"], window_start, window_end)
    except FsmRejected as exc:
        if exc.code in ("slot_taken", "no_capacity"):
            with tx(ctx.conn):
                booking.mark_attempt(ctx.conn, row_id, "failed", exc.code, ctx.now)
            ctx.cache.invalidate()
            job_type = ctx.tenant.job_types[ctx.tenant.job_type_codes[job["jobTypeId"]]]
            raise Refused(
                "slot_taken",
                ctx.tenant.line("slot_taken"),
                alternatives=_alternatives(ctx, job_type, job["priority"]),
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


def _callback_due(ctx: Ctx, time_value: str | None, minutes: int | None) -> datetime:
    try:
        return records.parse_callback_time(time_value, minutes, ctx.tenant, ctx.now)
    except ValueError as exc:
        raise Refused(
            "invalid_callback_time",
            "What time should I tell them someone will call by?",
            errors={"callback_time": str(exc)},
        ) from None


def flag_person(ctx: Ctx, args: FlagIn) -> Result:
    """The sentence comes back only after the flag and the callback are stored (spec S-23)."""
    due = _callback_due(ctx, args.callback_time, args.callback_in_minutes)
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
            ctx.conn, ctx.tenant, ctx.call, due, args.reason, ctx.now
        )
        if callback_id and system:
            outbound.add_callback_dial(ctx.conn, ctx.tenant, ctx.call, callback_id, due, ctx.now)
    spoken = ctx.tenant.time_spoken(due, ctx.now)
    if callback_id is None:
        say = ctx.tenant.line("flag_stored_no_callback")
    elif args.priority == "Urgent":
        say = ctx.tenant.line("flag_stored_urgent", time=spoken)
    else:
        say = ctx.tenant.line("flag_stored", time=spoken)
    return ok(
        say, flag_id=flag_id, callback_id=callback_id, callback_at=iso(due), callback_spoken=spoken
    )


def record_lead(ctx: Ctx, args: LeadIn) -> Result:
    """One write, no extra lookups (spec S-08). A commercial lead marks the call, so it can never
    become a booking."""
    details = args.details
    due = None
    if args.kind != "commercial":
        due = _callback_due(ctx, details.callback_time, details.callback_in_minutes)
    scoped = records.scoped_customers(ctx.conn, ctx.call["id"])
    customer_type = "Commercial" if args.kind == "commercial" else "Residential"
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
        "kind": ctx.tenant.fsm["lead_kinds"][args.kind],
        "customerType": customer_type,
        "customerId": next(iter(scoped))
        if len(scoped) == 1 and args.kind != "commercial"
        else None,
        "name": details.company or details.contact_name,
        "phone": ctx.call["caller_id"] if len(ctx.call["caller_key"]) == 10 else "",
        "summary": summary,
        "campaignId": ctx.tenant.fsm["campaign_id"],
    }
    key = _key(ctx, "lead", args.model_dump(mode="json"))
    with tx(ctx.conn):
        if args.kind == "commercial":
            records.update_call(ctx.conn, ctx.call["id"], commercial=1)
        callback_id = (
            records.add_callback(
                ctx.conn, ctx.tenant, ctx.call, due, f"{args.kind} lead: {details.need}", ctx.now
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
        say = ctx.tenant.line("lead_stored", time=ctx.tenant.time_spoken(due, ctx.now))
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
        fsm_note_id, where = note["id"], "customer"
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
            fsm_note_id, where = note["id"], "job"
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
        return ok(" ".join(safety["sign_off"].split()), hang_up=hang_up)
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
