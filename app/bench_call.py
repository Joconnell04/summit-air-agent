"""What the bench's call page shows beyond the step graph (app/nodes.py): who ended the call and
why, each server decision with the rule behind it, the per-turn timings, the customer the call
matched, the booking against the read-back, the call's cost, and what Retell heard beside what the
server received. Every function reads the call's record (app/trace.py) or Retell's call object;
none of them writes anything.

Retell's call object comes from `GET /v2/get-call/{id}` (app/failure_grader.py RetellTranscripts,
the versioned endpoint), only when a call page asks for it, and an ended call's object is kept in
memory so a page opened twice reads Retell once.
"""

import json
import re
import sqlite3
import threading
from datetime import datetime

from . import consistency, cost, failure_grader, lookup
from .db import parse_iso
from .fsm import FsmRejected, FsmUnavailable
from .text import phone_display

RETELL_PREFIX = failure_grader.RETELL_PREFIX

# How each value of calls.end_reason (app/schemas.py EndIn) reads, and who ended the call.
ENDED_BY = {
    "agent_end": ("Agent", ""),
    "stepped_away": ("Agent", "caller stepped away"),
    "caller_hangup": ("Caller", "hung up"),
    "dropped": ("Line", "dropped"),
    "transfer": ("Transfer", "put through"),
    "error": ("Error", "voice layer error"),
}

# The stage timings the voice layers store on an agent line (voice/retell_llm/call.py, the socket;
# voice/livekit, the earlier LiveKit worker), in the order they happen, with a short label.
STAGES = (
    ("server_ms", "Server"),
    ("llm_first_token_ms", "Model"),
    ("quality_first_token_ms", "Second model"),
    ("tool_ms", "Tools"),
    ("hold_ms", "Hold"),
    ("tts_first_byte_ms", "Voice"),
)
CALLER_STAGES = (("transcript_ms", "Transcript"), ("end_of_turn_ms", "End of turn"))

SAFETY_ACTIONS = {
    "speak_lines": "Safety lines",
    "speak_variant": "Safety lines again",
    "ask_check": "Safety check question",
    "cleared": "Safety cleared",
}
SAFETY_HEARD = {
    "speak_lines": "Hazard heard",
    "speak_variant": "Hazard heard again",
    "ask_check": "Possible hazard, asked to check",
    "cleared": "Caller said there is no hazard",
}
# A priority rule's source, from its name in bookings.priority_rules (app/priority.py).
SIDE_MODEL_RULE = "side_model"
QUOTE = re.compile(r'caller said "(.*)"')
LIFECYCLE = {"calls.start", "calls.end", "calls.transfer", "calls.recording", "turn"}
# The tools that write a record the tool call page can show (bookings, dispatches, flags,
# callbacks, leads, notes), and with them a job in ServiceTitan.
WRITES = {"book_appointment", "read_back", "find_slots", "reschedule_appointment",
          "cancel_appointment", "record_move", "flag_person", "request_transfer", "record_lead",
          "add_note", "mark_do_not_call"}
# Each tool as the Why tab says it: what the agent asked the server to do.
TOOL_WORDS = {
    "find_customer": "Look up the caller",
    "get_customer_history": "Read the customer's history",
    "check_service_area": "Check the service area",
    "find_address": "Look up the address",
    "find_slots": "Find open times",
    "read_back": "Read back the address and phone",
    "book_appointment": "Book the visit",
    "reschedule_appointment": "Move the visit",
    "cancel_appointment": "Cancel the visit",
    "record_move": "Record a move",
    "flag_person": "Flag for a person",
    "request_transfer": "Put the caller through",
    "record_lead": "Store a lead",
    "add_note": "Add a note for the tech",
    "mark_do_not_call": "Mark do not call",
    "end_call": "End the call",
    "lookup_hvac_info": "Answer an equipment question",
}


def clock(start: datetime, at: str | None) -> str:
    """Minutes and seconds from the call's start."""
    if not at:
        return ""
    seconds = max(0, int((parse_iso(at) - start).total_seconds()))
    return f"{seconds // 60}:{seconds % 60:02d}"


def seconds(call: dict) -> int | None:
    if not call.get("ended_at"):
        return None
    return int((parse_iso(call["ended_at"]) - parse_iso(call["started_at"])).total_seconds())


def end_call_reason(trace: dict) -> str | None:
    """The reason the agent gave its end_call, if it called one."""
    for event in reversed(trace["events"]):
        if event["type"] == "tool" and event["tool"] == "end_call":
            return (event["args"] or {}).get("reason")
    return None


def ended_by(trace: dict, retell: dict | None = None) -> dict:
    """Who ended the call and why: the server's end reason, the agent's end_call reason, and
    Retell's disconnection_reason when Retell's call object was read."""
    call = trace["call"]
    if not call.get("ended_at"):
        return {"who": "Open", "why": "still going", "retell": None}
    who, why = ENDED_BY.get(call.get("end_reason") or "", ("Unknown", call.get("end_reason") or ""))
    reason = end_call_reason(trace)
    if who == "Agent" and reason:
        why = reason.replace("_", " ")
    disconnection = (retell or {}).get("disconnection_reason")
    return {"who": who, "why": why,
            "retell": disconnection.replace("_", " ") if disconnection else None}


# ---- the server's decisions ---------------------------------------------------------------


def _rule_name(code: str) -> str:
    return _rule(code)[0]


def _rule(code: str) -> tuple[str, str]:
    """A gate's rule name and what it guards (app/nodes.py RULES)."""
    from .nodes import RULES

    for name, guards, gates, _ in RULES:
        if code in gates:
            return name, guards
    return code.replace("_", " ").capitalize(), ""


def _tool(tool: str) -> str:
    return TOOL_WORDS.get(tool, tool.replace("_", " ").capitalize())


def _priority_source(rule: str, reason: str) -> str:
    if rule == SIDE_MODEL_RULE:
        return "Side model"
    if QUOTE.search(reason):
        return "Caller's words"
    if rule.startswith("bracket:"):
        return "Temperature"
    return "Phrase rule"


def decisions(conn: sqlite3.Connection, trace: dict) -> list[dict]:
    """Every decision the server made on the call, in order: safety signals and how fast the lines
    came, each refusal with the rule and the sentence the server wrote, the server's lines, the
    priority of each booking with the source of each reason, dispatches, the label and the end."""
    call = trace["call"]
    call_id = call["id"]
    start = parse_iso(call["started_at"])
    rows: list[dict] = []
    turn = 0

    def add(at, kind, decision, rule="", source="", outcome="", say="", alarm=False, *,
            what="", why="", result=""):
        rows.append({"at": at, "clock": clock(start, at), "turn": turn, "kind": kind,
                     "decision": decision, "rule": rule, "source": source, "outcome": outcome,
                     "say": say or "", "alarm": alarm,
                     "what": what or decision, "why": why or rule, "result": result or outcome})

    for event in trace["events"]:
        if event["type"] == "turn":
            turn = event["seq"]
            action = event.get("safety_action")
            if event["speaker"] == "caller" and action in SAFETY_ACTIONS:
                add(event["at"], "Safety", SAFETY_ACTIONS[action], "Safety check on every line",
                    "Caller's words", "fired", event["text"], alarm=action != "cleared",
                    what=SAFETY_HEARD[action], why="The safety check runs on every caller line",
                    result="Cleared" if action == "cleared" else "Fixed lines next")
            continue
        tool = event["tool"]
        if tool in LIFECYCLE:
            continue
        if not event["ok"]:
            code = event["gate"] or event["refused"] or "refused"
            name, guards = _rule(code)
            add(event["at"], "Refused", tool, name, "Server gate", "held", event["say"],
                alarm=True, what=f"{_tool(tool)}: held", why=guards or name,
                result="Held back")
        elif event["gate"]:
            name, guards = _rule(event["gate"])
            add(event["at"], "Allowed", tool, name, "Server gate",
                event["gate"].replace("_", " "), event["say"],
                what=f"{_tool(tool)}: allowed", why=guards or name, result="Went through")
        elif event["say"]:
            add(event["at"], "Server line", tool, "", "Server", "", event["say"],
                what=_tool(tool), why="The server's own sentence, said word for word",
                result="Said")

    if call.get("safety_signal_at"):
        lines_at = call.get("safety_lines_at")
        took = (f"{(parse_iso(lines_at) - parse_iso(call['safety_signal_at'])).total_seconds():.1f} s"
                if lines_at else "never said")
        turn = 0
        hazard = (call.get('safety_kind') or 'safety').upper()
        add(lines_at or call["safety_signal_at"], "Safety", f"{hazard} lines said",
            "Safety lines first", "Server", took, alarm=not lines_at,
            what=f"{hazard} safety lines" + ("" if lines_at else " never said"),
            why="The fixed lines come before anything else on a hazard",
            result=f"{took} after the signal" if lines_at else "Never said")

    for b in trace["bookings"]:
        rules = json.loads(b["priority_rules"]) if b.get("priority_rules") else []
        reasons = json.loads(b["priority_reason_list"]) if b.get("priority_reason_list") else []
        turn = 0
        if not rules and not reasons:
            continue
        for i, reason in enumerate(reasons):
            rule = rules[i] if i < len(rules) else ""
            plain = QUOTE.sub('', reason).rstrip(', ')
            source = _priority_source(rule, reason)
            add(None, "Priority", f"{b['priority']}: {plain}",
                rule.replace("_", " "), source, b["priority"],
                (QUOTE.search(reason) or [None, ""])[1],
                what=f"Priority set to {b['priority']}", why=plain[:1].upper() + plain[1:],
                result=source)
    for d in conn.execute("SELECT * FROM dispatches WHERE call_id = ? ORDER BY id", (call_id,)):
        job = d["job_type"].replace("_", " ")
        add(d["at"], "Dispatch", job, "Dispatch at read-back",
            "Server", "replaced" if d["replaced_at"] else "booked",
            what=f"Technician dispatched for {job}",
            why="An urgent call goes to the on-call technician once the address is confirmed",
            result="Replaced by a later dispatch" if d["replaced_at"] else "Sent")
    for o in trace["out_of_area"]:
        add(o["at"], "Refused", f"ZIP {o['zip']}", "Service area", "Server", "out of area",
            alarm=True, what=f"ZIP {o['zip']} turned away", why="Only ZIPs the company serves are booked",
            result="Not booked")
    for c in trace["identity_checks"]:
        add(None, "Identity", f"customer {c['customer_id']}", "Name and address check", "Server",
            c["status"], what=f"Checked the caller is customer {c['customer_id']}",
            why="A change from a number not on the account needs the name and address to match",
            result=c["status"].replace("_", " ").capitalize())
    label = conn.execute("SELECT label, reason, at FROM call_labels WHERE call_id = ?",
                         (call_id,)).fetchone()
    if label:
        turn = 0
        add(label["at"], "Label", label["label"], "Call label", "Server", label["reason"],
            what=f"Labeled {label['label']}", why=label["reason"], result="On the call list")
    reason = end_call_reason(trace)
    if call.get("ended_at"):
        who, how = ENDED_BY.get(call.get("end_reason") or "", ("Unknown", ""))
        end_why = {
            "Agent": "The agent called end_call" + (f": {reason.replace('_', ' ')}" if reason else ""),
            "Caller": "The caller hung up",
            "Line": "The line dropped",
            "Transfer": "The caller was put through to a person",
        }.get(who, how or "The voice layer closed the call")
        add(call["ended_at"], "End", (call.get("end_reason") or "").replace("_", " "), "",
            "Agent" if reason else "Voice layer", (reason or "").replace("_", " "),
            what="Call ended", why=end_why, result=who)
    # Rows without a time (priority, identity) sit at the booking they came from: after the
    # timed rows of the same moment, before the label and the end.
    end = call.get("ended_at") or "9999"
    return sorted(rows, key=lambda r: (r["at"] or end, r["kind"] in ("Label", "End")))


def safety_summary(trace: dict) -> dict | None:
    """On a safety call: the caller's line that set it off, the seconds and agent lines between the
    signal and the fixed lines, and how many lines the agent spoke after them."""
    call = trace["call"]
    if not call.get("safety_signal_at"):
        return None
    turns = [e for e in trace["events"] if e["type"] == "turn"]
    trigger = next((e for e in turns if e["speaker"] == "caller"
                    and e.get("safety_action") in ("speak_lines", "speak_variant", "ask_check")), None)
    lines_at = call.get("safety_lines_at")
    between = after = 0
    if trigger:
        for e in turns:
            if e["seq"] <= trigger["seq"] or e["speaker"] != "agent":
                continue
            if lines_at and e["at"] > lines_at:
                after += 1
            elif not lines_at or e["at"] < lines_at:
                between += 1
    took = (parse_iso(lines_at) - parse_iso(call["safety_signal_at"])).total_seconds() if lines_at else None
    return {"kind": call.get("safety_kind"), "trigger": trigger["text"] if trigger else "",
            "seconds": took, "between": between, "after": after}


# ---- timings ------------------------------------------------------------------------------


def _pct(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def timing(trace: dict) -> dict:
    """Each agent line's stage timings as recorded, the reply time (the caller's last word to the
    agent's first), and the p50 and p95 of each column over the call."""
    call = trace["call"]
    start = parse_iso(call["started_at"])
    rows, last_caller = [], None
    for e in trace["events"]:
        if e["type"] != "turn":
            continue
        t = e.get("timings") or {}
        if e["speaker"] == "caller":
            last_caller = e
            continue
        stages = {key: t.get(key) for key, _ in STAGES}
        reply = t.get("reply_ms")
        known = sum(v for v in stages.values() if v)
        rows.append({
            "seq": e["seq"], "clock": clock(start, e["at"]), "text": e["text"],
            "caller": last_caller["text"] if last_caller else "",
            "stages": stages, "reply": reply,
            "other": max(0.0, reply - known) if reply is not None and known else None,
            "scripted": "spoken_from_code_ms" in t,
            "caller_stages": {k: (last_caller.get("timings") or {}).get(k)
                              for k, _ in CALLER_STAGES} if last_caller else {},
        })
        last_caller = None
    columns = [*STAGES, ("reply", "Reply")]
    summary = {}
    for key, _ in columns:
        values = [r["reply"] if key == "reply" else r["stages"][key] for r in rows]
        values = [v for v in values if v is not None]
        summary[key] = {"p50": _pct(values, 0.5), "p95": _pct(values, 0.95), "n": len(values)}
    shown = [(k, label) for k, label in STAGES if summary[k]["n"]]
    widest = max([r["reply"] or 0 for r in rows] + [1])
    return {"rows": rows, "stages": shown, "summary": summary, "widest": widest}


# ---- records ------------------------------------------------------------------------------


def _plan(membership) -> str:
    """A membership as lookup.membership_summary gives it, in a few words."""
    if not isinstance(membership, dict):
        return membership or ""
    plan = membership.get("plan") or membership.get("name") or "Member"
    status = membership.get("status", "Active")
    return plan if status == "Active" else f"{plan}, {status.lower()}"


def customers(conn: sqlite3.Connection, trace: dict, start: dict | None, fsm=None,
              tenant=None) -> list[dict]:
    """The customers matched to the call, how they were matched, and their membership and open
    jobs as /calls/start returned them. `start` is that result, from the log rows the step graph
    already read (app/nodes.py build), so this reads no log of its own. A customer matched later
    (by address or name), or on a call whose start isn't in the log, is read from ServiceTitan
    when `fsm` is given."""
    call_id = trace["call"]["id"]
    matched = {r["customer_id"]: dict(r) for r in conn.execute(
        "SELECT customer_id, matched_by, customer_type FROM call_customers WHERE call_id = ?",
        (call_id,))}
    found = (start or {}).get("customers") or []
    out = []
    for c in found:
        m = matched.pop(c.get("customer_id"), {})
        membership = c.get("membership")
        out.append({"id": c.get("customer_id"), "name": c.get("name", ""),
                    "type": c.get("type", m.get("customer_type", "")),
                    "matched_by": (m.get("matched_by") or "caller_id").replace("_", " "),
                    "membership": _plan(membership),
                    "locations": len(c.get("locations") or []),
                    "open_jobs": len(c.get("open_jobs") or [])})
    for customer_id, m in matched.items():
        out.append({"id": customer_id, "name": "", "type": m["customer_type"],
                    "matched_by": m["matched_by"].replace("_", " "), "membership": "",
                    "locations": None, "open_jobs": None, **_on_file(fsm, tenant, customer_id)})
    return out


def _on_file(fsm, tenant, customer_id: int) -> dict:
    """A matched customer's name, membership and open jobs read from ServiceTitan, for a call whose
    start result isn't in the log (rotated, or another server's). Reads only; an unanswered read
    leaves the row as it was."""
    if fsm is None or tenant is None:
        return {}
    try:
        customer = fsm.customer(customer_id)
        membership = lookup.membership_summary(tenant, fsm.memberships(customer_id))
        jobs = fsm.jobs(customer_id, list(lookup.OPEN_JOB_STATUSES))
    except (FsmUnavailable, FsmRejected):
        return {}
    return {"name": customer.get("name", ""), "membership": _plan(membership),
            "open_jobs": len(jobs)}


def readback(conn: sqlite3.Connection, trace: dict) -> dict | None:
    """The read-back the caller confirmed and whether every standing booking matches it
    (app/consistency.py, the same check the call's end runs)."""
    call = trace["call"]
    if not call.get("readback_json"):
        return None
    row = conn.execute("SELECT * FROM calls WHERE id = ?", (call["id"],)).fetchone()
    said = json.loads(call["readback_json"])
    return {"said": said, "confirmed": bool(call.get("readback_confirmed_at")),
            "mismatches": consistency.booking_mismatches(conn, row)}


# ---- what the call achieved ----------------------------------------------------------------

BOOKING_TAGS = {
    ("book", "booked"): ("Booked", "done"), ("book", "done"): ("Booked", "done"),
    ("reschedule", "booked"): ("Moved", "done"), ("reschedule", "done"): ("Moved", "done"),
    ("cancel", "booked"): ("Cancelled", "done"), ("cancel", "done"): ("Cancelled", "done"),
}


def _when(tenant, start: str | None) -> str:
    if not start:
        return ""
    return tenant.local(parse_iso(start)).strftime("%a %b %-d, %-I:%M %p")


def outcomes(conn: sqlite3.Connection, trace: dict, customers: list[dict], tenant) -> dict:
    """What the call achieved, in the order an owner asks about it: what was booked or sent, who
    the caller is on file (matched by phone, found by address, or added by the booking), what was
    left for a person, and the details the agent captured. Each line has a tag and its tone."""
    call = trace["call"]
    lines: list[dict] = []

    def line(tag, tone, text, sub=""):
        lines.append({"tag": tag, "tone": tone, "text": text, "sub": sub})

    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM bookings WHERE call_id = ? ORDER BY id", (call["id"],))]
    for b in rows:
        job = tenant.job_types.get(b["job_type"] or "", {}).get("name", (b["job_type"] or "visit").replace("_", " "))
        tag, tone = BOOKING_TAGS.get((b["kind"], b["status"]), ("", ""))
        if not tag:
            tag, tone = {"queued": ("Queued", "high"), "sending": ("Sending", "high"),
                         "failed": ("Failed", "urgent")}.get(b["status"], (b["status"].capitalize(), "outline"))
        text = f"{job}, {_when(tenant, b['window_start'])}" + (f", {b['priority']}" if b["priority"] else "")
        if b["status"] in ("queued", "sending"):
            sub = f"Waiting for ServiceTitan, {b['attempts']} attempt{'s' if b['attempts'] != 1 else ''}"
        elif b["status"] == "failed":
            sub = b["last_error"] or "ServiceTitan refused it"
        else:
            sub = f"ServiceTitan job {b['fsm_job_id']}" if b["fsm_job_id"] else ""
        line(tag, tone, text, sub)
    for d in conn.execute("SELECT * FROM dispatches WHERE call_id = ? ORDER BY id", (call["id"],)):
        if not d["replaced_at"]:
            line("Dispatched", "urgent", f"On-call technician sent, {d['job_type'].replace('_', ' ')}")

    matched = {c["id"] for c in customers}
    for c in customers[:2]:
        name = c["name"] or f"Customer {c['id']}"
        how = {"caller id": "by caller ID"}.get(c["matched_by"], f"by {c['matched_by']}")
        sub = ", ".join(p for p in (
            c["membership"] and f"member: {c['membership']}",
            c["open_jobs"] and f"{c['open_jobs']} open job{'s' if c['open_jobs'] != 1 else ''}",
        ) if p)
        line("Matched", "done", f"{name}, {how}", sub)
    for b in rows:
        if b["customer_id"] and b["customer_id"] not in matched and b["status"] in ("booked", "done"):
            name = (json.loads(b["request_json"]).get("details") or {}).get("name", "")
            line("Added", "done", f"New customer in ServiceTitan{': ' + name if name else ''}",
                 f"Customer {b['customer_id']}, made by the booking")
            matched.add(b["customer_id"])
    if not matched:
        line("New caller", "outline",
             "Number not on file" if call.get("caller_key") else "No caller ID",
             "Nothing added to ServiceTitan" if not rows else "")

    for f in follow_ups(trace, tenant):
        if f["callback"]:
            cb = f["callback"]
            tone = "high" if cb["status"] == "pending" else ("urgent" if cb["status"] == "overdue" else "done")
            line("Callback", tone, f"{cb['owner'].capitalize()} calls back by {cb['due']}",
                 cb["status"].capitalize())
    for lead in trace["leads"]:
        line("Lead", "info", lead["summary"], lead["status"])
    for t in trace["transfers"] + trace["handoffs"]:
        line("Transfer", "info", f"Put through to the {t['role']}", t["status"])
    for o in trace["out_of_area"]:
        line("Out of area", "outline", f"ZIP {o['zip']} isn't served", "Not booked")
    if not lines or all(x["tag"] in ("Matched", "New caller") for x in lines):
        line("No action", "outline", "Nothing booked, sent or left for a person")
    return {"lines": lines, "captured": captured(trace, rows, customers)}


def captured(trace: dict, bookings: list[dict], customers: list[dict]) -> list[tuple[str, str]]:
    """The details the agent took down, from the newest booking, else the read-back and the name
    the caller gave."""
    call = trace["call"]
    request = json.loads(bookings[-1]["request_json"]) if bookings else {}
    details = request.get("details") or {}
    said = json.loads(call["readback_json"]) if call.get("readback_json") else {}
    address = request.get("address") or said
    names = trace.get("names") or []
    name = details.get("name") or (names[0]["name"] if names else "") or (
        customers[0]["name"] if customers else "")
    street = ", ".join(p for p in (address.get("street"), address.get("unit"), address.get("zip")) if p)
    issue = details.get("issue", "")
    if details.get("since") and not str(details["since"]).lower().startswith("not asked"):
        issue = f"{issue}, since {details['since']}" if issue else f"Since {details['since']}"
    out = [
        ("Name", name),
        ("Address", street),
        ("Phone", phone_display(request.get("phone") or said.get("phone") or "")),
        ("Problem", issue),
        ("System", details.get("system_type", "")),
        ("Tried", details.get("tried", "")),
        ("Who's home", details.get("who_home", "")),
        ("Approves work", details.get("approver", "")),
    ]
    # "not asked: emergency dispatch" is the agent saying it skipped a question, not a detail.
    return [(k, str(v)) for k, v in out if v and not str(v).lower().startswith("not asked")]


def follow_ups(trace: dict, tenant) -> list[dict]:
    """Each flag for a person with the callback it led to, as one row: a callback whose reason is
    the flag's, or that a transfer or handoff tied to it, is shown once with the flag. Callbacks
    with no flag are rows of their own."""
    callbacks = {c["id"]: c for c in trace["callbacks"]}
    tied = {t["flag_id"]: t["callback_id"] for t in trace["transfers"] + trace["handoffs"]
            if t.get("flag_id") and t.get("callback_id")}
    used: set[int] = set()

    def shown(c: dict) -> dict:
        used.add(c["id"])
        return {"owner": c["owner"].capitalize(), "due": _when(tenant, c["due_at"]),
                "status": c["status"]}

    out = []
    for f in trace["flags"]:
        match = callbacks.get(tied.get(f["id"])) or next(
            (c for c in trace["callbacks"] if c["id"] not in used and c["reason"] == f["reason"]), None)
        out.append({"kind": f["kind"].replace("_", " ").capitalize(), "priority": f["priority"],
                    "status": f["status"], "reason": f["reason"],
                    "callback": shown(match) if match else None})
    for c in trace["callbacks"]:
        if c["id"] not in used:
            out.append({"kind": "Callback", "priority": "", "status": c["status"],
                        "reason": c["reason"], "callback": shown(c)})
    return out

def call_cost(conn: sqlite3.Connection, trace: dict, retell: dict | None = None) -> dict:
    """The call's cost at the measured rates (app/cost.py, docs/cost.md), the grader's and the
    summary's own costs as recorded, and Retell's bill when its call object was read."""
    call = trace["call"]
    length = seconds(call)
    minutes = (length or 0) / 60
    voice = minutes * cost.VOICE_PER_MINUTE
    grade = conn.execute("SELECT cost_usd FROM grades WHERE call_id = ?", (call["id"],)).fetchone()
    summary = conn.execute("SELECT cost_usd FROM summaries WHERE call_id = ?", (call["id"],)).fetchone()
    extra = (grade["cost_usd"] if grade else 0) + ((summary["cost_usd"] or 0) if summary else 0)
    billed = ((retell or {}).get("call_cost") or {}).get("combined_cost")
    return {"minutes": minutes, "voice": voice, "model": cost.MODEL_PER_CALL, "after": extra,
            "total": voice + cost.MODEL_PER_CALL + extra,
            "retell_billed": billed / 100 if billed is not None else None}


def estimate(started_at: str, ended_at: str | None) -> float | None:
    """A call's cost at the measured rates from its length alone, for the call list."""
    if not ended_at:
        return None
    minutes = (parse_iso(ended_at) - parse_iso(started_at)).total_seconds() / 60
    return minutes * cost.VOICE_PER_MINUTE + cost.MODEL_PER_CALL


# ---- what Retell heard --------------------------------------------------------------------


class RetellCalls:
    """Retell's call objects for the bench, read on demand and kept for ended calls. `reader` is
    anything with `get(retell_id) -> dict` (app/failure_grader.py RetellTranscripts)."""

    def __init__(self, reader=None, keep: int = 200):
        self.reader = reader
        self.keep = keep
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.reader is not None

    def get(self, call_id: str) -> tuple[dict | None, str | None]:
        """(the call object, None), or (None, why not)."""
        if not call_id.startswith(RETELL_PREFIX):
            return None, "not a Retell call"
        if self.reader is None:
            return None, "RETELL_API_KEY not set"
        retell_id = call_id.removeprefix(RETELL_PREFIX)
        with self._lock:
            if retell_id in self._cache:
                return self._cache[retell_id], None
        try:
            found = self.reader.get(retell_id)
        except Exception as exc:  # noqa: BLE001 - shown on the page; a failed read changes nothing
            return None, f"Retell read failed: {type(exc).__name__}"
        if found.get("call_status") == "ended":
            with self._lock:
                if len(self._cache) >= self.keep:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[retell_id] = found
        return found, None


def _words(entry: dict) -> list[dict]:
    return entry.get("words") or []


def talked_over(prev: dict, entry: dict) -> bool:
    """A caller entry whose first word began before the agent's line before it had ended: the
    caller spoke over the agent (a barge-in, or words Retell may have cut)."""
    said, heard = _words(prev), _words(entry)
    return bool(said and heard) and heard[0]["start"] < said[-1]["end"]


def agent_tail(prev: dict, entry: dict) -> bool:
    """An agent entry listed after a caller's but begun before it: the rest of a line the caller
    spoke over (voice/retell_llm/call.py `_talked_over`)."""
    heard, said = _words(prev), _words(entry)
    return bool(heard and said) and said[0]["start"] < heard[0]["start"]


def _runs(entries: list[dict]) -> list[dict]:
    """Retell's transcript as runs of one speaker: consecutive entries of the same role joined, and
    an agent tail folded back into the agent line it belongs to, the way the socket reads it."""
    runs: list[dict] = []
    for i, entry in enumerate(entries):
        role = "agent" if entry.get("role") == "agent" else "caller"
        text = (entry.get("content") or "").strip()
        if not text:
            continue
        prev = entries[i - 1] if i > 0 else None
        words = _words(entry)
        at = words[0]["start"] if words else None
        over = bool(prev and role == "caller" and prev.get("role") == "agent" and talked_over(prev, entry))
        if role == "agent" and prev and prev.get("role") != "agent" and agent_tail(prev, entry):
            for run in reversed(runs):
                if run["role"] == "agent":
                    run["text"] += f" {text}"
                    run["tail"] = True
                    break
            continue
        if runs and runs[-1]["role"] == role:
            runs[-1]["text"] += f" {text}"
            runs[-1]["over"] = runs[-1]["over"] or over
            continue
        runs.append({"role": role, "text": text, "at": at, "over": over, "tail": False})
    return runs


def _mmss(value: float | None) -> str:
    if value is None:
        return ""
    s = int(value)
    return f"{s // 60}:{s % 60:02d}"


def heard(trace: dict, retell: dict | None) -> list[dict]:
    """The server's lines beside what Retell heard, one row per run of one speaker. Without
    Retell's object the Retell column is empty. A row is marked when the caller spoke over the
    agent, when an agent line was split by the caller's words, and when the two texts differ."""
    start = parse_iso(trace["call"]["started_at"])
    server_runs: list[dict] = []
    before: list[dict] = []  # tool calls made before anyone spoke (the call's start)
    for e in trace["events"]:
        if e["type"] != "turn":
            if e["tool"] not in LIFECYCLE:
                (server_runs[-1]["tools"] if server_runs else before).append(_tool_row(start, e))
            continue
        role = e["speaker"]
        if server_runs and server_runs[-1]["role"] == role:
            server_runs[-1]["text"] += f" {e['text']}"
            continue
        server_runs.append({"role": role, "text": e["text"], "clock": clock(start, e["at"]),
                            "safety": e.get("safety_action") or "", "tools": []})
    if server_runs and before:
        server_runs[0]["tools"] = before + server_runs[0]["tools"]
    retell_runs = _runs((retell or {}).get("transcript_object") or []) if retell else []
    rows = []
    for i in range(max(len(server_runs), len(retell_runs))):
        s = server_runs[i] if i < len(server_runs) else None
        r = retell_runs[i] if i < len(retell_runs) else None
        role = (s or r)["role"]
        if s and r and s["role"] != r["role"]:
            r = None  # the runs no longer line up; show the server's alone rather than mislead
        differs = bool(s and r and _norm(s["text"]) != _norm(r["text"]))
        rows.append({"role": role, "server": s["text"] if s else "", "clock": s["clock"] if s else "",
                     "retell": r["text"] if r else "", "retell_at": _mmss(r["at"]) if r else "",
                     "over": bool(r and r["over"]), "tail": bool(r and r["tail"]),
                     "differs": differs, "safety": s["safety"] if s else "",
                     "tools": s["tools"] if s else []})
    return rows


def _tool_row(start: datetime, e: dict) -> dict:
    """A tool call as the transcript lists it, under the line it followed."""
    return {"id": e.get("id"), "tool": e["tool"], "label": _tool(e["tool"]), "ok": e["ok"],
            "refused": e["refused"] or "", "gate": e["gate"] or "", "say": e["say"] or "",
            "error": e["error"] or "", "ms": e["ms"], "clock": clock(start, e["at"]),
            "args": json.dumps(e["args"], indent=2, ensure_ascii=False),
            "writes": e["tool"] in WRITES}


BOOKING_TOOLS = ("book_appointment", "read_back", "find_slots", "reschedule_appointment",
                 "cancel_appointment", "record_move")
FLAG_TOOLS = ("flag_person", "request_transfer")
# The tools that can write each kind of record, for matching a record to a tool call by time.
WRITERS = {
    "bookings": BOOKING_TOOLS,
    "flags": FLAG_TOOLS + BOOKING_TOOLS,
    "callbacks": FLAG_TOOLS + ("record_lead",) + BOOKING_TOOLS,
    "leads": ("record_lead",),
}
# The result field that names a record of each kind; a booking's is its ServiceTitan job id.
ID_FIELDS = {"bookings": "booking_id", "flags": "flag_id", "callbacks": "callback_id",
             "leads": "lead_id"}
AMBIGUOUS = "No record matched by id; more than one tool ran in that second."


def tool_records(conn: sqlite3.Connection, call_id: str, event: dict, result: dict | None,
                 fsm, tenant) -> dict:
    """What one tool call wrote, and how each kind of record was matched to it. By id first: a
    booking by the ServiceTitan job id its result returned, a flag, callback or lead by its own
    id. Without an id (find_slots and read_back return none, and a result may be gone from the
    log), by time: a record made on this call in the request's second, but only when no other
    tool that writes that kind of record ran in the same second, since times are kept to the
    second. Otherwise nothing is shown for that kind, rather than another call's guess. The
    ServiceTitan job behind each booking is read live, so the page proves the write landed."""
    data = (result or {}).get("data") or {}
    at = event["at"]
    tool = event["tool"]

    def rows(sql: str, params=()) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, params)]

    def alone_in_second(kind: str) -> bool:
        writers = WRITERS[kind]
        same = conn.execute(
            f"SELECT count(*) FROM tool_calls WHERE call_id = ? AND at = ? AND tool IN "
            f"({','.join('?' * len(writers))})", (call_id, at, *writers)).fetchone()[0]
        return same == 1

    matched: dict[str, str] = {}

    def find(kind: str, table: str, id_column: str = "id") -> list[dict]:
        value = data.get(ID_FIELDS[kind])
        if isinstance(value, int):
            matched[kind] = "id"
            return rows(f"SELECT * FROM {table} WHERE call_id = ? AND {id_column} = ? ORDER BY id",
                        (call_id, value))
        if not alone_in_second(kind):
            matched[kind] = "ambiguous"
            return []
        found = rows(f"SELECT * FROM {table} WHERE call_id = ? AND created_at = ? ORDER BY id",
                     (call_id, at))
        if found:
            matched[kind] = "time"
        return found

    bookings = find("bookings", "bookings", "fsm_job_id") if tool in BOOKING_TOOLS else []
    for b in bookings:
        b["request"] = json.loads(b.pop("request_json") or "{}")
        b["reasons"] = b["request"].get("priority_reasons") or []
    ids = [b["id"] for b in bookings]
    dispatches = rows(
        f"SELECT * FROM dispatches WHERE booking_id IN ({','.join('?' * len(ids))}) ORDER BY id",
        ids) if ids else []
    flags = find("flags", "flags") if tool in FLAG_TOOLS else []
    # A booking's own callback (an approval check, an urgent visit's follow-up) has no id in
    # the result, so it is matched by time.
    callbacks = find("callbacks", "callbacks") if tool in WRITERS["callbacks"] else []
    leads = find("leads", "leads") if tool == "record_lead" else []
    for lead in leads:
        lead["request"] = json.loads(lead.pop("request_json") or "{}")
    jobs = [servicetitan_job(fsm, tenant, b["fsm_job_id"]) for b in bookings if b["fsm_job_id"]]
    return {"bookings": bookings, "dispatches": dispatches, "flags": flags,
            "callbacks": callbacks, "leads": leads, "jobs": jobs, "matched": matched,
            "ambiguous": AMBIGUOUS if "ambiguous" in matched.values() else ""}


def _tag_name(tenant, tag_id: int) -> str:
    """A job tag by the label the company gave it (agent.yaml fsm.label_tag_ids), else its id."""
    names = {v: k for k, v in (tenant.fsm.get("label_tag_ids") or {}).items()}
    return names.get(tag_id, f"tag {tag_id}")


def servicetitan_job(fsm, tenant, job_id: int) -> dict:
    """A job as ServiceTitan holds it now, with its appointments, customer and location. Reads
    only; a read that fails says why instead."""
    try:
        job = fsm.job(job_id)
        appointments = fsm.appointments(job_id)
        customer = fsm.customer(job["customerId"])
        location = fsm.location(job["locationId"])
    except (FsmUnavailable, FsmRejected) as exc:
        return {"id": job_id, "error": str(exc) or exc.__class__.__name__}
    address = location.get("address") or {}
    return {
        "id": job_id,
        "number": job.get("jobNumber"),
        "status": job.get("jobStatus"),
        "type": tenant.job_type_codes.get(job.get("jobTypeId"), job.get("jobTypeId")),
        "priority": job.get("priority"),
        "summary": job.get("summary") or "",
        "tags": [_tag_name(tenant, t) for t in job.get("tagTypeIds") or []],
        "customer": f"{customer.get('name', '')}, customer {customer.get('id')}",
        "location": ", ".join(str(p) for p in (address.get("street"), address.get("unit"),
                                               address.get("zip")) if p),
        "appointments": [
            {"id": a["id"], "status": a.get("status"),
             "window": f"{_when(tenant, a.get('arrivalWindowStart'))} to "
                       f"{tenant.local(parse_iso(a['arrivalWindowEnd'])).strftime('%-I:%M %p')}"
             if a.get("arrivalWindowStart") and a.get("arrivalWindowEnd") else ""}
            for a in appointments
        ],
        "raw": json.dumps(job, indent=2, ensure_ascii=False),
    }

def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def retell_facts(retell: dict | None) -> dict | None:
    """The few fields of Retell's call object the page shows: how it ended, its bill, its own
    latency percentiles, the recording."""
    if not retell:
        return None
    latency = retell.get("latency") or {}
    return {
        "disconnection": (retell.get("disconnection_reason") or "").replace("_", " "),
        "billed": ((retell.get("call_cost") or {}).get("combined_cost") or 0) / 100
        if retell.get("call_cost") else None,
        "latency": [(name, (latency.get(name) or {}).get("p50"), (latency.get(name) or {}).get("p90"))
                    for name in ("e2e", "llm", "tts", "asr") if latency.get(name)],
        "recording": retell.get("recording_url"),
    }
