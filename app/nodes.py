"""A call as a graph of nodes for the debug bench (app/bench.py): every step from the caller's line
to the agent's spoken reply, in order, in the lane of whoever did it, with its timing and its full
detail. Also the rule proof: each server rule the call reached and how it came out.

Built from the call record (app/trace.py) and the JSON log beside it (app/logs.py), which keeps
each request's result. The voice worker doesn't log the model requests it makes, so each request's
system prompt and messages are rebuilt from the same pieces the worker joins: the /calls/start
result through prompts/assemble.py, then the turns, the `[server]` notes and the tool results as
the worker hands them to the model. A timing the record doesn't hold is None, shown as missing.

Values are shown as recorded: the bench is admin only and admins see everything (DECISIONS.md 064).
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from prompts.assemble import greeting as built_greeting
from prompts.assemble import instructions, server_notes

from . import logs
from .db import parse_iso
from .plain import REFUSED
from .tenant import voice_lines
from .tools import TOOLS


def _as_recorded(value, _key: str = ""):
    return value

LANES = (
    ("caller", "Caller"),
    ("voice", "Voice worker"),
    ("model", "Model"),
    ("server", "Server"),
)
LANE_INDEX = {key: i for i, (key, _) in enumerate(LANES)}
# Lifecycle requests the voice layer makes; the rest are tools the model called.
LIFECYCLE = {"calls.start", "calls.end", "calls.transfer", "calls.recording", "turn"}
# Turn results whose text the worker speaks itself, word for word (voice/livekit/call.py).
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
SAFETY_TEXT = {
    "none": "No safety signal",
    "speak_lines": "Safety signal: the fixed lines are spoken next",
    "speak_variant": "Safety signal again: a short form of the lines is spoken next",
    "ask_check": "Unclear mention: the check question is spoken next",
    "cleared": "The caller said no to the check; the call carries on",
    "lines_said": "The agent line carried the fixed safety lines",
}
LOG_LIMIT = 1000
TENANTS_DIR = Path(os.environ.get("TENANTS_DIR") or Path(__file__).resolve().parents[1] / "tenants")

# Why the Retell socket cut a sentence the model wrote (voice/retell_llm/call.py _dropped), in
# the words the bench shows beside the struck text.
CUT_REASONS = {
    "repeats_said": "Repeat guard: already said in this reply",
    "narrates": "Reply filter: narrated its own steps",
    "explains_gate": "Reply filter: explained a server rule",
    "thanks_for_waiting": "Reply filter: thanked the caller for waiting",
    "after_question": "One question a reply",
    "ack_after_hold": "Second opener after the hold phrase",
    "goodbye_with_end_call": "Goodbye left to the hang-up",
    "lead_in_before_tool": "Lead-in before a tool call, never said",
    "interrupted": "Caller talked over it",
}
# Logged as a drop, but the reply was then said whole rather than leave the caller in silence.
NOT_CUT = {"fallback_spoke_whole_reply"}
# Why the server added a note to a caller's line (prompts/assemble.py SERVER_NOTES), by hint.
NOTE_REASONS = {
    "commercial": "the caller is a business, so this call can't book",
    "do_not_call": "the caller may not want calls from the company",
    "safety_check_cleared": "the caller said no to the safety check",
    "electrical_said": "the electrical advice had just been said",
    "electrical_answered": "the electrical advice was said a moment ago",
    "caller_holding": "the caller asked the agent to wait",
    "safety_stood_down": "the caller took back the smell or alarm",
    "safety_call": "the safety lines had been said and a person was flagged",
    "pace_slower": "the caller asked the agent to slow down",
    "pace_faster": "the caller asked the agent to speed up",
    "pace_address": "the caller wants the address said more slowly",
    "priority_pushed": "the caller pressed to be seen first with no new fact",
    "priority_pushed_asked": "the caller pressed to be seen first with no new fact",
    "preferred_name": "the caller asked to be called by another name",
    "preferred_name_refused": "the caller asked for a name the company doesn't use",
    "readback_changed": "the caller changed a detail after confirming the read-back",
    "technician_requested": "the caller asked for a particular technician",
    "approval_unclear": "who can approve the work isn't settled",
    "stage_booking": "the owner's guidance for booking applies",
    "stage_closing": "the owner's guidance for closing applies",
}

# LiveKit's default speech filter drops markdown and emoji before the voice speaks a line
# (livekit.agents DEFAULT_TTS_TEXT_TRANSFORMS). Its patterns, applied to a whole line, say what it
# dropped; the worker doesn't log that itself.
try:
    from livekit.agents.voice.transcription import filters as _speech
except ImportError:  # the tool server can run without the voice packages
    _speech = None


def speech_filter(line: str) -> str | None:
    """The line as LiveKit's default filter would hand it to the voice, or None without it."""
    if _speech is None:
        return None
    out = line
    for pattern, repl in _speech.FULL_LINE_PATTERNS + _speech.LINE_PATTERNS:
        out = pattern.sub(repl, out)
    for pattern, repl in _speech.INLINE_PATTERNS:
        out = pattern.sub(repl, out)
    return _speech.EMOJI_PATTERN.sub("", out)


def _clock(start: datetime, at: str) -> str:
    seconds = max(0, int((parse_iso(at) - start).total_seconds()))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _dumps(obj) -> str:
    return json.dumps(_as_recorded(obj), indent=2, default=str, ensure_ascii=False)


def _tool_output(row: dict | None) -> str | None:
    """What the worker hands the model as a tool's result: the envelope without empty fields
    (voice/livekit/tools.py compact)."""
    if row is None:
        return None
    envelope = {"ok": row["ok"], "say": row["say"], "data": row.get("result"), "refused": row["refused"]}
    return json.dumps(
        _as_recorded({k: v for k, v in envelope.items() if v not in (None, "", {})}),
        default=str, ensure_ascii=False,
    )


class Graph:
    """Collects nodes in order and keeps the conversation the model has seen so far."""

    def __init__(self, started: datetime):
        self.started = started
        self.nodes: list[dict] = []
        self.messages: list[dict] = []
        self.system: str | None = None
        self.system_note = ""
        self.start_id = ""
        self.turn_requests: list[dict] = []
        # The greeting as the bridge builds it, for a call from before the socket sent `said`.
        self.greeting = ""
        # The caller's line set off the safety lines, so the fixed line that follows is them.
        self.safety_next = False

    def add(self, lane: str, kind: str, title: str, summary: str, at: str | None,
            ms: float | None = None, ms_label: str = "", tone: str = "", **detail) -> dict:
        previous = self.nodes[-1]["lane"] if self.nodes else lane
        node = {
            "id": f"n{len(self.nodes) + 1}",
            "lane": lane,
            "col": LANE_INDEX[lane],
            "from_col": LANE_INDEX[previous],
            "kind": kind,
            "title": title,
            "summary": _as_recorded(summary),
            "at": _clock(self.started, at) if at else "",
            "ms": ms,
            "ms_label": ms_label,
            "tone": tone,
            "facts": [(k, _as_recorded(v, k)) for k, v in detail.pop("facts", [])],
            "blocks": detail.pop("blocks", []),
            "messages": detail.pop("messages", None),
            "refs": [],
        }
        self.nodes.append(node)
        return node

    def request(self, at: str) -> dict:
        """A model request with what the model was given at this point."""
        n = len(self.turn_requests) + 1
        node = self.add(
            "model", "model_request", "Model request",
            f"Request {n} of the turn, {len(self.messages)} messages",
            at, None, "first token",
            messages=[dict(m) for m in self.messages],
        )
        # The system prompt is the same on every request of a call, so it is shown once, on the
        # call's start, and each request links to it.
        if self.system is not None and self.start_id:
            node["refs"].append({"node": self.start_id, "title": "System prompt",
                                 "text": f"{len(self.system):,} characters, the same on every request"})
        self.turn_requests.append(node)
        return node


def build(conn, trace: dict, log_path) -> dict:
    """The node graph and rule proof for one call. `trace` is app.trace.timeline's result."""
    call = trace["call"]
    started = parse_iso(call["started_at"])
    rows = logs.read(log_path, call_id=call["id"], limit=LOG_LIMIT)
    by_request = {r["request_id"]: r for r in rows}
    turn_rows = {
        (r.get("result") or {}).get("seq"): r for r in rows if r["tool"] == "turn" and r["ok"]
    }
    g = Graph(started)
    scripted = True  # the greeting is spoken from the tenant's lines, not written by the model
    need_request = False
    for event in trace["events"]:
        if event["type"] == "turn":
            row = turn_rows.get(event["seq"])
            if event["speaker"] == "caller":
                scripted, need_request = _caller(g, event, row)
            else:
                _agent(g, event, scripted, need_request, (row or {}).get("args") or {})
                # Retell reports one spoken line in fragments: a fixed line stays fixed until the
                # caller speaks again.
                need_request = False
            continue
        row = by_request.get(event["request_id"])
        if event["tool"] == "calls.start":
            _start(g, event, row)
            continue
        if event["tool"] == "calls.end":
            g.add("server", "end", "Call ended", call["end_reason"] or "ended", event["at"],
                  event["ms"], "server", facts=[("Outcome", call["outcome"] or "-")])
            continue
        if event["tool"] in LIFECYCLE:
            g.add("server", "lifecycle", event["tool"], "Request from the voice worker",
                  event["at"], event["ms"], "server", blocks=[_args_block(event)])
            continue
        if need_request:
            g.request(event["at"])
        _tool(g, event, row)
        need_request = True
    return {
        "lanes": LANES,
        "nodes": join(g.nodes),
        "steps": g.nodes,
        "proof": proof(trace, turn_rows),
        "logged": bool(rows),
        # What /calls/start returned (customers matched, membership, open jobs), from the same log
        # read, so the bench's call page reads the log once.
        "start": next((r.get("result") or {} for r in rows if r["tool"] == "calls.start"), None),
    }


def _start(g: Graph, event: dict, row: dict | None) -> None:
    data = (row or {}).get("result") or {}
    customers = data.get("customers") or []
    version = (data.get("house_rules") or {}).get("version")
    summary = (
        f"Lookup before hello: {len(customers)} customer{'s' if len(customers) != 1 else ''} on "
        f"the number{', linked to an earlier call' if data.get('linked_call') else ''}"
    )
    facts = [
        ("Caller ID shown", (data.get("caller") or {}).get("known_number")),
        ("After hours", data.get("after_hours")),
        ("Scheduling system up", data.get("fsm_available")),
        ("Rules version", version if version is not None else "defaults"),
        ("Tools offered", f"{len(TOOLS)}: " + ", ".join(TOOLS)),
    ]
    blocks = [_args_block(event)]
    try:
        slug = (data.get("tenant") or {}).get("slug") or ""
        g.greeting = built_greeting(data, voice_lines(TENANTS_DIR / slug)) if slug else ""
    except (OSError, KeyError, TypeError, ValueError):
        g.greeting = ""
    if row is None:
        g.system_note = "The /calls/start result is not in the log, so the prompt can't be rebuilt."
    else:
        try:
            g.system = _as_recorded(instructions(_as_recorded(data), g.started))
            g.system_note = (
                "Rebuilt with prompts/assemble.py from this call's /calls/start result and the "
                "prompt files as they are now; the worker doesn't log the request itself."
            )
        except (KeyError, TypeError) as err:
            g.system_note = f"Couldn't rebuild the prompt: {err}."
        blocks.append({"title": "Start result", "text": _dumps(
            {k: v for k, v in data.items() if k != "knowledge"}), "open": True})
        g.messages.append({"role": "system", "text": "The system prompt, linked above."})
    node = g.add("server", "start", "Call start", summary, event["at"], event["ms"], "server",
                 facts=facts, blocks=blocks)
    g.start_id = node["id"]
    if g.system is not None:
        node["blocks"].append({"title": "System prompt", "text": g.system,
                               "note": g.system_note, "open": False})
    else:
        node["facts"].append(("System prompt", g.system_note))


def _caller(g: Graph, event: dict, row: dict | None) -> tuple[bool, bool]:
    """Nodes for a caller line. Returns (the next agent line is spoken from the server's text,
    the model is asked next)."""
    timings = event["timings"] or {}
    g.turn_requests = []
    g.add("caller", "caller_line", "Caller", event["text"], event["at"],
          timings.get("transcript_ms"), "final transcript",
          facts=[("Line", event["seq"])], blocks=[{"title": "Said", "text": _as_recorded(event["text"]), "open": True}])
    g.add("voice", "end_of_turn", "End of turn", "Decided the caller had finished", event["at"],
          timings.get("end_of_turn_ms"), "end of turn",
          facts=[(k, v) for k, v in sorted(timings.items())])
    result = (row or {}).get("result") or {}
    safety = result.get("safety") or {}
    action = safety.get("action") or "none"
    hints = result.get("hints") or []
    tone = "warn" if action in ("speak_lines", "speak_variant") else "hold" if action == "ask_check" else ""
    summary = SAFETY_TEXT.get(action, action)
    if safety.get("kind") and action != "none":
        summary += f" ({safety['kind'].replace('_', ' ')})"
    facts = [("Safety action", action), ("Hints", ", ".join(hints) or "none")]
    blocks = [{"title": "Turn result", "text": _dumps(result), "open": False}] if row else []
    g.add("server", "turn_check", "Turn check", summary, event["at"],
          row["ms"] if row else None, "server", tone=tone, facts=facts, blocks=blocks)
    notes = server_notes(hints)
    if notes:
        reasons = [NOTE_REASONS[h] for h in hints if h in NOTE_REASONS]
        if any(h.startswith("units:") for h in hints):
            reasons.append("the caller named more than one unit")
        if any(h.startswith("not_offered:") for h in hints):
            reasons.append("the caller asked for work the company doesn't do")
        why = "; ".join(dict.fromkeys(reasons)) or "the turn check returned " + ", ".join(hints)
        g.add("server", "server_note", "Note to the model",
              "Server added a note to the caller's line before the model saw it", event["at"],
              facts=[("When", "Before the model's next request, on this caller line"),
                     ("Because", why)],
              blocks=[{"title": "Caller's line", "text": _as_recorded(event["text"]), "open": True},
                      {"title": "Note added", "text": _as_recorded("\n".join(notes)), "open": True}])
    g.safety_next = action in ("speak_lines", "speak_variant")
    content = event["text"] + ("\n" + "\n".join(notes) if notes else "")
    g.messages.append({"role": "user", "text": _as_recorded(content)})
    spoken = action in SPOKEN_ACTIONS and bool(safety.get("text"))
    return spoken, not spoken


def _tool(g: Graph, event: dict, row: dict | None) -> None:
    status = "ok" if event["ok"] else f"refused {event['refused']}"
    summary = status + (f", says: {event['say']}" if event["say"] else "")
    facts = [("Result", status), ("Gate", event["gate"] or "none"), ("Request id", event["request_id"])]
    if event["error"]:
        facts.append(("Error", event["error"]))
    blocks = [_args_block(event)]
    output = _tool_output(row)
    if output is not None:
        blocks.append({"title": "Result to the model", "open": True,
                       "text": json.dumps(json.loads(output), indent=2, ensure_ascii=False)})
    else:
        facts.append(("Result to the model", "not in the log"))
    tone = "" if event["ok"] else "hold"
    g.add("server", "tool", event["tool"], summary, event["at"], event["ms"], "server",
          tone=tone, facts=facts, blocks=blocks)
    if event["gate"]:
        text, rule_tone = GATE_TEXT.get(event["gate"]) or REFUSED.get(
            event["gate"], (f"Gate {event['gate']} fired", "hold"))
        if event["ok"]:
            text, rule_tone = "Allowed past a gate: " + event["gate"].replace("_", " "), "warn"
        g.add("server", "rule", "Rule", text, event["at"], tone=rule_tone,
              facts=[("Gate", event["gate"]), ("Outcome", "allowed" if event["ok"] else "held")])
    g.messages.append({"role": "assistant", "text": f"[tool call] {event['tool']} "
                       + json.dumps(_as_recorded(event["args"]), default=str, ensure_ascii=False)})
    g.messages.append({"role": "tool", "text": output or "(not in the log)"})


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def cut_view(heard: str, said: str, cuts: list[dict]) -> dict:
    """What the caller heard against what was sent and written: the heard text, then each cut
    part with the reason. A line Retell heard shorter than what was sent, word for word up to
    where it stops, was talked over; anything else Retell heard differently isn't a cut."""
    parts = []
    heard_words, said_words = _words(heard), _words(said)
    if said and heard_words and len(said_words) > len(heard_words) \
            and said_words[: len(heard_words)] == heard_words:
        tail = said.split()[len(heard.split()):]
        if tail:
            parts.append({"text": " ".join(tail), "reason": "interrupted"})
    for cut in cuts or []:
        parts.append({"text": cut.get("text", ""), "reason": cut.get("reason", "")})
    return {
        "heard": heard,
        "said": said,
        "cut": [{**p, "struck": p["reason"] not in NOT_CUT,
                 "tag": CUT_REASONS.get(p["reason"], p["reason"].replace("_", " "))}
                for p in parts if p["text"]],
    }


def _agent(g: Graph, event: dict, scripted: bool, need_request: bool, args: dict) -> None:
    timings = event["timings"] or {}
    text = event["text"]
    said = args.get("said") or ""
    recorded = bool(said or args.get("cuts"))
    if not said and scripted and g.greeting and not any(n["kind"] == "spoken" for n in g.nodes):
        said = g.greeting
    view = cut_view(text, said, args.get("cuts") or [])
    if not recorded:
        view["note"] = ("What was sent before Retell heard it is only recorded on calls after the "
                        "socket started posting it" + ("; the greeting is rebuilt from the "
                                                       "tenant's lines" if said else ""))
    if scripted:
        safety_lines = bool(event["safety_action"]) or g.safety_next
        node = g.add("voice", "spoken", "Spoken", text, event["at"], timings.get("tts_first_byte_ms"),
                     "first audio", tone="warn" if safety_lines else "",
                     facts=[("Source", "fixed text from the server or the tenant's lines, no model"),
                            ("Line", event["seq"])],
                     blocks=[{"title": "Spoken", "text": _as_recorded(text), "open": True}])
        node["view"] = view
        node["source"] = "server"
        g.messages.append({"role": "assistant", "text": _as_recorded(text)})
        return
    if need_request or not g.turn_requests:
        g.request(event["at"])
    first, last = g.turn_requests[0], g.turn_requests[-1]
    for key, label in (("prompt_tokens", "Prompt tokens"), ("cache_read_tokens", "Cache read"),
                       ("cache_write_tokens", "Cache write"), ("llm_output_tokens", "Output tokens"),
                       ("llm_requests", "Requests kept"), ("llm_cancelled", "Requests thrown away")):
        if key in timings:
            first["facts"].append((label, timings[key]))
    last["ms"] = timings.get("llm_first_token_ms")
    wrote = " ".join([said or text, *(c["text"] for c in view["cut"] if c["reason"] != "interrupted")])
    g.add("model", "model_wrote", "Model wrote", wrote, event["at"],
          facts=[("Line", event["seq"]), ("Characters", len(wrote))],
          blocks=[{"title": "Text", "text": _as_recorded(wrote), "open": True}])
    filtered = speech_filter(text)
    if filtered is not None and filtered != text:
        g.add("voice", "filter", "Speech filter", f"Dropped markdown or emoji: {_dropped(text, filtered)}",
              event["at"], tone="hold",
              blocks=[{"title": "After the filter", "text": _as_recorded(filtered), "open": True}],
              facts=[("How it's known", "LiveKit's default filter patterns, applied to the line")])
    reply = timings.get("reply_ms")
    node = g.add("voice", "spoken", "Spoken", text if filtered is None else filtered, event["at"],
                 timings.get("tts_first_byte_ms"), "first audio",
                 tone="warn" if event["safety_action"] else "",
                 facts=[("Reply delay", f"{reply:.0f} ms" if reply is not None else "missing"),
                        ("Tool time on the worker", timings.get("tool_ms", "missing")),
                        ("Bridge line played", bool(timings.get("bridge")))])
    node["view"] = view
    node["source"] = "model"
    g.messages.append({"role": "assistant", "text": _as_recorded(text)})


# What a gate the server-led flow and the transfer rules hold means, for the bench's rule steps
# (app/plain.py REFUSED has the owner view's codes).
GATE_TEXT = {
    "transfer_not_asked": (
        "Held a transfer: the caller hadn't asked for a person in words the server knows.", "hold"),
    "transfer_safety": (
        "Held a transfer the model asked for on a safety call; the server decides those.", "hold"),
    "flow_visit_questions": ("Held until the visit questions were asked.", "hold"),
    "flow_read_back_first": ("Held until the address and phone were read back.", "hold"),
    "confirm_first": ("Held until the caller confirmed the read-back.", "hold"),
    "anything_else_first": ("Held until the agent asked if there was anything else.", "hold"),
    "move_first": ("Held: the visit is moved before anything else.", "hold"),
    "on_call_first": ("Held: after hours the on-call technician is tried first.", "hold"),
    "open_job": ("The caller already has an open job; asked about it first.", "hold"),
    "identity_mismatch": ("The name didn't match the record; asked again.", "hold"),
    "identity_check": ("Checked who's calling before changing a record.", "hold"),
    "read_back_confirmed": ("Already confirmed, so no second read-back.", "hold"),
    "already_booked": ("Already booked on this call.", "hold"),
}

# Steps the Steps tab shows as one row, opened to see each step inside.
CALLER_PARTS = ("end_of_turn", "turn_check")
REPLY_PARTS = ("model_request", "model_wrote", "filter", "spoken")
# Successful tools that are a proof point on their own.
PROOF_OK = {
    "book_appointment": "Booked",
    "reschedule_appointment": "Moved",
    "cancel_appointment": "Cancelled",
    "calls.handoff_done": "Handed to a person",
    "request_transfer": "Transfer allowed",
    "flag_person": "Person flagged",
}


def join(nodes: list[dict]) -> list[dict]:
    """The steps as the Steps tab lists them: a caller line with its end of turn and turn check
    is one row, a reply (the model's requests, what it wrote, any filter and what was spoken) is
    one "Agent said" row, a run of fragments of one reply is one row, and a tool with the rule it
    met is one row. A model request that led to a tool joins that tool's row. Each row keeps the
    steps inside it, and marks a rule that held or fired as a proof point."""
    rows: list[dict] = []
    i = 0
    while i < len(nodes):
        n = nodes[i]
        kind = n["kind"]
        if kind == "caller_line":
            parts = [n]
            while i + 1 < len(nodes) and nodes[i + 1]["kind"] in CALLER_PARTS:
                i += 1
                parts.append(nodes[i])
            row = _caller_row(parts)
            earlier = rows[-1] if rows else None
            if earlier and earlier["kind"] == "caller_line" \
                    and _words(n["summary"])[: len(_words(earlier["summary"]))] == _words(earlier["summary"]):
                # Retell sent the caller's line again as they kept talking: one row, the last text.
                row["joined"] = [dict(earlier, joined=[]), *earlier["joined"], *row["joined"]]
                row["at"] = earlier["at"]
                row["proof"] = row.get("proof") or earlier.get("proof", "")
                row["tone"] = row["tone"] or earlier["tone"]
                rows[-1] = row
            else:
                rows.append(row)
        elif kind in REPLY_PARTS:
            parts = [n]
            while i + 1 < len(nodes) and nodes[i + 1]["kind"] in REPLY_PARTS:
                i += 1
                parts.append(nodes[i])
            if not any(p["kind"] == "spoken" for p in parts):
                # Requests that only led to a tool: they join the tool's row.
                if i + 1 < len(nodes) and nodes[i + 1]["kind"] == "tool":
                    i += 1
                    tool_parts = [nodes[i]]
                    if i + 1 < len(nodes) and nodes[i + 1]["kind"] == "rule":
                        i += 1
                        tool_parts.append(nodes[i])
                    rows.append(_tool_row(tool_parts, parts))
                else:
                    rows.extend(dict(p, joined=[]) for p in parts)
            else:
                rows.append(_reply_row(parts))
        elif kind == "tool":
            parts = [n]
            if i + 1 < len(nodes) and nodes[i + 1]["kind"] == "rule":
                i += 1
                parts.append(nodes[i])
            rows.append(_tool_row(parts, []))
        else:
            row = dict(n, joined=[])
            if kind == "server_note":
                row["proof"] = "Server note"
            rows.append(row)
        i += 1
    # A row keeps its first step's id, so links to a step (the system prompt on the call's start)
    # still land; its edge comes from the row above.
    previous = None
    for row in rows:
        row["from_col"] = previous if previous is not None else row["col"]
        previous = row["col"]
    return rows


def _caller_row(parts: list[dict]) -> dict:
    line, *rest = parts
    check = next((p for p in rest if p["kind"] == "turn_check"), None)
    row = dict(line, joined=rest)
    if check and check["tone"]:
        row["tone"] = check["tone"]
        row["proof"] = check["summary"]
    return row


def _reply_row(parts: list[dict]) -> dict:
    spoken = [p for p in parts if p["kind"] == "spoken"]
    requests = [p for p in parts if p["kind"] == "model_request"]
    model = any(p.get("source") == "model" for p in spoken)
    text = " ".join(p["summary"] for p in spoken)
    cuts = [c for p in spoken for c in (p.get("view") or {}).get("cut", [])]
    notes = [p["view"]["note"] for p in spoken if (p.get("view") or {}).get("note")]
    first = requests[0] if requests and requests[0]["ms"] is not None else spoken[0]
    lane = "model" if model else "voice"
    row = dict(spoken[0], lane=lane, col=LANE_INDEX[lane], kind="reply", title="Agent said",
               summary=text, ms=first["ms"], ms_label=first["ms_label"],
               tone=next((p["tone"] for p in spoken if p["tone"]), ""),
               facts=[("Source", "the model" if model else "the server's or the company's fixed words"),
                      ("Spoken in", f"{len(spoken)} part{'s' if len(spoken) != 1 else ''}"),
                      ("Model requests", len(requests))],
               blocks=[], messages=None, refs=[], joined=parts,
               view={"heard": text, "cut": cuts, "note": notes[0] if notes else ""})
    if row["tone"] == "warn" and not model:
        row["proof"] = "Safety lines, word for word"
    return row


def _tool_row(parts: list[dict], requests: list[dict]) -> dict:
    tool, *rest = parts
    rule = next((p for p in rest if p["kind"] == "rule"), None)
    row = dict(tool, joined=[*requests, *rest])
    if rule:
        row["proof"] = rule["summary"]
        row["tone"] = rule["tone"] or row["tone"]
    elif tool["title"] in PROOF_OK and "refused" not in tool["summary"]:
        row["proof"] = PROOF_OK[tool["title"]]
    if requests:
        row["facts"] = [*tool["facts"], ("Asked for by", "the model")]
    return row


def _dropped(text: str, filtered: str) -> str:
    gone = sorted(set(text) - set(filtered) - {" "})
    return " ".join(gone) or "spacing"


def _args_block(event: dict) -> dict:
    return {"title": "Arguments", "text": _dumps(event["args"]), "open": True}


# Each server rule the proof list reports: what it guards, the gates that show it held, and the
# tools whose success shows it was checked and passed.
RULES = (
    ("Safety lines first", "No booking while a safety signal is open and the lines aren't said",
     {"gate_safety"}, {"book_appointment", "reschedule_appointment"}),
    ("Read-back before booking", "The address and phone are read back and confirmed first",
     {"gate_read_back", "flow_read_back_first", "read_back_first", "confirm_first"},
     {"book_appointment", "read_back"}),
    ("Questions before booking", "The visit questions are asked before a time is offered",
     {"flow_visit_questions", "visit_questions_first"}, {"find_slots", "book_appointment"}),
    ("Address heard and found", "Only a street and ZIP the caller said, found on the map",
     {"address_not_heard", "address_several", "address_not_found", "zip_not_heard",
      "zip_mismatch", "address_lookup_down"}, {"find_address", "read_back"}),
    ("Person only when asked",
     "A transfer needs the caller to ask; a safety call is put through by the server",
     {"transfer_not_asked", "transfer_safety", "transfer_closed", "transfer_unavailable",
      "transfer_commercial"}, {"request_transfer", "calls.handoff_done"}),
    ("Service area", "Only ZIPs the company serves are booked",
     {"out_of_area"}, {"check_service_area", "book_appointment"}),
    ("Homes only", "A commercial caller is never booked",
     {"customer_type"}, {"book_appointment", "find_slots"}),
    ("Caller's own records", "Lookups and changes stay on the customers matched to this call",
     {"not_owner", "scope", "ownership", "identity_mismatch", "identity_check"},
     {"get_customer_history", "reschedule_appointment",
                                           "cancel_appointment"}),
    ("Per-call limits", "One booking and a few lookups per call, then a person",
     {"limit"}, {"book_appointment", "find_customer", "get_customer_history"}),
    ("Open times only", "A booking takes a window the schedule offered",
     {"invalid_slot", "slot_taken"}, {"book_appointment", "reschedule_appointment"}),
)


def proof(trace: dict, turn_rows: dict) -> list[dict]:
    """Each server rule: checked how many times on this call, and whether it held something back,
    passed, or was never reached."""
    tools = [e for e in trace["events"] if e["type"] == "tool" and e["tool"] not in LIFECYCLE]
    caller_lines = [e for e in trace["events"] if e["type"] == "turn" and e["speaker"] == "caller"]
    signals = [r for r in turn_rows.values()
               if ((r.get("result") or {}).get("safety") or {}).get("action") in SPOKEN_ACTIONS]
    out = [{
        "rule": "Safety check on every caller line",
        "guards": "A safety signal switches the call to the fixed lines at once",
        "checked": len(caller_lines),
        "outcome": "fired" if signals else ("passed" if caller_lines else "not reached"),
        "evidence": (f"{len(signals)} line{'s' if len(signals) != 1 else ''} set off a check or "
                     "the lines") if signals else "No signal on any line",
    }]
    for name, guards, gates, checked_by in RULES:
        held = [e for e in tools if (e["gate"] or e["refused"]) in gates]
        checked = [e for e in tools if e["tool"] in checked_by]
        if held:
            outcome = "fired"
            evidence = ", ".join(f"{e['tool']} at {e['at'][11:19]}" for e in held)
        elif checked:
            outcome = "passed"
            evidence = ", ".join(sorted({e["tool"] for e in checked}))
        else:
            outcome, evidence = "not reached", "No tool this rule guards was called"
        out.append({"rule": name, "guards": guards, "checked": len(checked) + len(
            [e for e in held if e not in checked]), "outcome": outcome, "evidence": evidence})
    override = [e for e in tools if e["gate"] == "safety_override"]
    if override:
        out.append({"rule": "Safety override", "guards": "A booking on a safety call, after the lines",
                    "checked": len(override), "outcome": "allowed",
                    "evidence": ", ".join(e["at"][11:19] for e in override)})
    return out

