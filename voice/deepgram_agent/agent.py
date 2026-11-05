"""One call on Deepgram's Voice Agent API: Deepgram's Flux ears and turn-taking, its Aura-2 voice,
our Haiku profile as the think model, and our tool server behind every function.

This entry is deliberately loose (one of Jackson's single live calls per stack): the one-page
prompt (prompts/stripped.md) and only the server's own gates. The model's replies are not
filtered or held. What stays the server's:

- Every function runs on the tool server, with the call id and an Idempotency-Key for writes, and
  its envelope goes back as the function result. A refused tool stays refused.
- Each caller line is posted to the server's turn check. When the server answers with safety
  lines, they are injected as the agent's next words, word for word.
- `end_call` drops the line only when the server's result says so, after the goodbye has played.

The same class runs a phone call (server.py, Twilio media streams, 8 kHz mu-law both ways) and a
rig call (rig_adapter.py, 24 kHz linear PCM). Messages (SOURCE:
https://developers.deepgram.com/docs/voice-agent-message-flow.md and the pages it links, read
2026-11-05): Settings, binary audio, InjectAgentMessage, FunctionCallResponse; from Deepgram
Welcome, SettingsApplied, ConversationText, UserStartedSpeaking, FunctionCallRequest,
AgentAudioDone, Error and Warning.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

import websockets
import yaml

from app.tools import TOOLS
from prompts import assemble
from voice import names as local_names

log = logging.getLogger("deepgram_agent")

TENANTS = Path(__file__).resolve().parents[2] / "tenants"

URL = "wss://agent.deepgram.com/v1/agent/converse"
ANTHROPIC = "https://api.anthropic.com/v1/messages"
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
# Once the server agrees to hang up: how long to wait for the model's own goodbye before saying
# the tenant's, and the pause after the last word before the line drops (ASSUMED, the LiveKit
# stack's end_call_grace_s).
GOODBYE_WAIT_S = 3.0
HANG_UP_GRACE_S = 1.5
# The latest the line drops after an allowed end_call whose goodbye was already said (ASSUMED:
# longer than a goodbye takes to play).
FALLBACK_HANG_UP_S = 6.0


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    raw = json.dumps([call_id, tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def settings(*, instructions: str, specs: list[dict], greeting: str, cfg: dict, model: str,
             anthropic_key: str, keyterms: list[str], audio: dict) -> dict:
    """The Settings message. Haiku 5.5 isn't in Deepgram's managed Anthropic list, so it goes
    through Deepgram's custom-endpoint mode on our key; that mode refuses an `anthropic-version`
    header, and the model refuses `temperature` (MEASURED on the first connection)."""
    return {
        "type": "Settings",
        "mip_opt_out": True,
        "audio": audio,
        "agent": {
            "greeting": greeting,
            "listen": {"provider": {"type": "deepgram", "model": cfg["listen_model"],
                                    "version": "v2", "keyterms": keyterms}},
            "think": {
                "provider": {"type": "anthropic", "model": model},
                "endpoint": {"url": ANTHROPIC, "headers": {"x-api-key": anthropic_key}},
                "prompt": instructions,
                "functions": [{"name": s["name"], "description": s["description"],
                               "parameters": s["parameters"]} for s in specs],
            },
            "speak": {"provider": {"type": "deepgram", "model": cfg["voice"]}},
        },
    }


class Call:
    """Bridges one caller's audio to a Deepgram agent session.

    `server` has async `turn(speaker, text)`, `tool(name, args, key)` and `end(reason)`.
    `play(audio)` sends the agent's audio to the caller, `clear()` stops what is playing when the
    caller talks over it, and `hang_up()` drops the line."""

    def __init__(self, server, writes: set[str], play: Callable[[bytes], Awaitable[None]],
                 clear: Callable[[], Awaitable[None]], hang_up: Callable[[], Awaitable[None]],
                 key: str, connect=None, done: Callable[[], None] | None = None):
        self.server = server
        # Told when the agent has finished speaking and listens again (the audio rig's signal).
        self.done = done or (lambda: None)
        self.writes = writes
        self.play, self.clear, self.hang_up = play, clear, hang_up
        self.key = key
        self.connect = connect or websockets.connect
        self.ws = None
        self.reader = None
        self.call_id = ""
        self.goodbye = ""
        self.hanging_up = False
        # The goodbye after an allowed end_call has started; its AgentAudioDone drops the line.
        self.goodbye_started = False
        self.agent_lines: list[str] = []
        # Agent lines since the caller last spoke: a goodbye said before end_call counts.
        self.said_this_turn = 0
        self.closed = asyncio.Event()

    async def open(self, call_id: str, settings_message: dict, goodbye: str) -> None:
        self.call_id, self.goodbye = call_id, goodbye
        self.ws = await self.connect(URL, additional_headers={"Authorization": f"Token {self.key}"},
                                     max_size=None)
        welcome = json.loads(await self.ws.recv())
        if welcome.get("type") != "Welcome":
            raise RuntimeError(f"Deepgram agent: expected Welcome, got {welcome}")
        await self.ws.send(json.dumps(settings_message))
        self.reader = asyncio.create_task(self._read())

    async def send(self, audio: bytes) -> None:
        if self.ws is None or self.closed.is_set():
            return
        try:
            await self.ws.send(audio)
        except websockets.ConnectionClosed:
            self.closed.set()

    async def close(self, reason: str) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.reader is not None:
            await asyncio.gather(self.reader, return_exceptions=True)
        await self.server.end(reason)

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    await self.play(raw)
                else:
                    await self.handle(json.loads(raw))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed.set()

    async def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "UserStartedSpeaking":
            await self.clear()
        elif kind == "ConversationText":
            text = (event.get("content") or "").strip()
            if event.get("role") == "user":
                self.said_this_turn = 0
                await self.caller_line(text)
            elif text:
                self.agent_lines.append(text)
                self.said_this_turn += 1
                self.goodbye_started = self.goodbye_started or self.hanging_up
                await self.server.turn("agent", text)
        elif kind == "FunctionCallRequest":
            for function in event.get("functions") or []:
                if function.get("client_side", True):
                    await self.function(function)
        elif kind == "AgentAudioDone":
            if self.goodbye_started:
                await asyncio.sleep(HANG_UP_GRACE_S)
                await self.hang_up()
            else:
                self.done()
        elif kind in ("Error", "Warning"):
            log.warning("%s: Deepgram %s %s", self.call_id, kind, event.get("description"))

    async def caller_line(self, text: str) -> None:
        if not text:
            return
        result = await self.server.turn("caller", text)
        safety = (result.get("data") or {}).get("safety") or {}
        if safety.get("action") in SPOKEN_ACTIONS and result.get("say"):
            await self.inject(result["say"])

    async def inject(self, text: str) -> None:
        await self.ws.send(json.dumps({"type": "InjectAgentMessage", "message": text,
                                       "behavior": "interrupt"}))

    async def function(self, request: dict) -> None:
        name = request.get("name", "")
        try:
            args = json.loads(request.get("arguments") or "{}")
        except ValueError:
            args = {}
        if name not in TOOLS:
            envelope = {"ok": False, "refused": "unknown_tool"}
        else:
            key = idempotency_key(self.call_id, name, args) if name in self.writes else None
            envelope = await self.server.tool(name, args, key)
        compact = {k: v for k, v in envelope.items() if v not in (None, "", {})}
        if name == "end_call" and envelope.get("ok") and self.said_this_turn:
            # The model has said its goodbye already; the server's would be said a second time
            # (MEASURED on the first rig runs: "Thanks for calling" twice in one turn).
            compact.pop("say", None)
        await self.ws.send(json.dumps({"type": "FunctionCallResponse", "id": request.get("id"),
                                       "name": name, "content": json.dumps(compact, default=str)}))
        if name == "end_call" and envelope.get("ok") and (envelope.get("data") or {}).get("hang_up"):
            self.hanging_up = True
            if self.said_this_turn:
                # The model's goodbye came with the call; its audio ending drops the line, or
                # this fallback does if that audio had already ended.
                self.goodbye_started = True
                asyncio.get_running_loop().create_task(self._hang_up_later(FALLBACK_HANG_UP_S))
            else:
                asyncio.get_running_loop().create_task(self._goodbye_if_silent(len(self.agent_lines)))

    async def _hang_up_later(self, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        if not self.closed.is_set():
            await self.hang_up()

    async def _goodbye_if_silent(self, lines_before: int) -> None:
        """The model usually says goodbye after end_call's result. If it says nothing, the
        tenant's goodbye is injected, and its AgentAudioDone drops the line."""
        await asyncio.sleep(GOODBYE_WAIT_S)
        if len(self.agent_lines) == lines_before and not self.closed.is_set():
            await self.inject(self.goodbye)


def first_line(started: dict, voice: dict) -> str:
    """As the worker opens a call: the recording notice when there is one, then the greeting and
    any line the server adds; on a resumed safety call, the safety lines first."""
    data = started["data"]
    safety = (data.get("safety") or {}).get("text")
    if safety:
        return safety
    greeting = " ".join(x for x in (assemble.greeting(data, voice), started.get("say")) if x)
    return " ".join(x for x in (data.get("recording_notice"), greeting) if x)


def prepare(started: dict, cfg: dict, model: str, anthropic_key: str, audio: dict) -> tuple[dict, set[str], str]:
    """The Settings message, the names of the write tools and the goodbye for a started call."""
    data = started["data"]
    folder = TENANTS / data["tenant"]["slug"]
    specs = assemble.tool_specs(assemble.job_types(folder))
    voice = assemble.spoken(data, yaml.safe_load((folder / "agent.yaml").read_text())["voice"])
    message = settings(
        instructions=assemble.stripped_instructions(data, folder), specs=specs,
        greeting=first_line(started, voice), cfg=cfg, model=model, anthropic_key=anthropic_key,
        keyterms=local_names.load(folder).keyterms(), audio=audio,
    )
    return message, {s["name"] for s in specs if s["write"]}, voice["goodbye"]
