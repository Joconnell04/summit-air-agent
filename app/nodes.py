"""A call as a graph of nodes for the debug bench (app/bench.py): every step from the caller's line
to the agent's spoken reply, in order, in the lane of whoever did it, with its timing and its full
detail. Also the rule proof: each server rule the call reached and how it came out.

Built from the call record (app/trace.py) and the JSON log beside it (app/logs.py), which keeps
each request's result. The voice worker doesn't log the model requests it makes, so each request's
system prompt and messages are rebuilt from the same pieces the worker joins: the /calls/start
result through prompts/assemble.py, then the turns, the `[server]` notes and the tool results as
the worker hands them to the model. A timing the record doesn't hold is None, shown as missing.

Everything returned is redacted (app/redact.py).
"""

import json
from datetime import datetime

from prompts.assemble import instructions, server_notes

from . import logs, redact
from .db import parse_iso
from .plain import REFUSED
from .tools import TOOLS

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
    return json.dumps(redact.value(obj), indent=2, default=str, ensure_ascii=False)


def _tool_output(row: dict | None) -> str | None:
    """What the worker hands the model as a tool's result: the envelope without empty fields
    (voice/livekit/tools.py compact)."""
    if row is None:
        return None
    envelope = {"ok": row["ok"], "say": row["say"], "data": row.get("result"), "refused": row["refused"]}
    return json.dumps(
        redact.value({k: v for k, v in envelope.items() if v not in (None, "", {})}),
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
            "summary": redact.text(summary),
            "at": _clock(self.started, at) if at else "",
            "ms": ms,
            "ms_label": ms_label,
            "tone": tone,
            "facts": [(k, redact.value(v, k)) for k, v in detail.pop("facts", [])],
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
                _agent(g, event, scripted, need_request)
                scripted, need_request = False, False
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
        "nodes": g.nodes,
        "proof": proof(trace, turn_rows),
        "logged": bool(rows),
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
        ("Known number", (data.get("caller") or {}).get("known_number")),
        ("After hours", data.get("after_hours")),
        ("Scheduling system up", data.get("fsm_available")),
        ("Rules version", version if version is not None else "defaults"),
        ("Tools offered", f"{len(TOOLS)}: " + ", ".join(TOOLS)),
    ]
    blocks = [_args_block(event)]
    if row is None:
        g.system_note = "The /calls/start result is not in the log, so the prompt can't be rebuilt."
    else:
        try:
            g.system = redact.text(instructions(redact.value(data), g.started))
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
          facts=[("Line", event["seq"])], blocks=[{"title": "Said", "text": redact.text(event["text"]), "open": True}])
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
        g.add("server", "server_note", "Note to the model", notes[0], event["at"],
              blocks=[{"title": "Added to the caller's line", "text": redact.text("\n".join(notes)),
                       "open": True}])
    content = event["text"] + ("\n" + "\n".join(notes) if notes else "")
    g.messages.append({"role": "user", "text": redact.text(content)})
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
        text, rule_tone = REFUSED.get(event["gate"], (f"Gate {event['gate']} fired", "hold"))
        if event["ok"]:
            text, rule_tone = "Allowed past a gate: " + event["gate"].replace("_", " "), "warn"
        g.add("server", "rule", "Rule", text, event["at"], tone=rule_tone,
              facts=[("Gate", event["gate"]), ("Outcome", "allowed" if event["ok"] else "held")])
    g.messages.append({"role": "assistant", "text": f"[tool call] {event['tool']} "
                       + json.dumps(redact.value(event["args"]), default=str, ensure_ascii=False)})
    g.messages.append({"role": "tool", "text": output or "(not in the log)"})


def _agent(g: Graph, event: dict, scripted: bool, need_request: bool) -> None:
    timings = event["timings"] or {}
    text = event["text"]
    if scripted:
        g.add("voice", "spoken", "Spoken", text, event["at"], timings.get("tts_first_byte_ms"),
              "first audio", tone="warn" if event["safety_action"] else "",
              facts=[("Source", "fixed text from the server or the tenant's lines, no model"),
                     ("Line", event["seq"])],
              blocks=[{"title": "Spoken", "text": redact.text(text), "open": True}])
        g.messages.append({"role": "assistant", "text": redact.text(text)})
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
    g.add("model", "model_wrote", "Model wrote", text, event["at"],
          facts=[("Line", event["seq"]), ("Characters", len(text))],
          blocks=[{"title": "Text", "text": redact.text(text), "open": True}])
    filtered = speech_filter(text)
    if filtered is not None and filtered != text:
        g.add("voice", "filter", "Speech filter", f"Dropped markdown or emoji: {_dropped(text, filtered)}",
              event["at"], tone="hold",
              blocks=[{"title": "After the filter", "text": redact.text(filtered), "open": True}],
              facts=[("How it's known", "LiveKit's default filter patterns, applied to the line")])
    reply = timings.get("reply_ms")
    g.add("voice", "spoken", "Spoken", text if filtered is None else filtered, event["at"],
          timings.get("tts_first_byte_ms"), "first audio",
          tone="warn" if event["safety_action"] else "",
          facts=[("Reply delay", f"{reply:.0f} ms" if reply is not None else "missing"),
                 ("Tool time on the worker", timings.get("tool_ms", "missing")),
                 ("Bridge line played", bool(timings.get("bridge")))])
    g.messages.append({"role": "assistant", "text": redact.text(text)})


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
     {"gate_read_back"}, {"book_appointment"}),
    ("Service area", "Only ZIPs the company serves are booked",
     {"out_of_area"}, {"check_service_area", "book_appointment"}),
    ("Homes only", "A commercial caller is never booked",
     {"customer_type"}, {"book_appointment", "find_slots"}),
    ("Caller's own records", "Lookups and changes stay on the customers matched to this call",
     {"not_owner", "scope", "ownership"}, {"get_customer_history", "reschedule_appointment",
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

