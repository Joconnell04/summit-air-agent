"""The Deepgram Voice Agent entry on the simulated audio rig (evals/audio_rig, DECISIONS.md 026),
loaded by name so shared code never imports this folder. Each call is the same agent.Call the
phone bridge runs, at 24 kHz linear PCM, against the rig's scratch world for the scenario."""

import asyncio

from .agent import Call, prepare

RATE = 24000
RIG_AUDIO = {"input": {"encoding": "linear16", "sample_rate": RATE},
             "output": {"encoding": "linear16", "sample_rate": RATE, "container": "none"}}


class RigServer:
    """The scenario's tool server (evals/world.py, synchronous) as agent.Call wants it."""

    def __init__(self, server, call_id: str):
        self.server, self.call_id = server, call_id

    async def turn(self, speaker: str, text: str) -> dict:
        return await asyncio.to_thread(self.server.turn, self.call_id, speaker, text)

    async def tool(self, name: str, args: dict, key: str | None) -> dict:
        return await asyncio.to_thread(self.server.tool, name, self.call_id, args, key)

    async def end(self, reason: str) -> dict:
        return await asyncio.to_thread(self.server.end, self.call_id, reason)


class Line:
    def __init__(self, entrant: "Entrant"):
        self.entrant = entrant
        self.call: Call | None = None
        self.listener = None
        self.caller_seconds = 0.0

    async def open(self, call_id, caller_id, world, listener):
        self.listener = listener
        started = await asyncio.to_thread(world.server.start, call_id, caller_id)
        message, writes, goodbye = prepare(started, self.entrant.cfg, self.entrant.model,
                                           self.entrant.env["ANTHROPIC_API_KEY"], RIG_AUDIO)
        self.call = Call(RigServer(world.server, call_id), writes, self._play, self._clear,
                         self._hang_up, self.entrant.env["DEEPGRAM_API_KEY"], done=listener.done)
        await self.call.open(call_id, message, goodbye)
        asyncio.get_running_loop().create_task(self._watch())
        return world.server

    async def _play(self, pcm: bytes) -> None:
        self.listener.audio(pcm, RATE, live=True)

    async def _clear(self) -> None:
        return None

    async def _hang_up(self) -> None:
        """The goodbye has been sent; drop the line once it has played."""
        while self.listener.clock() < self.listener.playout.end:
            await asyncio.sleep(0.05)
        await self.call.ws.close()

    async def _watch(self) -> None:
        await self.call.closed.wait()
        self.listener.hung_up()

    async def send(self, pcm: bytes) -> None:
        self.caller_seconds += len(pcm) / 2 / RATE
        await self.call.send(pcm)

    async def close(self, reason: str) -> None:
        if self.call is not None:
            await self.call.close(reason)

    async def cost(self) -> dict[str, float]:
        """Deepgram bills the session by the minute; the caller's audio time stands in for it."""
        return {"platform": self.caller_seconds / 60 * self.entrant.cfg["price_per_minute"]}


class Entrant:
    name = "deepgram-agent"
    profile = "realtime.deepgram_agent"
    in_rate = RATE
    local_world = True
    final_record = False
    signals_done = True

    def __init__(self, ctx):
        self.cfg = ctx.config["realtime"]["deepgram_agent"]
        self.model = ctx.config["profiles"][self.cfg["brain"]]["model"]
        self.env = ctx.env
        missing = [k for k in ("DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY") if not ctx.env.get(k)]
        if missing:
            raise RuntimeError(f"Deepgram agent runs need {', '.join(missing)}")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
