"""The agents under test. Each one is driven a caller line at a time and answers with what it said,
which tools it called and how long the caller waited, so every stack is scored the same way.

- `livekit`: our stack's brain in process. The same prompt and tool list as the LiveKit worker
  (prompts/assemble.py, built from prompts/ and app/tools.py) and the same tool server, without
  audio. Safety text from the server is spoken as given, never generated, as the worker does.
- `retell`: the Retell baseline through Retell's text chat API. Its adapter lives in
  voice/retell/eval_adapter.py and is loaded by name (`load_adapter`), so shared code never imports
  a voice layer (docs/architecture.md).
"""
import hashlib
import importlib
import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.tools import TOOLS
from evals.models import Model, Usage
from evals.results import TurnTiming
from evals.world import ToolServer

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
TENANTS_DIR = ROOT / "tenants"
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


def load_adapter(stack: str):
    """The voice layer's own eval adapter, `voice.<stack>.eval_adapter`, which offers
    `open_agent(call_id, caller_id)` and returns the agent and the tool server whose record the
    checks read. Loaded by name so this file never imports a voice folder."""
    try:
        return importlib.import_module(f"voice.{stack}.eval_adapter")
    except ModuleNotFoundError as err:
        if err.name and err.name.startswith(f"voice.{stack}"):
            raise RuntimeError(f"stack {stack!r} has no eval adapter (voice/{stack}/eval_adapter.py)"
                               ) from err
        raise


def prompt_files(prompts_dir: Path = PROMPTS_DIR) -> list[Path]:
    """The agent's prompt files, in name order (README and the eval prompts left out), or the
    stand-in while there are none. The run hash covers these.

    Where prompts/assemble.py is, only the parts it joins into the agent's prompt count. Other
    prompts kept beside them, such as the technician summary's (app/summary.py), are never part of
    a call, so changing one doesn't make a voice-agent eval run stale."""
    assemble = _assemble() if (prompts_dir / "assemble.py").exists() else None
    if assemble is not None:
        names = set(assemble.PARTS) | {assemble.HOUSE_GUIDANCE}
        files = sorted(prompts_dir / n for n in names if (prompts_dir / n).exists())
    else:
        files = sorted(p for p in prompts_dir.glob("*.md") if p.name != "README.md")
    if not files:
        return [STANDIN_PROMPT]
    return files + sorted(prompts_dir.glob("*.yaml")) + sorted(prompts_dir.glob("*.py"))


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


def _assemble():
    """The voice lane's prompt and tool builder (prompts/assemble.py), when it is in the tree."""
    try:
        return importlib.import_module("prompts.assemble")
    except ImportError:
        return None


def job_types(tenant_slug: str) -> list[str]:
    config = yaml.safe_load((TENANTS_DIR / tenant_slug / "config.yaml").read_text())
    return [j["code"] for j in config["job_types"] if j.get("active", True)]


def system_prompt(start_data: dict, prompts_dir: Path = PROMPTS_DIR, now=None) -> str:
    assemble = _assemble()
    if assemble:
        return assemble.instructions(start_data, now)
    parts = [p.read_text().strip() for p in prompt_files(prompts_dir)]
    context = json.dumps(start_data, indent=1, default=str)
    parts.append(f"## This call\n\nWhat the server found before the greeting:\n\n{context}")
    return "\n\n".join(parts)


def idempotency_key(call_id: str, name: str, args: dict) -> str:
    """Derived from call, tool and arguments, as the voice layer does (docs/tools.md)."""
    raw = json.dumps([call_id, name, args], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def assembled_tool_specs(tenant_slug: str) -> list[dict]:
    """The worker's tool list, in the Messages API shape."""
    assemble = _assemble()
    codes = assemble.job_types(TENANTS_DIR / tenant_slug) if hasattr(assemble, "job_types") else (
        job_types(tenant_slug))
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in assemble.tool_specs(codes)
    ]


def greeting(start_data: dict) -> str | None:
    """The worker's fixed opening line from the tenant's `voice` lines, when both exist."""
    assemble = _assemble()
    agent_yaml = TENANTS_DIR / start_data["tenant"]["slug"] / "agent.yaml"
    voice = yaml.safe_load(agent_yaml.read_text()).get("voice")
    if assemble is None or not hasattr(assemble, "greeting") or not voice:
        return None
    return assemble.greeting(start_data, voice)


def server_notes(hints: list[str]) -> list[str]:
    assemble = _assemble()
    if assemble is not None and hasattr(assemble, "server_notes"):
        return assemble.server_notes(hints)
    return [f"(Server note: {HINT_NOTES[h]})" for h in hints if h in HINT_NOTES]


def spoken_by_code(name: str, envelope: dict) -> str | None:
    """Tool results the worker speaks word for word with no model step: a refusal by the safety
    gate, the read-back sentence, and the safety sign-off an end_call returns. The worker's own
    rule (prompts/assemble.py) when it is in the tree."""
    assemble = _assemble()
    if assemble is not None and hasattr(assemble, "spoken_by_server"):
        return assemble.spoken_by_server(name, envelope)
    say = envelope.get("say") or ""
    if envelope.get("refused") == "gate_safety" and say:
        return say
    if name == "end_call" and envelope.get("ok") and say:
        return say
    return None


class InProcessAgent:
    def __init__(self, server: ToolServer, model: Model, call_id: str, caller_id: str,
                 clock=None):
        self.server = server
        self.model = model
        self.call_id = call_id
        self.caller_id = caller_id
        self.clock = clock
        self.messages: list[dict] = []
        self.system = ""
        self.tools: list[dict] = []
        # What of each model reply the caller hears, by the worker's own rule, so the transcript
        # and the checks see what a caller would (prompts/assemble.py SpokenReply).
        assemble = _assemble()
        self.reply = assemble.SpokenReply() if hasattr(assemble, "SpokenReply") else None

    def start(self) -> AgentTurn:
        started = self.server.start(self.call_id, self.caller_id)
        now = self.clock() if self.clock else None
        self.system = system_prompt(started["data"], now=now)
        slug = started["data"]["tenant"]["slug"]
        self.tools = assembled_tool_specs(slug) if _assemble() else tool_specs()
        safety = started["data"].get("safety")
        if safety and started["say"]:
            # A dropped safety call carries on: the lines come first on this call too.
            self.messages.append({"role": "user", "content": "(The call has connected.)"})
            return self._speak_from_code(started["say"], time.perf_counter())
        opening = greeting(started["data"])
        if opening:
            self.messages.append({"role": "user", "content": "(The call has connected.)"})
            return self._speak_from_code(opening, time.perf_counter())
        return self._think("(The call has connected. Greet the caller.)", time.perf_counter())

    def hear(self, caller_text: str) -> AgentTurn:
        t0 = time.perf_counter()
        result = self.server.turn(self.call_id, "caller", caller_text)
        safety = result["data"].get("safety") or {}
        if self.reply is not None:
            self.reply.heard(safety)
        if safety.get("action") in SPOKEN_FROM_CODE and result["say"]:
            self.messages.append({"role": "user", "content": caller_text})
            return self._speak_from_code(result["say"], t0)
        notes = server_notes(result["data"].get("hints", []))
        return self._think("\n".join([caller_text, *notes]), t0)

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
            said, content = reply.text, reply.content
            if self.reply is not None and said:
                calls = [(u["name"], u["input"]) for u in reply.tool_uses]
                said = self.reply.text(said, calls)
                if not said:
                    # The worker never speaks it, so the model's own history doesn't keep it.
                    content = [b for b in content if b.get("type") != "text"]
            self.messages.append({"role": "assistant", "content": content})
            if said:
                spoken.append(said)
                if first_word is None:
                    first_word = hop_start + reply.first_token_ms / 1000
            if not reply.tool_uses:
                break
            results, from_code = [], None
            for use in reply.tool_uses:
                called = time.perf_counter()
                envelope = self._call_tool(use["name"], use["input"])
                tool_ms += (time.perf_counter() - called) * 1000
                if self.reply is not None:
                    self.reply.tool_done(use["name"], envelope)
                if use["name"] == "end_call" and envelope.get("ok"):
                    ended = bool((envelope.get("data") or {}).get("hang_up"))
                from_code = from_code or spoken_by_code(use["name"], envelope)
                results.append(
                    {"type": "tool_result", "tool_use_id": use["id"],
                     "content": json.dumps(envelope)}
                )
            self.messages.append({"role": "user", "content": results})
            if from_code:
                self.messages.append({"role": "assistant", "content": from_code})
                spoken.append(from_code)
                first_word = first_word or time.perf_counter()
                break
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
