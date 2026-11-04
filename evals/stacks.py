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
    # Why the call ended when `ended`: the agent hung up, or a cold handoff sent the caller on.
    end_reason: str = "agent_end"


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


def server_notes(hints: list[str], known: dict | None = None) -> list[str]:
    assemble = _assemble()
    if assemble is not None and hasattr(assemble, "server_notes"):
        return assemble.server_notes(hints, known)
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


def read_back_parts(name: str, envelope: dict) -> list[dict] | None:
    """A read-back the caller asked to hear slower: the worker speaks the server's sentence in these
    parts, each at its own speed (app/pace.py), with no model step."""
    if name == "read_back" and envelope.get("ok"):
        return (envelope.get("data") or {}).get("parts")
    return None


class InProcessAgent:
    def __init__(self, server: ToolServer, model: Model, call_id: str, caller_id: str,
                 clock=None, handoff: str = "sent"):
        self.server = server
        # What the simulated phone company does with a cold handoff's SIP transfer.
        self.handoff = handoff
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
        # The speaking rate the server set (app/pace.py); every agent line is posted with the
        # speed it would be spoken at, as the worker does.
        self.speed: float | None = None
        # The caller asked the agent to wait on their last line (app/dead_air.py).
        self.holding = False

    def _pace(self, data: dict) -> None:
        pace = (data or {}).get("pace")
        if pace and pace.get("speed"):
            self.speed = float(pace["speed"])

    def _post_agent(self, text: str, timings: dict, speed: float | None = None) -> None:
        speed = self.speed if speed is None else speed
        if speed is not None:
            timings = {**timings, "speed": speed}
        self.server.turn(self.call_id, "agent", text, timings)

    def start(self) -> AgentTurn:
        started = self.server.start(self.call_id, self.caller_id)
        self._pace(started["data"])
        now = self.clock() if self.clock else None
        self.system = system_prompt(started["data"], now=now)
        slug = started["data"]["tenant"]["slug"]
        self.slug = slug
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
        self._pace(result["data"])
        safety = result["data"].get("safety") or {}
        if self.reply is not None:
            self.reply.heard(safety, caller_text)
        if safety.get("action") in SPOKEN_FROM_CODE and result["say"]:
            self.messages.append({"role": "user", "content": caller_text})
            return self._speak_from_code(result["say"], t0)
        handoff = result["data"].get("handoff")
        if handoff and result["say"]:
            self.messages.append({"role": "user", "content": caller_text})
            return self._hand_off(result["say"], handoff, t0)
        # The card stop, the billing callback or the electrical lines, said as written.
        spoken = result["data"].get("spoken") or {}
        if spoken.get("text"):
            self.messages.append({"role": "user", "content": caller_text})
            return self._speak_from_code(spoken["text"], t0)
        hold = (result["data"].get("hold") or {}).get("state")
        self.holding = hold == "holding"
        if hold == "aside":
            # Said on hold to someone else: the worker lets the model say nothing.
            self.messages.append({"role": "user", "content": caller_text})
            return AgentTurn("", TurnTiming(kind="plain", reply_delay_ms=0, model_first_token_ms=0))
        notes = server_notes(result["data"].get("hints", []), result["data"].get("known"))
        return self._think("\n".join([caller_text, *notes]), t0)

    def step_away(self) -> AgentTurn:
        """go_quiet with the live timings (config/models.yaml voice.dead_air) and the tenant's
        lines."""
        timings = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["voice"]["dead_air"]
        lines = yaml.safe_load((TENANTS_DIR / self.slug / "agent.yaml").read_text())["voice"]
        return self.go_quiet(timings["check_in_s"], timings["hang_up_s"], lines)

    def go_quiet(self, check_in_s: float, hang_up_s: float, lines: dict) -> AgentTurn:
        """The caller asked the agent to wait and never came back. The worker's own timer
        (voice/dead_air.py) runs on a clock that jumps ahead instead of waiting minutes, so each
        check-in and the goodbye are posted as the worker would say them, and the call ends as
        `stepped_away`."""
        from voice.dead_air import CHECK_IN, GOODBYE, DeadAir

        now = [0.0]
        timer = DeadAir(check_in_s, hang_up_s, clock=lambda: now[0])
        said = []
        while True:
            now[0] += 1.0
            action = timer.due()
            if action is None:
                continue
            timer.said(action)
            line = " ".join(lines["still_here" if action == CHECK_IN else "stepped_away"].split())
            self._speak_from_code(line, time.perf_counter())
            said.append(line)
            if action == GOODBYE:
                break
        turn = AgentTurn(" ".join(said), TurnTiming(kind="plain", reply_delay_ms=0,
                                                    model_first_token_ms=0))
        turn.ended, turn.end_reason = True, "stepped_away"
        return turn

    def _hand_off(self, line: str, handoff: dict, t0: float) -> AgentTurn:
        """What the worker does with a caller the server handed off (voice/livekit/transfer.py):
        say the server's line, then report the transfer as the scenario's phone company would.
        Sent ends the call; failed says the stored callback line and the call carries on."""
        turn = self._speak_from_code(line, t0)
        plan = handoff.get("transfer")
        if not plan:
            return turn
        done = self.server.handoff_done(self.call_id, plan["id"],
                                        "handed_off" if self.handoff == "sent" else "failed")
        if self.handoff == "sent":
            turn.ended, turn.end_reason = True, "transfer"
            return turn
        back = self._speak_from_code(done["say"] or handoff["fallback"], time.perf_counter())
        return AgentTurn(f"{turn.text} {back.text}", turn.timing)

    def _speak_from_code(self, text: str, t0: float) -> AgentTurn:
        self.messages.append({"role": "assistant", "content": text})
        delay = (time.perf_counter() - t0) * 1000
        self._post_agent(text, {"spoken_from_code_ms": delay})
        return AgentTurn(text, TurnTiming(kind="plain", reply_delay_ms=delay,
                                          model_first_token_ms=0))

    def _think(self, content: str, t0: float) -> AgentTurn:
        self.messages.append({"role": "user", "content": content})
        spoken, usage, ended = [], Usage(), False
        parts: list[dict] = []
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
                # The model's own history keeps what the worker would have spoken, as LiveKit's
                # chat context does.
                content = [b for b in content if b.get("type") != "text"]
                if said:
                    content.insert(0, {"type": "text", "text": said})
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
                # A read-back split by speed is spoken in its parts, never also whole.
                split = read_back_parts(use["name"], envelope) or []
                parts = parts or split
                if not split:
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
            if parts:
                sentence = " ".join(p["text"] for p in parts)
                self.messages.append({"role": "assistant", "content": sentence})
                first_word = first_word or time.perf_counter()
                break
            if ended:
                break
        said = " ".join(spoken)
        text = " ".join([said, *(p["text"] for p in parts)]).strip()
        delay = ((first_word or time.perf_counter()) - t0) * 1000
        timing = TurnTiming(
            kind="lookup" if len(hops) > 1 else "plain",
            reply_delay_ms=delay,
            model_first_token_ms=hops[0],
            tool_ms=tool_ms,
            second_hop_ms=hops[1] if len(hops) > 1 else None,
        )
        stages = {"model_first_token_ms": hops[0], "tool_ms": tool_ms, "reply_ms": delay}
        if said:
            self._post_agent(said, stages)
        # Each part is its own line, as the worker speaks them.
        for n, part in enumerate(parts):
            self._post_agent(part["text"], {} if said or n else stages, float(part["speed"]))
        return AgentTurn(text, timing, usage, ended)

    def _call_tool(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return {"ok": False, "say": "", "data": {}, "refused": "unknown_tool"}
        key = idempotency_key(self.call_id, name, args) if TOOLS[name][2] else None
        return self.server.tool(name, self.call_id, args, key)

    def end(self, reason: str) -> None:
        self.server.end(self.call_id, reason)
