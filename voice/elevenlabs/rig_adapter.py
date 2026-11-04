"""ElevenLabs Agents on the simulated audio rig (evals/audio_rig, DECISIONS.md 026), loaded by name
so shared code never imports this folder.

For a run it serves our brain (brain.py) on a local port, opens a temporary ngrok tunnel to it,
and creates a test agent whose custom LLM is that tunnel; the agent is deleted when the run ends.
Each call starts on the rig's scratch world through the brain, then opens ElevenLabs' conversation
WebSocket and streams the caller's audio in, 16 kHz PCM both ways.

WebSocket messages (SOURCE: https://elevenlabs.io/docs/eleven-agents/libraries/web-sockets):
`conversation_initiation_client_data` first (overrides, dynamic variables, extra body), then
`{"user_audio_chunk": <base64>}`; the server sends `conversation_initiation_metadata`, `audio`,
`agent_response`, `user_transcript`, `interruption` and `ping`, which needs a `pong`. The socket
closes when the agent's end-call tool runs.
"""

import asyncio
import base64
import json
import secrets

import httpx
import websockets

from evals.audio_rig.router import Serving
from evals.audio_rig.tunnel import Tunnel

from .agent import ElevenLabs, agent_body
from .brain import Brain, build_app, load_brain
from .server import ToolServer

WS_URL = "wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"
RATE = 16000


def pong(message: dict) -> dict | None:
    if message.get("type") == "ping":
        return {"type": "pong", "event_id": message["ping_event"]["event_id"]}
    return None


def initiation(call_id: str, first_message: str) -> dict:
    return {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {"agent": {"first_message": first_message}},
        "dynamic_variables": {"summit_call_id": call_id},
        "custom_llm_extra_body": {"summit_call_id": call_id},
    }


def handle(message: dict, listener, state: dict) -> dict | None:
    """One server message: agent audio and text to the listener; returns a reply to send."""
    kind = message.get("type")
    if kind == "conversation_initiation_metadata":
        state["conversation_id"] = message["conversation_initiation_metadata_event"][
            "conversation_id"]
    elif kind == "audio":
        listener.audio(base64.b64decode(message["audio_event"]["audio_base_64"]), RATE)
    elif kind == "agent_response":
        listener.said(message["agent_response_event"]["agent_response"])
    return pong(message)


class Line:
    def __init__(self, entrant: "Entrant"):
        self.entrant = entrant
        self.ws = None
        self.reader = None
        self.call_id = ""
        self.state: dict = {}
        self.brain_cost = 0.0

    async def open(self, call_id, caller_id, world, listener):
        self.call_id = call_id
        local = self.entrant.local
        started = await local.post(f"/{self.entrant.token}/calls",
                                   json={"call_id": call_id, "caller_id": caller_id})
        started.raise_for_status()
        url = WS_URL.format(agent_id=self.entrant.agent_id)
        self.ws = await websockets.connect(url, additional_headers={"xi-api-key": self.entrant.key},
                                           max_size=None)
        await self.ws.send(json.dumps(initiation(call_id, started.json()["first_message"])))
        self.reader = asyncio.create_task(self._read(listener))
        return world.server

    async def _read(self, listener) -> None:
        try:
            async for raw in self.ws:
                reply = handle(json.loads(raw), listener, self.state)
                if reply:
                    await self.ws.send(json.dumps(reply))
        except websockets.ConnectionClosed:
            pass
        finally:
            listener.hung_up()

    async def send(self, pcm: bytes) -> None:
        await self.ws.send(json.dumps({"user_audio_chunk": base64.b64encode(pcm).decode()}))

    async def close(self, reason: str) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        ended = await self.entrant.local.post(f"/{self.entrant.token}/calls/{self.call_id}/end",
                                              json={"reason": reason})
        if ended.status_code == 200:
            self.brain_cost = ended.json().get("cost_usd", 0.0)

    async def cost(self) -> dict[str, float]:
        """Platform minutes at the list price, from ElevenLabs' own call length, plus our brain's
        tokens. The plan's credits used are printed for the readout."""
        parts = {"brain_model": self.brain_cost}
        conversation_id = self.state.get("conversation_id")
        if not conversation_id:
            return parts
        # ElevenLabs finishes its record of the call a few seconds after the socket closes.
        for _ in range(10):
            details = await self.entrant.api.conversation(conversation_id)
            meta = details.get("metadata") or {}
            if meta.get("call_duration_secs"):
                break
            await asyncio.sleep(2)
        seconds = meta.get("call_duration_secs") or 0
        print(f"{self.call_id}: ElevenLabs {seconds} s, {meta.get('cost')} credits", flush=True)
        parts["platform"] = seconds / 60 * self.entrant.cfg["price_per_minute"]
        return parts


class Entrant:
    name = "elevenlabs"
    profile = "realtime.elevenlabs"
    in_rate = RATE
    local_world = True
    final_record = False
    signals_done = False

    def __init__(self, ctx):
        self.ctx = ctx
        self.cfg = ctx.config["realtime"]["elevenlabs"]
        self.model = load_brain()["model"]
        self.key = ctx.env.get("ELEVENLABS_API_KEY", "")
        if not self.key:
            raise RuntimeError("ElevenLabs runs need ELEVENLABS_API_KEY")
        self.token = secrets.token_urlsafe(24)
        self.api = ElevenLabs(self.key)
        self.agent_id = ""
        self.serving = None
        self.tunnel = None
        self.local = None

    async def start(self) -> None:
        brain = Brain(ToolServer(self.ctx.router_url, timeout_s=10))
        self.serving = Serving(build_app(brain, self.token)).__enter__()
        self.local = httpx.AsyncClient(base_url=self.serving.url, timeout=30)
        self.tunnel = await asyncio.to_thread(Tunnel(self.serving.port).__enter__)
        body = agent_body(f"{self.tunnel.url}/{self.token}/v1", self.model, self.cfg)
        self.agent_id = await self.api.create_agent(body)

    async def stop(self) -> None:
        if self.agent_id:
            await self.api.delete_agent(self.agent_id)
        if self.tunnel:
            self.tunnel.close()
        if self.serving:
            self.serving.__exit__(None, None, None)
        if self.local:
            await self.local.aclose()
        await self.api.aclose()

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
