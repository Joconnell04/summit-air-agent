"""The calls the voice layer makes around a conversation: start, every turn, end."""

import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import lookup, records, rules, safety
from .db import iso, tx
from .envelope import Result, ok
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable
from .schemas import EndIn, StartIn, TurnIn
from .tenant import Tenant
from .text import has_phrase, phone_key, words

RUN_WORDS = r"(?:i|we) (?:run|manage|operate|work at)"
# Owning is matched only against businesses: "I own the building" is a landlord of a one or
# two-family house as often as not, which is a home (spec S-05, journey 8).
OWN_WORDS = r"(?:i|we) own"


def sounds_commercial(text: str, cfg: dict) -> bool:
    """Does the caller describe a business? Best effort by design: the agent and the FSM account
    type catch the rest, and a false positive costs a booking, so the tenant's lists stay narrow."""
    text_words = words(text)
    for phrase in cfg["not"]:
        text_words = text_words.replace(words(phrase), " ")
    if any(has_phrase(text_words, p) for p in cfg["phrases"]):
        return True
    owned = "|".join(map(re.escape, cfg["owned"]))
    run = "|".join(map(re.escape, cfg["run"]))
    return bool(
        re.search(rf" (?:my|our) (?:{owned}) ", text_words)
        or re.search(rf" {RUN_WORDS} (?:a|an|the) (?:\w+ )?(?:{run}) ", text_words)
        or re.search(rf" {OWN_WORDS} (?:a|an|the) (?:\w+ )?(?:{owned}) ", text_words)
    )


DO_NOT_CALL_PHRASES = (
    "dont call me",
    "do not call me",
    "stop calling",
    "never call",
    "dont ever call",
    "take me off your list",
    "remove my number",
)


def _resume(conn: sqlite3.Connection, previous: sqlite3.Row) -> dict:
    """Where the last call stopped, so the agent can pick up without redoing anything."""
    last = {
        speaker: conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND speaker = ? ORDER BY seq DESC LIMIT 1",
            (previous["id"], speaker),
        ).fetchone()
        for speaker in ("caller", "agent")
    }
    booked = conn.execute(
        "SELECT fsm_job_id, status, window_start FROM bookings WHERE call_id = ? AND kind = 'book' "
        "AND status IN ('sending', 'booked', 'queued')",
        (previous["id"],),
    ).fetchone()
    return {
        "call_id": previous["id"],
        "ended_at": previous["ended_at"],
        "dropped": records.dropped(previous),
        "last_caller_line": last["caller"]["text"] if last["caller"] else None,
        "last_agent_line": last["agent"]["text"] if last["agent"] else None,
        "booking": dict(booked) if booked else None,
        "safety_state": previous["safety_state"],
    }


def start(
    conn: sqlite3.Connection, tenant: Tenant, fsm: FsmAdapter, args: StartIn, now: datetime
) -> Result:
    """Lookup before hello: the caller's records, a recent call to resume, and the safety state
    carried from a dropped safety call. Calling it twice for one call id is harmless."""
    caller_key = phone_key(args.caller_id)
    known = caller_key if len(caller_key) == 10 else ""
    recent = (
        [
            c
            for c in records.recent_calls(
                conn, tenant.slug, known, now, tenant.repeat["link_within_minutes"]
            )
            if c["id"] != args.call_id
        ]
        if known
        else []
    )
    previous = recent[0] if recent else None
    with tx(conn):
        conn.execute(
            """INSERT OR IGNORE INTO calls (id, tenant, caller_id, caller_key, dialed_number,
                   direction, started_at, after_hours, linked_call_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                args.call_id,
                tenant.slug,
                args.caller_id,
                known,
                args.dialed_number,
                args.direction,
                iso(now),
                int(not tenant.is_open(now)),
                previous["id"] if previous else None,
            ),
        )
        call = records.get_call(conn, args.call_id)
        # A safety call that dropped carries on where it was: if the lines weren't said, they come
        # first on this call too.
        if (
            previous
            and previous["safety_state"] in ("signal", "lines_said", "checking")
            and call["safety_state"] == "none"
        ):
            records.update_call(
                conn,
                args.call_id,
                safety_state=previous["safety_state"],
                safety_lines_at=previous["safety_lines_at"],
                safety_kind=previous["safety_kind"],
                safety_signal_at=previous["safety_signal_at"],
                safety_flag_id=previous["safety_flag_id"],
            )
    call = records.get_call(conn, args.call_id)

    customers, fsm_ok = [], True
    if known:
        try:
            found = fsm.customers_by_phone(known)[:5]
            with tx(conn):
                for customer in found:
                    records.add_scope(
                        conn, args.call_id, customer["id"], "caller_id", customer["type"]
                    )
            customers = [lookup.customer_bundle(fsm, tenant, conn, c, now) for c in found]
        except (FsmUnavailable, FsmRejected):
            fsm_ok = False
    _record_known_out_of_area(conn, args.call_id, customers, now)

    drops = sum(1 for c in recent if records.dropped(c))
    offer_callback = drops >= tenant.repeat["drops_before_callback_offer"]
    data = {
        "call_id": args.call_id,
        "tenant": {"slug": tenant.slug, "name": tenant.name, "timezone": str(tenant.tz)},
        "after_hours": bool(call["after_hours"]),
        "caller": {
            "phone": args.caller_id,
            "known_number": bool(known),
            "do_not_call": bool(known) and records.is_do_not_call(conn, known),
        },
        "customers": customers,
        "fsm_available": fsm_ok,
        "linked_call": _resume(conn, previous) if previous else None,
        "offer_callback": offer_callback,
        "knowledge": tenant.knowledge,
        # The owner's live edits, pinned to this call (app/rules.py).
        "house_rules": rules.start_call(conn, tenant, args.call_id, now),
    }
    say = tenant.line("repeated_drops") if offer_callback else ""
    if call["safety_state"] == "signal":
        lines = safety.lines_for(tenant.safety, call["safety_kind"])
        data["safety"] = {"action": "speak_lines", "kind": call["safety_kind"], "text": lines}
        say = lines
    return ok(say, **data)


def _record_known_out_of_area(
    conn: sqlite3.Connection, call_id: str, customers: list[dict], now: datetime
) -> None:
    """A known caller whose one location on file is outside the area is counted as out of area at
    the start, so the count doesn't depend on the agent checking the ZIP (spec S-28). With several
    locations the call's ZIP isn't known yet, so it waits for the read-back or a ZIP check. If they
    give a new address inside the area and book, the owner view leaves the call out of the count."""
    locations = [loc for c in customers for loc in c["locations"]]
    if len(locations) != 1 or locations[0]["in_service_area"]:
        return
    with tx(conn):
        for loc in locations:
            conn.execute(
                "INSERT OR IGNORE INTO out_of_area (call_id, zip, at) VALUES (?, ?, ?)",
                (call_id, loc["zip"], iso(now)),
            )


def turn(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, args: TurnIn, now: datetime
) -> Result:
    """Log the line and run the safety check on every caller line (docs/call-journeys.md, spine 4)."""
    cfg = tenant.safety
    seq = call["turn_seq"] + 1
    action = safety.SafetyAction("none", call["safety_kind"])
    changes: dict = {"turn_seq": seq}
    hints = []
    with tx(conn):
        if args.speaker == "caller":
            heating = cfg.get("heating_check")
            heating_asked = bool(heating) and records.agent_said(
                conn, call["id"], cfg["vague"][heating["kind"]]["question"]
            )
            action = safety.check_caller_turn(
                args.text,
                call["safety_state"],
                call["safety_kind"],
                call["safety_variant"],
                cfg,
                heating_asked,
            )
            if action.action == "speak_lines":
                changes.update(safety_state="signal", safety_kind=action.kind)
                if call["safety_signal_at"] is None or call["safety_kind"] != action.kind:
                    label = cfg["kinds"][action.kind]["label"]
                    changes["safety_signal_at"] = iso(now)
                    changes["safety_flag_id"] = records.add_flag(
                        conn,
                        call["id"],
                        "safety",
                        cfg["flag_reason"].format(label=label.capitalize()),
                        now,
                        "Urgent",
                    )
            elif action.action == "speak_variant":
                changes["safety_variant"] = action.variant
            elif action.action == "ask_check":
                changes.update(safety_state="checking", safety_kind=action.kind)
            elif action.action == "cleared":
                changes.update(safety_state="cleared")
                reason = (
                    "Caller said there was no smell or alarm after the safety lines"
                    if call["safety_state"] == "lines_said"
                    else "Caller said no to the safety check"
                )
                records.add_flag(conn, call["id"], "safety_check_cleared", reason, now, "Low")
            text_words = words(args.text)
            # Marked at once on the caller's words (spec S-49); the agent still says the line.
            if len(call["caller_key"]) == 10 and any(
                has_phrase(text_words, p) for p in DO_NOT_CALL_PHRASES
            ):
                records.mark_do_not_call(conn, call, now)
                hints.append("do_not_call")
            # A caller who describes a business can't book, whatever the agent does next (S-05).
            if sounds_commercial(args.text, tenant.commercial):
                changes["commercial"] = 1
                hints.append("commercial")
            # The server, not the model, says when the call reaches booking or closing, and only
            # then sends the owner's guidance for that stage, as a [server] note. Never on a safety
            # call; a stage reached during one gets its note once the safety state clears.
            rules.advance_stage(conn, call, now)
            on_safety = call["safety_state"] not in ("none", "cleared") or action.action != "none"
            if not on_safety:
                stage, items = rules.note_due(conn, tenant, call)
                if stage:
                    hints += [f"stage_{stage}"] + [f"guidance:{item}" for item in items]
        elif call["safety_state"] == "signal" and safety.agent_said_lines(
            args.text, call["safety_kind"], cfg
        ):
            changes.update(safety_state="lines_said", safety_lines_at=iso(now))
        records.update_call(conn, call["id"], **changes)
        conn.execute(
            "INSERT INTO turns (call_id, seq, speaker, text, at, timings_json, safety_action) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                call["id"],
                seq,
                args.speaker,
                args.text,
                iso(now),
                json.dumps(args.timings),
                action.action if action.action != "none" else None,
            ),
        )
    if action.action in ("speak_lines", "speak_variant", "ask_check"):
        hints.append("journey:safety")
    if action.action == "cleared":
        stood_down = call["safety_state"] == "lines_said"
        hints.append("safety_stood_down" if stood_down else "safety_check_cleared")
    # After the lines, every caller line carries a reminder that the safety call doesn't book.
    if args.speaker == "caller" and action.action == "none" and call["safety_state"] == "lines_said":
        hints.append("safety_call")
    payload = None
    if action.action != "none":
        payload = {"action": action.action, "kind": action.kind, "text": action.text}
    return ok(action.text, seq=seq, safety=payload, hints=hints)


def end(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, args: EndIn, now: datetime
) -> Result:
    """Close the call, record its outcome, and make sure a dropped safety call gets a person."""
    if call["ended_at"]:
        return ok(outcome=call["outcome"], already_ended=True)
    with tx(conn):
        if call["safety_state"] in ("signal", "checking"):
            records.add_flag(
                conn,
                call["id"],
                "safety_dropped",
                "Safety call ended before the lines were said",
                now,
                "Urgent",
            )
            due = now + timedelta(minutes=tenant.callbacks["dropped_safety_minutes"])
            records.add_callback(conn, tenant, call, due, "Safety call dropped; call back", now)
        outcome = _outcome(conn, call)
        records.update_call(
            conn,
            call["id"],
            ended_at=iso(now),
            end_reason=args.reason,
            outcome=outcome,
            summary_status="pending",
        )
    return ok(outcome=outcome)


def _outcome(conn: sqlite3.Connection, call: sqlite3.Row) -> str:
    def has(sql: str) -> bool:
        return bool(conn.execute(sql, (call["id"],)).fetchone())

    if has("SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'booked'"):
        return "booked"
    if has("SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'queued'"):
        return "queued"
    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        return "safety"
    if call["commercial"]:
        return "declined_commercial"
    if has("SELECT 1 FROM out_of_area WHERE call_id = ?"):
        return "declined_out_of_area"
    if has(
        "SELECT 1 FROM bookings WHERE call_id = ? AND kind IN ('reschedule', 'cancel') AND status = 'done'"
    ):
        return "changed_booking"
    if has("SELECT 1 FROM callbacks WHERE call_id = ?"):
        return "callback"
    if has("SELECT 1 FROM flags WHERE call_id = ? AND kind = 'person'"):
        return "flagged"
    return "no_booking"
