"""The Retell baseline on the simulated audio rig (evals/audio_rig, DECISIONS.md 026), as a
calibration entrant beside our stack. Loaded by name so shared code never imports this folder.

Each call is a Retell web call: a plain WebRTC connection through Retell's proxy, which the rig
opens with the call's access token (evals/audio_rig/webrtc.py) and streams the caller's audio
into. The calls use a voice agent kept for the rig and named as a test agent, set at the start of
each run to the production voice agent's voice and dials and the eval chat agent's LLM, whose
functions and webhooks go to the eval instance's bridge, tool server and mock
(voice/retell/README.md). As for the chat evals, the mock is reset before every call, the call is
started on the eval tool server under the rig's call id, and calls run one at a time.

Endpoints (SOURCE: https://docs.retellai.com/api-references/create-web-call and
https://docs.retellai.com/deploy/native-web-call): POST /v3/create-web-call returns `call_id`,
`access_token` and `ice_servers` (the v2 endpoint was retired on 2026-10-18); the offer goes to
POST /webrtc-proxy/{call_id}/v1/webrtc/sessions and the session ends with DELETE on it. Retell
sends no "agent finished" event on a web call, so the rig waits for silence.
"""

import asyncio
import os
from pathlib import Path

import httpx

from evals.audio_rig.webrtc import WebRtcPhone
from evals.world import ToolServer
from prompts.assemble import instructions

from . import build, conversation
from .api import Retell
from .chat import RETELL_NUMBER
from .eval_adapter import NEEDED, RetellNotReady, reset_world

PROXY = "https://api.retellai.com/webrtc-proxy/{call_id}/v1/webrtc/sessions"
RATE = 24000
# Kept between runs and updated at the start of each, so this layer never needs to delete an
# agent (voice/retell/api.py allows no deletes).
TEST_AGENT_NAME = "Summit Air rig test (Retell, audio bake-off only)"


def rig_agent_body(llm_id: str, eval_base: str, voice: dict) -> dict:
    """The production voice agent's dials on the eval LLM and eval bridge."""
    body = build.agent_body(llm_id, eval_base, voice.get("voice_id"), voice.get("voice_model"))
    body["agent_name"] = TEST_AGENT_NAME
    return {k: v for k, v in body.items() if v is not None}


class Line:
    def __init__(self, entrant: "Entrant"):
        self.entrant = entrant
        self.phone = WebRtcPhone(RATE)
        self.retell_call_id = ""
        self.token = ""
        self.session_id = ""

    async def open(self, call_id, caller_id, world, listener):
        env = self.entrant.env
        await asyncio.to_thread(reset_world, env)
        server = self.entrant.tool_server()
        started = await asyncio.to_thread(server.start, call_id, caller_id, RETELL_NUMBER)
        if not started.get("ok"):
            raise RetellNotReady(f"/calls/start failed for {call_id}: {started}")
        tenants = Path(env.get("TENANTS_DIR", "tenants"))
        variables = conversation.for_call(
            call_id, started, instructions, lambda slug: conversation.spoken_lines(tenants, slug)
        )
        web = await asyncio.to_thread(self.entrant.retell.request, "POST", "/v3/create-web-call",
                                      {"agent_id": self.entrant.agent_id,
                                       "metadata": {"summit_call_id": call_id},
                                       "retell_llm_dynamic_variables": variables})
        self.retell_call_id, self.token = web["call_id"], web["access_token"]
        await self.phone.connect(web.get("ice_servers") or [], self._offer, listener)
        return server

    async def _offer(self, sdp: str) -> str:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.post(PROXY.format(call_id=self.retell_call_id),
                                       headers={"Authorization": f"Bearer {self.token}"},
                                       json={"identity": "client", "sdp": sdp})
        response.raise_for_status()
        answer = response.json()
        self.session_id = answer["session_id"]
        return answer["sdp"]

    async def send(self, pcm: bytes) -> None:
        await self.phone.send(pcm)

    async def close(self, reason: str) -> None:
        """Ending the session ends the web call; Retell's call_ended webhook closes it on the tool
        server."""
        await self.phone.close()
        if self.session_id:
            async with httpx.AsyncClient(timeout=20) as http:
                await http.delete(f"{PROXY.format(call_id=self.retell_call_id)}/{self.session_id}",
                                  headers={"Authorization": f"Bearer {self.token}"})

    async def cost(self) -> dict[str, float]:
        """Retell's own billed cost for the call, once it has it."""
        for _ in range(15):
            call = await asyncio.to_thread(self.entrant.retell.request, "GET",
                                           f"/v2/get-call/{self.retell_call_id}")
            combined = (call.get("call_cost") or {}).get("combined_cost")
            if combined is not None:
                return {"platform": combined / 100}
            await asyncio.sleep(2)
        return {}


class Entrant:
    name = "retell-rig"
    profile = "fast"
    in_rate = RATE
    local_world = False
    final_record = True
    signals_done = False

    def __init__(self, ctx):
        self.env = {**os.environ, **ctx.env}
        missing = [k for k in NEEDED if not self.env.get(k)]
        if missing:
            raise RetellNotReady(f"Retell rig runs need {', '.join(missing)}")
        self.model = ctx.config["profiles"][self.profile]["model"]
        self.retell = Retell(self.env["RETELL_API_KEY"])
        self.agent_id = ""

    def tool_server(self) -> ToolServer:
        token = self.env["RETELL_EVAL_DEBUG_TOKEN"]
        return ToolServer(httpx.Client(base_url=self.env["RETELL_EVAL_TOOL_SERVER"],
                                       headers={"X-Debug-Token": token}, timeout=30), token)

    async def start(self) -> None:
        chat = await asyncio.to_thread(self.retell.request, "GET",
                                       f"/get-chat-agent/{self.env['RETELL_CHAT_AGENT_ID']}")
        eval_base = chat["webhook_url"].removesuffix("/retell/webhook")
        listed = await asyncio.to_thread(self.retell.request, "POST", "/v2/list-agents", {})
        items = listed.get("items", []) if isinstance(listed, dict) else listed
        voice = next(a for a in items if a.get("agent_name") == build.AGENT_NAME)
        body = rig_agent_body(chat["response_engine"]["llm_id"], eval_base, voice)
        existing = next((a for a in items if a.get("agent_name") == TEST_AGENT_NAME), None)
        if existing:
            self.agent_id = existing["agent_id"]
            await asyncio.to_thread(self.retell.request, "PATCH",
                                    f"/update-agent/{self.agent_id}", body)
        else:
            created = await asyncio.to_thread(self.retell.request, "POST", "/create-agent", body)
            self.agent_id = created["agent_id"]

    async def stop(self) -> None:
        return None

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
