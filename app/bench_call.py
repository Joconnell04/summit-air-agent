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

from . import consistency, cost, failure_grader
from .db import parse_iso

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
# voice/archive/livekit, the earlier LiveKit worker), in the order they happen, with a short label.
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
# A priority rule's source, from its name in bookings.priority_rules (app/priority.py).
SIDE_MODEL_RULE = "side_model"
QUOTE = re.compile(r'caller said "(.*)"')
LIFECYCLE = {"calls.start", "calls.end", "calls.transfer", "calls.recording", "turn"}


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
    from .nodes import RULES

    for name, _, gates, _ in RULES:
        if code in gates:
            return name
    return code.replace("_", " ")


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

    def add(at, kind, decision, rule="", source="", outcome="", say="", alarm=False):
        rows.append({"at": at, "clock": clock(start, at), "turn": turn, "kind": kind,
                     "decision": decision, "rule": rule, "source": source, "outcome": outcome,
                     "say": say or "", "alarm": alarm})

    for event in trace["events"]:
        if event["type"] == "turn":
            turn = event["seq"]
            action = event.get("safety_action")
            if event["speaker"] == "caller" and action in SAFETY_ACTIONS:
                add(event["at"], "Safety", SAFETY_ACTIONS[action], "Safety check on every line",
                    "Caller's words", "fired", event["text"], alarm=action != "cleared")
            continue
        tool = event["tool"]
        if tool in LIFECYCLE:
            continue
        if not event["ok"]:
            code = event["gate"] or event["refused"] or "refused"
            add(event["at"], "Refused", tool, _rule_name(code), "Server gate", "held", event["say"],
                alarm=True)
        elif event["gate"]:
            add(event["at"], "Allowed", tool, _rule_name(event["gate"]), "Server gate",
                event["gate"].replace("_", " "), event["say"])
        elif event["say"]:
            add(event["at"], "Server line", tool, "", "Server", "", event["say"])

    if call.get("safety_signal_at"):
        lines_at = call.get("safety_lines_at")
        took = (f"{(parse_iso(lines_at) - parse_iso(call['safety_signal_at'])).total_seconds():.1f} s"
                if lines_at else "never said")
        turn = 0
        add(lines_at or call["safety_signal_at"], "Safety",
            f"{(call.get('safety_kind') or 'safety').upper()} lines said", "Safety lines first",
            "Server", took, alarm=not lines_at)

    for b in trace["bookings"]:
        rules = json.loads(b["priority_rules"]) if b.get("priority_rules") else []
        reasons = json.loads(b["priority_reason_list"]) if b.get("priority_reason_list") else []
        turn = 0
        if not rules and not reasons:
            continue
        for i, reason in enumerate(reasons):
            rule = rules[i] if i < len(rules) else ""
            add(None, "Priority", f"{b['priority']}: {QUOTE.sub('', reason).rstrip(', ')}",
                rule.replace("_", " "), _priority_source(rule, reason), b["priority"],
                (QUOTE.search(reason) or [None, ""])[1])
    for d in conn.execute("SELECT * FROM dispatches WHERE call_id = ? ORDER BY id", (call_id,)):
        add(d["at"], "Dispatch", d["job_type"].replace("_", " "), "Dispatch at read-back",
            "Server", "replaced" if d["replaced_at"] else "booked")
    for o in trace["out_of_area"]:
        add(o["at"], "Refused", f"ZIP {o['zip']}", "Service area", "Server", "out of area", alarm=True)
    for c in trace["identity_checks"]:
        add(None, "Identity", f"customer {c['customer_id']}", "Name and address check", "Server",
            c["status"])
    label = conn.execute("SELECT label, reason, at FROM call_labels WHERE call_id = ?",
                         (call_id,)).fetchone()
    if label:
        turn = 0
        add(label["at"], "Label", label["label"], "Call label", "Server", label["reason"])
    reason = end_call_reason(trace)
    if call.get("ended_at"):
        add(call["ended_at"], "End", (call.get("end_reason") or "").replace("_", " "), "",
            "Agent" if reason else "Voice layer", (reason or "").replace("_", " "))
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


def customers(conn: sqlite3.Connection, trace: dict, start: dict | None) -> list[dict]:
    """The customers matched to the call, how they were matched, and their membership and open
    jobs as /calls/start returned them. `start` is that result, from the log rows the step graph
    already read (app/nodes.py build), so this reads no log of its own."""
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
                    "membership": (membership.get("name") or membership.get("type") or "member")
                    if isinstance(membership, dict) else (membership or ""),
                    "locations": len(c.get("locations") or []),
                    "open_jobs": len(c.get("open_jobs") or [])})
    for customer_id, m in matched.items():
        out.append({"id": customer_id, "name": "", "type": m["customer_type"],
                    "matched_by": m["matched_by"].replace("_", " "), "membership": "",
                    "locations": None, "open_jobs": None})
    return out


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
    for e in trace["events"]:
        if e["type"] != "turn":
            continue
        role = e["speaker"]
        if server_runs and server_runs[-1]["role"] == role:
            server_runs[-1]["text"] += f" {e['text']}"
            continue
        server_runs.append({"role": role, "text": e["text"], "clock": clock(start, e["at"]),
                            "safety": e.get("safety_action") or ""})
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
                     "differs": differs, "safety": s["safety"] if s else ""})
    return rows


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
