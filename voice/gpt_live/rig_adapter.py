"""GPT-Live on the simulated audio rig (evals/audio_rig): the same session as the phone path
(session.py), with the caller's audio on GPT-Live's primary WebSocket instead of SIP. Loaded by
name so shared code never imports this folder.

Connection (SOURCE: https://developers.openai.com/api/docs/guides/voice-websockets?api=live, read
2026-11-05): `wss://api.openai.com/v1/live/sessions`, `session.start` first, then wait for
`session.started`; caller audio goes up as `session.input_audio.append` and the agent's comes down
as `session.output_audio.delta`, PCM16 at 24 kHz. GPT-Live sends no "done speaking" event, so the
rig waits for silence after the agent's last sound (signals_done is False).

Model, voice, backend model and prices are `realtime.gpt_live` in config/models.yaml.
"""

import asyncio
import base64
import json

import websockets

from voice.gpt_live import session as live

WS_URL = "wss://api.openai.com/v1/live/sessions"
RATE = 24000
CLOSE_WAIT_S = 5.0


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
        self.conversation: live.Conversation | None = None
        self.started = asyncio.Event()
        self.ended = False
        self.greeting = ""

    async def _send(self, event: dict) -> None:
        if self.ws is not None and not self.ended:
            await self.ws.send(json.dumps(event))

    async def open(self, call_id, caller_id, world, listener):
        self.call_id, self.listener, self.server = call_id, listener, world.server
        started = await asyncio.to_thread(self.server.start, call_id, caller_id)
        data = started["data"]
        specs, voice = live.tenant_files(data)
        greeting = live.opening(data, started.get("say", ""), voice)
        config = live.session_config(data, specs, self.cfg, greeting,
                                     audio_format={"type": "audio/pcm", "rate": RATE})
        self.conversation = live.Conversation(
            call_id, self.server, {s["name"] for s in specs if s["write"]}, self._send,
            self._hang_up, on_agent_line=listener.said, price=self.cfg["price"])
        self.ws = await self.connect(
            WS_URL, additional_headers={"Authorization": f"Bearer {self.entrant.key}"},
            max_size=None)
        self.reader = asyncio.create_task(self._read())
        await self.ws.send(json.dumps({"type": "session.start", "event_id": "start",
                                       "session": config}))
        await asyncio.wait_for(self.started.wait(), 15)
        self.greeting = greeting
        return world.server

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session.started":
                    self.started.set()
                elif kind == "session.output_audio.delta":
                    self.listener.audio(base64.b64decode(event["delta"]), RATE)
                    self.conversation.last_agent_delta = self.conversation.clock()
                await self.conversation.handle(event)
                if kind == "session.closed":
                    break
        except websockets.ConnectionClosed:
            pass
        finally:
            self.ended = True
            self.listener.hung_up()

    async def _hang_up(self) -> None:
        """The server allowed the hang-up: drop the line once the goodbye has played."""
        listener = self.listener
        while listener.clock() < listener.playout.end:
            await asyncio.sleep(0.05)
        await self._close_session()

    async def _close_session(self) -> None:
        if self.ws is None or self.ended:
            return
        await self._send({"type": "session.close"})
        if self.reader:
            try:
                await asyncio.wait_for(asyncio.shield(self.reader), CLOSE_WAIT_S)
            except TimeoutError:
                pass
        await self.ws.close()

    async def send(self, pcm: bytes) -> None:
        if self.ended:
            return
        await self._send({"type": "session.input_audio.append",
                          "audio": base64.b64encode(pcm).decode()})
        if self.greeting:
            # The greeting is asked for once input audio is running (the docs' sequence); asked
            # for before the first frame, it was skipped on one of two rig calls (MEASURED).
            greeting, self.greeting = self.greeting, ""
            await self._send(live.greet_event(greeting))

    async def close(self, reason: str) -> None:
        if self.conversation is not None:
            await self.conversation.drain()
        await self._close_session()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.server is not None:
            await asyncio.to_thread(self.server.end, self.call_id, reason)

    async def cost(self) -> dict[str, float]:
        if self.conversation is None:
            return {}
        return self.conversation.cost(self.cfg["price"]["per_minute"])


class Entrant:
    name = "gpt-live"
    profile = "realtime.gpt_live"
    in_rate = RATE
    local_world = True
    final_record = False
    signals_done = False

    def __init__(self, ctx):
        self.cfg = ctx.config["realtime"]["gpt_live"]
        self.model = self.cfg["model"]
        self.key = ctx.env.get("OPENAI_API_KEY", "")
        if not self.key:
            raise RuntimeError("GPT-Live runs need OPENAI_API_KEY")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
