"""OpenAI Realtime on the simulated audio rig (evals/audio_rig, DECISIONS.md 026): one
speech-to-speech model hears, decides and speaks, with our tool server behind it. Loaded by name
so shared code never imports this folder.

It is wired the way the LiveKit worker is (voice/livekit/call.py and tools.py), so the gates are
the server's, not the model's:

- Turn detection runs on OpenAI's side but does not start a reply. When the caller's words come
  back transcribed, they are posted to the tool server's turn check first; only then is a reply
  asked for, with any `[server]` notes added. That wait is the price of a server gate on a
  speech-to-speech model and is part of the measured delay.
- Safety text from the server (the lines, a gate refusal, the safety sign-off) can't be spoken
  from code here: the model is the voice. It is asked to say the text exactly, and the tool
  server checks the words it reports, as on Retell. Whether it does is what this entrant tests.
- Function calls are forwarded to the tool server with the call id and an Idempotency-Key, and
  the envelope goes back as the function output. `end_call` hangs up only when the server's
  result says so.

Model, voice, transcription model and prices come from `realtime.openai` in config/models.yaml.
Events (SOURCE: https://developers.openai.com/api/docs/guides/realtime-conversations):
`session.update`, `input_audio_buffer.append`, `conversation.item.create`, `response.create`;
from the server `conversation.item.input_audio_transcription.completed`,
`response.output_audio.delta`, `response.output_audio_transcript.done` and `response.done`.
"""

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import websockets
import yaml

from app.tools import TOOLS
from prompts import assemble

WS_URL = "wss://api.openai.com/v1/realtime?model={model}"
RATE = 24000
TENANTS = Path(__file__).resolve().parents[2] / "tenants"
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")


def say_exactly(text: str) -> str:
    return f"Say exactly the following words and nothing else, then stop: {text}"


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    raw = json.dumps([call_id, tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def session_config(instructions: str, specs: list[dict], cfg: dict) -> dict:
    return {
        "type": "realtime",
        "instructions": instructions,
        "output_modalities": ["audio"],
        "tools": [{"type": "function", "name": s["name"], "description": s["description"],
                   "parameters": s["parameters"]} for s in specs],
        "tool_choice": "auto",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": RATE},
                "transcription": {"model": cfg["transcribe_model"]},
                "turn_detection": {"type": "server_vad", "silence_duration_ms": cfg["silence_ms"],
                                   "create_response": False, "interrupt_response": False},
            },
            "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": cfg["voice"]},
        },
    }


def usage_cost(usage: dict, price: dict) -> float:
    """One response's tokens at the profile's prices. Cached input is billed at the cached rate,
    whichever kind it was."""
    details = usage.get("input_token_details") or {}
    cached = details.get("cached_tokens", 0) or 0
    cached_detail = details.get("cached_tokens_details") or {}
    text_in = (details.get("text_tokens", 0) or 0) - (cached_detail.get("text_tokens", 0) or 0)
    audio_in = (details.get("audio_tokens", 0) or 0) - (cached_detail.get("audio_tokens", 0) or 0)
    out = usage.get("output_token_details") or {}
    return (
        max(text_in, 0) * price["text_input"] + max(audio_in, 0) * price["audio_input"]
        + cached * price["cached_input"] + (out.get("text_tokens", 0) or 0) * price["text_output"]
        + (out.get("audio_tokens", 0) or 0) * price["audio_output"]
    ) / 1e6


class Line:
    def __init__(self, entrant: "Entrant", connect=None):
        self.entrant = entrant
        self.cfg = entrant.cfg
        self.connect = connect or websockets.connect
        self.ws = None
        self.server = None
        self.call_id = ""
        self.listener = None
        self.reader = None
        self.cost_usd = 0.0
        self.caller_seconds = 0.0
        self.hang_up_after = False
        self.writes: set[str] = set()
        self.voice: dict = {}
        self._agent_text: list[str] = []
        # OpenAI takes one response at a time. A reply asked for while one is under way waits
        # for it, and cuts it short unless it is fixed text (as the worker can't be interrupted
        # while it speaks the safety lines).
        self._active = False
        self._fixed = False
        self._queued: dict | None = None

    async def _send(self, event: dict) -> None:
        await self.ws.send(json.dumps(event))

    async def _server(self, method: str, *args):
        return await asyncio.to_thread(getattr(self.server, method), *args)

    async def open(self, call_id, caller_id, world, listener):
        self.call_id, self.listener, self.server = call_id, listener, world.server
        started = await self._server("start", call_id, caller_id)
        data = started["data"]
        folder = TENANTS / data["tenant"]["slug"]
        specs = assemble.tool_specs(assemble.job_types(folder))
        self.writes = {s["name"] for s in specs if s["write"]}
        self.voice = yaml.safe_load((folder / "agent.yaml").read_text())["voice"]
        url = WS_URL.format(model=self.cfg["model"])
        self.ws = await self.connect(
            url, additional_headers={"Authorization": f"Bearer {self.entrant.key}"}, max_size=None
        )
        await self._send({"type": "session.update",
                          "session": session_config(assemble.instructions(data), specs, self.cfg)})
        safety = (data.get("safety") or {}).get("text")
        first = safety or " ".join(x for x in (assemble.greeting(data, self.voice), started["say"])
                                   if x)
        self.reader = asyncio.create_task(self._read())
        await self._respond(say_exactly(first), tools=False)
        return world.server

    async def _respond(self, instructions: str | None = None, tools: bool = True) -> None:
        response: dict = {}
        if instructions:
            response["instructions"] = instructions
        if not tools:
            response["tool_choice"] = "none"
        if not self._active:
            await self._create(response)
            return
        if self._queued is None or self._queued.get("tool_choice") != "none":
            self._queued = response
        if not self._fixed:
            await self._send({"type": "response.cancel"})

    async def _create(self, response: dict) -> None:
        self._active = True
        self._fixed = response.get("tool_choice") == "none"
        await self._send({"type": "response.create", "response": response})

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                await self.handle(json.loads(raw))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.listener.hung_up()

    async def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "response.output_audio.delta":
            self.listener.audio(base64.b64decode(event["delta"]), RATE)
        elif kind == "response.output_audio_transcript.done":
            if event.get("transcript"):
                self._agent_text.append(event["transcript"].strip())
        elif kind == "conversation.item.input_audio_transcription.completed":
            await self.caller_line(event.get("transcript", "").strip())
        elif kind == "response.done":
            await self.response_done(event["response"])
        elif kind == "error":
            print(f"{self.call_id}: realtime error {event.get('error')}", flush=True)

    async def caller_line(self, text: str) -> None:
        """The worker's caller_line: the server reads the words before any reply."""
        if not text:
            return
        result = await self._server("turn", self.call_id, "caller", text)
        data = result.get("data") or {}
        safety = data.get("safety") or {}
        if safety.get("action") in SPOKEN_ACTIONS and result["say"]:
            await self._respond(say_exactly(result["say"]), tools=False)
            return
        notes = assemble.server_notes(data.get("hints", []))
        if notes:
            await self._send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "\n".join(notes)}]}})
        await self._respond()

    async def response_done(self, response: dict) -> None:
        self._active = False
        self.cost_usd += usage_cost(response.get("usage") or {}, self.cfg["price"])
        # A reply cut short by a newer caller line runs none of its function calls.
        cancelled = response.get("status") == "cancelled"
        calls = [] if cancelled else [o for o in response.get("output", [])
                                      if o.get("type") == "function_call"]
        spoken = None
        for call in calls:
            args = json.loads(call.get("arguments") or "{}")
            envelope = await self.tool(call["name"], args)
            if call["name"] == "end_call" and envelope.get("ok"):
                self.hang_up_after = self.hang_up_after or bool(
                    (envelope.get("data") or {}).get("hang_up"))
            say = envelope.get("say") or ""
            if say and (envelope.get("refused") == "gate_safety"
                        or (call["name"] == "end_call" and envelope.get("ok"))):
                spoken = spoken or say
            compact = {k: v for k, v in envelope.items() if v not in (None, "", {})}
            await self._send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call["call_id"],
                "output": json.dumps(compact, default=str)}})
        text = " ".join(self._agent_text).strip()
        self._agent_text = []
        if text:
            self.listener.said(text)
            await self._server("turn", self.call_id, "agent", text)
        if spoken:
            await self._respond(say_exactly(spoken), tools=False)
            return
        if self._queued is not None:
            queued, self._queued = self._queued, None
            await self._create(queued)
            return
        if calls and not self.hang_up_after:
            await self._respond()
            return
        if self.hang_up_after:
            if not text:
                await self._respond(say_exactly(self.voice["goodbye"]), tools=False)
                return
            self.entrant.schedule_hang_up(self)
        self.listener.done()

    async def tool(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return {"ok": False, "say": "", "data": {}, "refused": "unknown_tool"}
        key = idempotency_key(self.call_id, name, args) if name in self.writes else None
        return await self._server("tool", name, self.call_id, args, key)

    async def send(self, pcm: bytes) -> None:
        self.caller_seconds += len(pcm) / 2 / RATE
        await self._send({"type": "input_audio_buffer.append",
                          "audio": base64.b64encode(pcm).decode()})

    async def close(self, reason: str) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.server is not None:
            await self._server("end", self.call_id, reason)

    async def cost(self) -> dict[str, float]:
        transcribe = self.caller_seconds / 60 * self.cfg["price"]["transcribe_per_minute"]
        return {"platform": self.cost_usd, "platform_transcription": transcribe}


class Entrant:
    name = "openai-realtime"
    profile = "realtime.openai"
    in_rate = RATE
    local_world = True
    final_record = False
    signals_done = True

    def __init__(self, ctx):
        self.cfg = ctx.config["realtime"]["openai"]
        self.model = self.cfg["model"]
        self.key = ctx.env.get("OPENAI_API_KEY", "")
        if not self.key:
            raise RuntimeError("OpenAI Realtime runs need OPENAI_API_KEY")

    def schedule_hang_up(self, line: Line) -> None:
        """The server agreed to hang up: drop the line once the goodbye has played."""

        async def drop() -> None:
            listener = line.listener
            while listener.clock() < listener.playout.end:
                await asyncio.sleep(0.05)
            await line.ws.close()

        asyncio.get_running_loop().create_task(drop())

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
