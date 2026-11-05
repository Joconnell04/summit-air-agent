"""One GPT-Live call, the same on the phone (sip.py) and on the audio rig (rig_adapter.py).

GPT-Live is two models: a full-duplex voice model that talks to the caller, and a Responses model
it delegates work to ("Responses delegation"). The backend's function calls come to us; we run them
on the tool server and send the result back. Nothing else is layered on top: the gates are the tool
server's own (its turn check and each tool's checks), and the prompts are one page each
(prompts/gpt_live/).

Events (SOURCE: https://developers.openai.com/api/docs/guides/live-delegation and
https://developers.openai.com/api/docs/guides/live-conversations, read 2026-11-05):
- `session.input_transcript.delta`, `session.output_transcript.delta`: the caller's and the agent's
  words as fragments, with no end-of-turn marker. A line is posted to /calls/{id}/turn when the
  other side starts speaking or after LINE_GAP_S with no new fragment.
- `response.event` wrapping `response.output_item.done` with a `function_call` item: run it, answer
  with `response.item.create` (a `function_call_output`), then `response.create` to resume the
  backend. Parallel calls are off, so each backend response has at most one call to answer.
- `session.instructions.append`, `session.thinking.append`: the turn check's safety lines and
  notes. The voice model is the voice, so it is asked to say the lines exactly; the tool server
  counts them as said only when an agent line contains them, as on Retell (DECISIONS.md 006).
- `session.usage.updated`, `session.closed`: voice seconds, billed per second.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import yaml

from app.tools import TOOLS
from prompts import assemble

log = logging.getLogger("gpt_live")

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts" / "gpt_live"
TENANTS = ROOT / "tenants"
SAFETY_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
SAY_NOW = "Say this to the caller now, exactly as written, with no word changed: {text}"
GREET = "Greet the caller now in English with exactly these words, then pause and listen: {text}"
# A speaker's line is complete after this much quiet in their transcript (ASSUMED; transcript
# fragments arrive unevenly, and a longer gap only delays the turn check).
LINE_GAP_S = 0.8
# After the server allows a hang-up: wait for the goodbye to start, then for the agent's transcript
# to go quiet, then a tail for the audio behind it (ASSUMED, checked on the first calls).
GOODBYE_WAIT_S = 5.0
GOODBYE_QUIET_S = 1.2
GOODBYE_TAIL_S = 1.0
GOODBYE_MAX_S = 15.0
POLL_S = 0.1


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    raw = json.dumps([call_id, tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def compact(envelope: dict) -> dict:
    return {k: v for k, v in envelope.items() if v not in (None, "", {})}


def prompts(start: dict) -> tuple[str, str]:
    """The voice model's prompt and the backend's: each one page, with the company knowledge and
    what the server found before hello in the backend's."""
    company = start["tenant"]["name"]
    voice = (PROMPTS / "voice.md").read_text().strip().replace("{company}", company)
    parts = dict(assemble.sections(start))
    backend = "\n\n".join([
        (PROMPTS / "backend.md").read_text().strip().replace("{company}", company),
        parts["Company knowledge"],
        parts["This call"],
    ])
    return voice, backend


def tenant_files(start: dict) -> tuple[list[dict], dict]:
    """The tool specs for this tenant's job types and its spoken lines."""
    folder = TENANTS / start["tenant"]["slug"]
    specs = assemble.tool_specs(assemble.job_types(folder))
    voice = yaml.safe_load((folder / "agent.yaml").read_text())["voice"]
    return specs, voice


def opening(start: dict, say: str, voice: dict) -> str:
    """What the agent says first: the safety lines if the server already has them, otherwise the
    tenant's greeting and anything /calls/start asked to be said."""
    safety = (start.get("safety") or {}).get("text")
    if safety:
        return safety
    return " ".join(x for x in (assemble.greeting(start, voice), say) if x)


def session_config(start: dict, specs: list[dict], cfg: dict, greeting: str,
                   audio_format: dict | None = None) -> dict:
    voice_prompt, backend_prompt = prompts(start)
    audio: dict = {"output": {"voice": cfg["voice"]}}
    if audio_format:
        audio["format"] = audio_format
    return {
        "model": cfg["model"],
        "instructions": voice_prompt,
        "audio": audio,
        "input": [{"type": "message", "role": "developer",
                   "content": [{"type": "input_text", "text": GREET.format(text=greeting)}]}],
        "delegation": {
            "type": "responses",
            "responses": {
                "model": cfg["backend_model"],
                "instructions": backend_prompt,
                "tools": [{"type": "function", "name": s["name"], "description": s["description"],
                           "parameters": s["parameters"]} for s in specs],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "reasoning": {"effort": cfg["reasoning_effort"]},
            },
        },
    }


def greet_event(greeting: str) -> dict:
    """Sent once after the session starts, as the docs advise for a greeting before the caller
    speaks."""
    return {"type": "session.commentary.append", "event_id": "greeting",
            "delegation_id": None, "content": GREET.format(text=greeting)}


def backend_cost(usage: dict, price: dict) -> float:
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    fresh = max((usage.get("input_tokens", 0) or 0) - cached, 0)
    out = usage.get("output_tokens", 0) or 0
    return (fresh * price["backend_input"] + cached * price["backend_cached_input"]
            + out * price["backend_output"]) / 1e6


class Conversation:
    """The events of one session, from whichever connection carries them.

    `server` is a tool server client with `turn(call_id, speaker, text)` and
    `tool(name, call_id, args, key)` (evals/world.py ToolServer, or sip.ToolServer); its calls run
    in a thread. `send` sends one client event. `hang_up` ends the call; it is awaited once the
    server has allowed it and the goodbye has been said.
    """

    def __init__(self, call_id: str, server, writes: set[str],
                 send: Callable[[dict], Awaitable[None]],
                 hang_up: Callable[[], Awaitable[None]],
                 on_agent_line: Callable[[str], None] | None = None,
                 price: dict | None = None, clock=None):
        self.call_id = call_id
        self.server = server
        self.writes = writes
        self.send = send
        self.hang_up = hang_up
        self.on_agent_line = on_agent_line or (lambda _text: None)
        self.price = price
        self.clock = clock or (lambda: asyncio.get_running_loop().time())
        self.text = {"caller": "", "agent": ""}
        self.seq = {"caller": 0, "agent": 0}
        self.posting = asyncio.Lock()
        self.answered: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.last_agent_delta = 0.0
        self.hang_up_task: asyncio.Task | None = None
        self.voice_seconds = 0.0
        self.backend_usd = 0.0
        self.closed: dict | None = None

    async def _server(self, method: str, *args):
        return await asyncio.to_thread(getattr(self.server, method), *args)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "session.input_transcript.delta":
            await self._fragment("caller", event.get("delta", ""))
        elif kind == "session.output_transcript.delta":
            self.last_agent_delta = self.clock()
            await self._fragment("agent", event.get("delta", ""))
        elif kind == "response.event":
            await self._backend(event.get("event") or {})
        elif kind == "session.usage.updated":
            self.voice_seconds = float((event.get("usage") or {}).get("seconds", 0))
        elif kind == "session.closed":
            self.voice_seconds = float((event.get("usage") or {}).get("seconds",
                                                                       self.voice_seconds))
            self.closed = event
        elif kind == "error":
            log.warning("%s: live error %s", self.call_id, event.get("error"))

    # ---- transcript ---------------------------------------------------------------------------

    async def _fragment(self, speaker: str, delta: str) -> None:
        other = "agent" if speaker == "caller" else "caller"
        if self.text[other].strip():
            await self.flush(other)
        self.text[speaker] += delta
        self.seq[speaker] += 1
        self._spawn(self._flush_later(speaker, self.seq[speaker]))

    async def _flush_later(self, speaker: str, seq: int) -> None:
        # Not cancelled on a newer fragment: a cancel could land mid-post and lose the turn
        # check's answer. A newer fragment makes this one a no-op instead.
        await asyncio.sleep(LINE_GAP_S)
        if self.seq[speaker] == seq:
            await self.flush(speaker)

    async def flush(self, speaker: str) -> None:
        async with self.posting:
            text, self.text[speaker] = self.text[speaker].strip(), ""
            if not text:
                return
            result = await self._server("turn", self.call_id, speaker, text) or {}
        if speaker == "agent":
            self.on_agent_line(text)
            return
        if self.hang_up_task and not self.hang_up_task.done():
            # The caller spoke after an allowed end_call: keep the call open, as the Retell
            # bridge does.
            self.hang_up_task.cancel()
        data = result.get("data") or {}
        safety = data.get("safety") or {}
        if safety.get("action") in SAFETY_ACTIONS and result.get("say"):
            await self.send({"type": "session.instructions.append", "delegation_id": None,
                             "content": SAY_NOW.format(text=result["say"])})
            return
        notes = assemble.server_notes(data.get("hints") or [])
        if notes:
            await self.send({"type": "session.thinking.append", "delegation_id": None,
                             "content": "\n".join(notes)})

    async def flush_all(self) -> None:
        for speaker in ("caller", "agent"):
            await self.flush(speaker)

    # ---- backend ------------------------------------------------------------------------------

    async def _backend(self, inner: dict) -> None:
        kind = inner.get("type")
        if kind == "response.output_item.done":
            item = inner.get("item") or {}
            if item.get("type") == "function_call" and item.get("call_id") not in self.answered:
                self.answered.add(item["call_id"])
                self._spawn(self.function_call(item))
        elif kind in ("response.completed", "response.done", "response.incomplete"):
            usage = (inner.get("response") or {}).get("usage") or {}
            if usage and self.price:
                self.backend_usd += backend_cost(usage, self.price)

    async def function_call(self, item: dict) -> None:
        # Lines not yet posted go first, so the server's checks have the caller's latest words.
        await self.flush_all()
        name = item.get("name", "")
        try:
            args = json.loads(item.get("arguments") or "{}")
        except ValueError:
            args = {}
        if name not in TOOLS:
            envelope = {"ok": False, "refused": "unknown_tool"}
        else:
            key = idempotency_key(self.call_id, name, args) if name in self.writes else None
            envelope = await self._server("tool", name, self.call_id, args, key)
        await self.send({"type": "response.item.create", "item": {
            "type": "function_call_output", "call_id": item["call_id"],
            "output": json.dumps(compact(envelope), default=str)}})
        await self.send({"type": "response.create"})
        if name == "end_call" and envelope.get("ok") and (envelope.get("data") or {}).get(
                "hang_up"):
            self.hang_up_task = self._spawn(self._hang_up_after_goodbye())

    async def _hang_up_after_goodbye(self) -> None:
        start = self.clock()
        while self.clock() - start < GOODBYE_MAX_S:
            if self.last_agent_delta > start:
                if self.clock() - self.last_agent_delta >= GOODBYE_QUIET_S:
                    break
            elif self.clock() - start >= GOODBYE_WAIT_S:
                break
            await asyncio.sleep(POLL_S)
        await asyncio.sleep(GOODBYE_TAIL_S)
        await self.flush_all()
        await self.hang_up()

    async def drain(self) -> None:
        """Post what is left and let running tool calls finish."""
        running = [t for t in self.tasks if t is not self.hang_up_task]
        await asyncio.gather(*running, return_exceptions=True)
        await self.flush_all()

    def cost(self, per_minute: float) -> dict[str, float]:
        return {"platform": self.voice_seconds / 60 * per_minute, "brain_model":
                self.backend_usd}
