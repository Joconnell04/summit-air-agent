"""The agents under test. Each one is driven a caller line at a time and answers with what it said,
which tools it called and how long the caller waited, so every stack is scored the same way.

- `livekit`: our stack's brain in process. The same prompts (prompts/*.md), the same tools
  (app/tools.py) and the same tool server as the LiveKit worker, without audio. Safety text from
  the server is spoken as given, never generated, as the worker does.
- `retell`: the Retell baseline through Retell's text chat API (evals/retell_chat.py).
"""
import hashlib
import importlib
import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.tools import TOOLS
from evals.models import Model, Usage
from evals.results import TurnTiming
from evals.world import ToolServer

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
# Used only while prompts/ holds no agent prompt yet, so the harness can be run end to end.
STANDIN_PROMPT = Path(__file__).resolve().parent / "standin_prompt.md"
MAX_HOPS = 6
SPOKEN_FROM_CODE = ("speak_lines", "speak_variant", "ask_check")
HINT_NOTES = {
    "commercial": "The server marked this caller as commercial; Summit Air doesn't book them.",
    "do_not_call": "The caller asked not to be called. Call mark_do_not_call for the line to say.",
    "safety_check_cleared": "The caller said no to the safety check. Go back to where the call was.",
}


@dataclass
class AgentTurn:
    text: str
    timing: TurnTiming
    usage: Usage = field(default_factory=Usage)
    ended: bool = False


def prompt_files(prompts_dir: Path = PROMPTS_DIR) -> list[Path]:
    """The agent's prompt files, in name order; README and the eval prompts are not part of it."""
    files = sorted(p for p in prompts_dir.glob("*.md") if p.name != "README.md")
    return files or [STANDIN_PROMPT]


def tool_specs() -> list[dict]:
    """Every model tool in app/tools.py, described by its handler and request model."""
    specs = []
    for name, (handler, model, _write) in TOOLS.items():
        schema = model.model_json_schema()
        schema["properties"].pop("call_id", None)
        schema["required"] = [r for r in schema.get("required", []) if r != "call_id"]
        schema.pop("title", None)
        specs.append(
            {
                "name": name,
                "description": inspect.cleandoc(handler.__doc__ or name),
                "input_schema": schema,
            }
        )
    return specs


def _brain():
    """The voice lane's own prompt and tool builders, if it ships them (voice/brain.py)."""
    try:
        return importlib.import_module("voice.brain")
    except ImportError:
        return None


def system_prompt(start_data: dict, prompts_dir: Path = PROMPTS_DIR) -> str:
    brain = _brain()
    if brain and hasattr(brain, "system_prompt"):
        return brain.system_prompt(start_data)
    parts = [p.read_text().strip() for p in prompt_files(prompts_dir)]
    context = json.dumps(start_data, indent=1, default=str)
    parts.append(f"## This call\n\nWhat the server found before the greeting:\n\n{context}")
    return "\n\n".join(parts)


def idempotency_key(call_id: str, name: str, args: dict) -> str:
    """Derived from call, tool and arguments, as the voice layer does (docs/tools.md)."""
    raw = json.dumps([call_id, name, args], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class InProcessAgent:
    def __init__(self, server: ToolServer, model: Model, call_id: str, caller_id: str):
        self.server = server
        self.model = model
        self.call_id = call_id
        self.caller_id = caller_id
        self.messages: list[dict] = []
        self.system = ""
        brain = _brain()
        self.tools = brain.tool_specs() if brain and hasattr(brain, "tool_specs") else tool_specs()

    def start(self) -> AgentTurn:
        started = self.server.start(self.call_id, self.caller_id)
        self.system = system_prompt(started["data"])
        safety = started["data"].get("safety")
        if safety and started["say"]:
            # A dropped safety call carries on: the lines come first on this call too.
            self.messages.append({"role": "user", "content": "(The call has connected.)"})
            return self._speak_from_code(started["say"], time.perf_counter())
        return self._think("(The call has connected. Greet the caller.)", time.perf_counter())

    def hear(self, caller_text: str) -> AgentTurn:
        t0 = time.perf_counter()
        result = self.server.turn(self.call_id, "caller", caller_text)
        safety = result["data"].get("safety") or {}
        if safety.get("action") in SPOKEN_FROM_CODE and result["say"]:
            self.messages.append({"role": "user", "content": caller_text})
            return self._speak_from_code(result["say"], t0)
        notes = [HINT_NOTES[h] for h in result["data"].get("hints", []) if h in HINT_NOTES]
        content = caller_text + "".join(f"\n(Server note: {n})" for n in notes)
        return self._think(content, t0)

    def _speak_from_code(self, text: str, t0: float) -> AgentTurn:
        self.messages.append({"role": "assistant", "content": text})
        delay = (time.perf_counter() - t0) * 1000
        self.server.turn(self.call_id, "agent", text, {"spoken_from_code_ms": delay})
        return AgentTurn(text, TurnTiming(kind="plain", reply_delay_ms=delay,
                                          model_first_token_ms=0))

    def _think(self, content: str, t0: float) -> AgentTurn:
        self.messages.append({"role": "user", "content": content})
        spoken, usage, ended = [], Usage(), False
        first_word = None
        hops: list[float] = []
        tool_ms = 0.0
        for _ in range(MAX_HOPS):
            hop_start = time.perf_counter()
            reply = self.model.complete(self.system, self.messages, self.tools, max_tokens=1024)
            usage.add(reply.usage)
            hops.append(reply.first_token_ms)
            self.messages.append({"role": "assistant", "content": reply.content})
            if reply.text:
                spoken.append(reply.text)
                if first_word is None:
                    first_word = hop_start + reply.first_token_ms / 1000
            if not reply.tool_uses:
                break
            results = []
            for use in reply.tool_uses:
                called = time.perf_counter()
                envelope = self._call_tool(use["name"], use["input"])
                tool_ms += (time.perf_counter() - called) * 1000
                if use["name"] == "end_call" and envelope.get("ok"):
                    ended = bool((envelope.get("data") or {}).get("hang_up"))
                results.append(
                    {"type": "tool_result", "tool_use_id": use["id"],
                     "content": json.dumps(envelope)}
                )
            self.messages.append({"role": "user", "content": results})
            if ended:
                break
        text = " ".join(spoken)
        delay = ((first_word or time.perf_counter()) - t0) * 1000
        timing = TurnTiming(
            kind="lookup" if len(hops) > 1 else "plain",
            reply_delay_ms=delay,
            model_first_token_ms=hops[0],
            tool_ms=tool_ms,
            second_hop_ms=hops[1] if len(hops) > 1 else None,
        )
        if text:
            stages = {"model_first_token_ms": hops[0], "tool_ms": tool_ms, "reply_ms": delay}
            self.server.turn(self.call_id, "agent", text, stages)
        return AgentTurn(text, timing, usage, ended)

    def _call_tool(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return {"ok": False, "say": "", "data": {}, "refused": "unknown_tool"}
        key = idempotency_key(self.call_id, name, args) if TOOLS[name][2] else None
        return self.server.tool(name, self.call_id, args, key)

    def end(self, reason: str) -> None:
        self.server.end(self.call_id, reason)
