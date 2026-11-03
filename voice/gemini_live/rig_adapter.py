"""Gemini Live on the simulated audio rig (evals/audio_rig, DECISIONS.md 026): Google's
speech-to-speech model with our tool server behind it. Loaded by name so shared code never imports
this folder.

Wired like the LiveKit worker where the platform allows it:

- Function calls are forwarded to the tool server with the call id and an Idempotency-Key, and
  `end_call` hangs up only when the server's result says so.
- Gemini Live answers as soon as it hears the caller stop and can't be told to wait, so the
  server's turn check can't run before the reply as it does on our stack and on the OpenAI entrant.
  The caller's transcribed words are posted to the tool server when the reply starts; if the
  server asks for the safety lines, the model is interrupted and asked to say them exactly. The
  caller hears the start of the model's own reply first, which is part of what this entrant
  measures.
- Safety text is said by the model, which the tool server checks word for word.

Model, voice and prices come from `realtime.gemini` in config/models.yaml. Messages (SOURCE:
https://ai.google.dev/api/live): `setup` first, then `realtimeInput` audio, `clientContent` text
turns and `toolResponse`; the server sends `setupComplete`, `serverContent` (audio parts, input and
output transcription, `turnComplete`) and `toolCall`.
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

WS_URL = ("wss://generativelanguage.googleapis.com/ws/"
          "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={key}")
IN_RATE = 16000
OUT_RATE = 24000
TENANTS = Path(__file__).resolve().parents[2] / "tenants"
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")


def say_exactly(text: str) -> str:
    return f"Say exactly the following words and nothing else, then stop: {text}"


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    raw = json.dumps([call_id, tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def setup(instructions: str, specs: list[dict], cfg: dict) -> dict:
    return {"setup": {
        "model": f"models/{cfg['model']}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": cfg["voice"]}}},
        },
        "systemInstruction": {"parts": [{"text": instructions}]},
        "tools": [{"functionDeclarations": [
            {"name": s["name"], "description": s["description"],
             "parametersJsonSchema": s["parameters"]} for s in specs]}],
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
    }}


def text_turn(text: str, complete: bool = True) -> dict:
    return {"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}],
                              "turnComplete": complete}}


def usage_cost(usage: dict, price: dict) -> float:
    def by_modality(details: list | None) -> dict[str, int]:
        return {d.get("modality", "TEXT"): d.get("tokenCount", 0) for d in details or []}

    prompt = by_modality(usage.get("promptTokensDetails"))
    reply = by_modality(usage.get("responseTokensDetails"))
    return (prompt.get("TEXT", 0) * price["text_input"]
            + prompt.get("AUDIO", 0) * price["audio_input"]
            + reply.get("TEXT", 0) * price["text_output"]
            + reply.get("AUDIO", 0) * price["audio_output"]) / 1e6


class Line:
    def __init__(self, entrant: "Entrant", connect=None):
        self.entrant = entrant
        self.cfg = entrant.cfg
        self.connect = connect or websockets.connect
        self.ws = None
        self.server = None
        self.listener = None
        self.reader = None
        self.call_id = ""
        self.writes: set[str] = set()
        self.voice: dict = {}
        self.cost_usd = 0.0
        self.hang_up_after = False
        self._heard: list[str] = []
        self._said: list[str] = []
        self._posted = True
        self._ready = asyncio.Event()

    async def _send(self, message: dict) -> None:
        await self.ws.send(json.dumps(message))

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
        self.ws = await self.connect(WS_URL.format(key=self.entrant.key), max_size=None)
        await self._send(setup(assemble.instructions(data), specs, self.cfg))
        self.reader = asyncio.create_task(self._read())
        await asyncio.wait_for(self._ready.wait(), timeout=20)
        safety = (data.get("safety") or {}).get("text")
        first = safety or " ".join(x for x in (assemble.greeting(data, self.voice), started["say"])
                                   if x)
        await self._send(text_turn(say_exactly(first)))
        return world.server

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                await self.handle(json.loads(raw))
        except websockets.ConnectionClosed as closed:
            if closed.rcvd and closed.rcvd.code != 1000:
                print(f"{self.call_id}: Gemini closed: {closed.rcvd.reason}", flush=True)
        finally:
            self._ready.set()
            self.listener.hung_up()

    async def handle(self, message: dict) -> None:
        if "setupComplete" in message:
            self._ready.set()
        if message.get("usageMetadata"):
            # Reported once per generation, with the whole prompt so far (MEASURED: one report per
            # turn, with turnComplete, and the prompt count growing turn on turn).
            self.cost_usd += usage_cost(message["usageMetadata"], self.cfg["price"])
        if "toolCall" in message:
            await self.caller_line()
            await self.tool_calls(message["toolCall"].get("functionCalls", []))
        content = message.get("serverContent") or {}
        if content.get("inputTranscription", {}).get("text"):
            self._heard.append(content["inputTranscription"]["text"])
            self._posted = False
        for part in (content.get("modelTurn") or {}).get("parts", []):
            blob = part.get("inlineData")
            if blob and blob.get("mimeType", "").startswith("audio/pcm"):
                await self.caller_line()
                self.listener.audio(base64.b64decode(blob["data"]), OUT_RATE)
        if content.get("outputTranscription", {}).get("text"):
            self._said.append(content["outputTranscription"]["text"])
        if content.get("turnComplete"):
            await self.turn_complete()

    async def caller_line(self) -> None:
        """Post the caller's words once the model starts answering them."""
        if self._posted:
            return
        self._posted = True
        text = "".join(self._heard).strip()
        self._heard = []
        if not text:
            return
        result = await self._server("turn", self.call_id, "caller", text)
        data = result.get("data") or {}
        safety = data.get("safety") or {}
        if safety.get("action") in SPOKEN_ACTIONS and result["say"]:
            await self._send(text_turn(say_exactly(result["say"])))
            return
        notes = assemble.server_notes(data.get("hints", []))
        if notes:
            await self._send(text_turn("\n".join(notes), complete=False))

    async def tool_calls(self, calls: list[dict]) -> None:
        responses, spoken = [], None
        for call in calls:
            envelope = await self.tool(call["name"], call.get("args") or {})
            if call["name"] == "end_call" and envelope.get("ok"):
                self.hang_up_after = self.hang_up_after or bool(
                    (envelope.get("data") or {}).get("hang_up"))
            say = envelope.get("say") or ""
            if say and (envelope.get("refused") == "gate_safety"
                        or (call["name"] == "end_call" and envelope.get("ok"))):
                spoken = spoken or say
            compact = {k: v for k, v in envelope.items() if v not in (None, "", {})}
            responses.append({"id": call.get("id"), "name": call["name"], "response": compact})
        await self._send({"toolResponse": {"functionResponses": responses}})
        if spoken:
            await self._send(text_turn(say_exactly(spoken)))

    async def tool(self, name: str, args: dict) -> dict:
        if name not in TOOLS:
            return {"ok": False, "say": "", "data": {}, "refused": "unknown_tool"}
        key = idempotency_key(self.call_id, name, args) if name in self.writes else None
        return await self._server("tool", name, self.call_id, args, key)

    async def turn_complete(self) -> None:
        text = "".join(self._said).strip()
        self._said = []
        if text:
            self.listener.said(text)
            await self._server("turn", self.call_id, "agent", text)
        if self.hang_up_after:
            asyncio.get_running_loop().create_task(self._drop_after_playout())
        self.listener.done()

    async def _drop_after_playout(self) -> None:
        while self.listener.clock() < self.listener.playout.end:
            await asyncio.sleep(0.05)
        await self.ws.close()

    async def send(self, pcm: bytes) -> None:
        await self._send({"realtimeInput": {"audio": {
            "data": base64.b64encode(pcm).decode(), "mimeType": f"audio/pcm;rate={IN_RATE}"}}})

    async def close(self, reason: str) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.server is not None:
            await self._server("end", self.call_id, reason)

    async def cost(self) -> dict[str, float]:
        return {"platform": self.cost_usd}


class Entrant:
    name = "gemini-live"
    profile = "realtime.gemini"
    in_rate = IN_RATE
    local_world = True
    final_record = False
    signals_done = True

    def __init__(self, ctx):
        self.cfg = ctx.config["realtime"]["gemini"]
        self.model = self.cfg["model"]
        self.key = ctx.env.get("GEMINI_API_KEY", "")
        if not self.key:
            raise RuntimeError("Gemini Live runs need GEMINI_API_KEY")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
