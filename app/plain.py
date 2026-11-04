"""What the agent did on a call, in words an owner reads without knowing the tools (story O-7).

Each tool call in a timeline becomes one short note: what was checked and what came of it. A
refusal is said as what the server held back, because to the owner a refused booking is the system
doing its job. The tool name and arguments stay on the page behind "Technical details".
"""

from datetime import datetime

from . import safety
from .owner import LEAD_PHRASES, type_label
from .tenant import Tenant

# What a refusal code means on the call, said as what the server did.
REFUSED = {
    "gate_safety": ("Held the booking until the safety lines were said.", "hold"),
    "gate_read_back": ("Held the booking until the address and phone were read back.", "hold"),
    "out_of_area": ("Outside the service area, so it can't be booked.", "warn"),
    "zip_not_heard": ("The caller hadn't said that ZIP; asked for it.", "hold"),
    "zip_mismatch": ("The caller's ZIP didn't match their street; asked which is right.", "hold"),
    "address_several": ("That street is in more than one place; asked which.", "hold"),
    "address_not_found": ("The street wasn't found; asked the caller to spell it.", "hold"),
    "address_lookup_down": ("The address lookup didn't answer; asked for the ZIP.", "hold"),
    "address_not_heard": ("The caller hadn't said that street; asked for the address.", "hold"),
    "customer_type": ("A commercial caller, which this company doesn't book.", "warn"),
    "slot_taken": ("That opening had just been taken; offered another.", "hold"),
    "invalid_slot": ("That time isn't open; offered another.", "hold"),
    "fsm_unavailable": ("ServiceTitan didn't answer; the booking was queued to retry.", "warn"),
    "fsm_rejected": ("ServiceTitan turned the booking down; a person was flagged.", "warn"),
    "limit": ("Hit the limit for this call, so a person was flagged instead.", "warn"),
    "not_owner": ("Blocked a look at a record that isn't this caller's.", "warn"),
    "invalid_phone": ("The phone number didn't check out; asked again.", "hold"),
    "invalid_arguments": ("Some details didn't check out; asked again.", "hold"),
    "invalid_callback_time": ("That callback time didn't work; asked for another.", "hold"),
    "invalid_state": ("That change couldn't be made on this job.", "hold"),
}
# Bookkeeping the voice layer sends; nothing an owner needs to read.
SKIP = {"calls.start", "calls.end", "end_call", "turn"}


def _window(slot: dict, tenant: Tenant) -> str:
    window = tenant.window(slot.get("window", ""))
    if window is None:
        return ""
    return "after hours" if window.after_hours else window.label()


def tool_note(event: dict, tenant: Tenant) -> dict | None:
    """One note for a tool call: {"text", "tone"} where tone is ok, hold or warn. None for calls
    that are bookkeeping, and for a refusal the agent is expected to hit and move past."""
    tool, args = event["tool"], event["args"]
    if tool in SKIP:
        return None
    if not event["ok"]:
        if event["refused"] == "anything_else_first":
            return None
        text, tone = REFUSED.get(
            event["refused"], ("The server held this back; see the technical details.", "hold")
        )
        if event["refused"] == "out_of_area" and args.get("zip"):
            text = f"ZIP {args['zip']} is outside the service area, so it can't be booked."
        return {"text": text, "tone": tone}
    if tool == "find_customer":
        return {"text": "Looked the caller up in ServiceTitan.", "tone": "ok"}
    if tool == "get_customer_history":
        return {"text": "Checked this customer's past visits.", "tone": "ok"}
    if tool == "find_address":
        return {"text": f"Found the ZIP for {args.get('street', 'the street')}.", "tone": "ok"}
    if tool == "check_service_area":
        return {"text": f"ZIP {args.get('zip', '')} is in the service area.", "tone": "ok"}
    if tool == "find_slots":
        what = type_label(tenant, args.get("job_type") or "other")
        return {"text": f"Looked for openings: {what}.", "tone": "ok"}
    if tool == "read_back":
        if args.get("confirmed"):
            return {"text": "The caller confirmed the address and phone.", "tone": "ok"}
        return {"text": "Read the address and phone back before booking.", "tone": "ok"}
    if tool == "book_appointment":
        what = type_label(tenant, args.get("job_type") or "other")
        when = _window(args.get("slot", {}), tenant)
        text = f"Booked in ServiceTitan: {what}" + (f", {when}." if when else ".")
        if event["gate"] == "safety_override":
            return {
                "text": text + " The caller asked to book after the safety lines; logged as a "
                "safety override.",
                "tone": "warn",
            }
        return {"text": text, "tone": "ok"}
    if tool == "reschedule_appointment":
        return {"text": "Moved the job to the new time in ServiceTitan.", "tone": "ok"}
    if tool == "cancel_appointment":
        return {"text": "Cancelled the job in ServiceTitan, as the caller asked.", "tone": "ok"}
    if tool == "flag_person":
        if args.get("system_calls_back"):
            return {"text": "Set up a call back from the system at the time asked.", "tone": "ok"}
        return {"text": f"Flagged for a person: {args.get('reason', '')}", "tone": "hold"}
    if tool == "record_lead":
        label = LEAD_PHRASES.get(args.get("kind", ""), "lead")
        return {"text": f"Saved {label} for sales.", "tone": "ok"}
    if tool == "add_note":
        return {"text": "Added a note for the technician.", "tone": "ok"}
    if tool == "mark_do_not_call":
        return {"text": "Added this number to the do-not-call list.", "tone": "ok"}
    return {"text": f"Used {tool.replace('_', ' ')}.", "tone": "ok"}


def safety_note(tenant: Tenant, kind: str | None) -> dict:
    """The note on the caller's turn that tripped the safety check."""
    what = tenant.safety["kinds"].get(kind or "", {}).get("label", "a safety warning sign")
    return {
        "text": f"Heard {what}. Booking is locked until the safety lines are said.",
        "tone": "warn",
    }


def conversation(trace: dict, tenant: Tenant) -> list[dict]:
    """The call as a thread: each turn, and after it a note for whatever the agent did, with the
    raw tool call kept beside the note for "Technical details". Seconds are from the call's start."""
    started = datetime.fromisoformat(trace["call"]["started_at"])
    kind = trace["call"]["safety_kind"]

    def elapsed(at: str) -> str:
        seconds = max(int((datetime.fromisoformat(at) - started).total_seconds()), 0)
        return f"{seconds // 60}:{seconds % 60:02d}"

    # Only the turn the server accepted as the lines gets the note: the same word-for-word check
    # lifecycle.turn runs before it records safety_lines_at.
    lines_recorded = bool(trace["call"]["safety_lines_at"])
    items: list[dict] = []
    lines_due = False
    for event in trace["events"]:
        at = elapsed(event["at"])
        if event["type"] == "turn":
            items.append(
                {
                    "kind": "turn",
                    "at": at,
                    "seq": event["seq"],
                    "speaker": event["speaker"],
                    "text": event["text"],
                }
            )
            if event["speaker"] == "caller" and event["safety_action"] == "speak_lines":
                items.append({"kind": "note", "at": at, **safety_note(tenant, kind), "tech": None})
                lines_due = True
            elif (
                event["speaker"] == "agent"
                and lines_due
                and lines_recorded
                and safety.agent_said_lines(event["text"], kind, tenant.safety)
            ):
                items.append(
                    {
                        "kind": "note",
                        "at": at,
                        "text": "Said the signed-off safety lines, word for word.",
                        "tone": "ok",
                        "tech": None,
                    }
                )
                lines_due = False
            continue
        note = tool_note(event, tenant)
        if note is None:
            continue
        items.append({"kind": "note", "at": at, **note, "tech": event})
    if lines_due:
        items.append(
            {
                "kind": "note",
                "at": items[-1]["at"],
                "text": "The signed-off safety lines were not said on this call.",
                "tone": "warn",
                "tech": None,
            }
        )
    return items
