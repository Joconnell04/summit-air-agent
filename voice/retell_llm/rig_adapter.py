"""Retell in custom-LLM mode on the simulated audio rig (evals/audio_rig), loaded by name.

The calls are web calls, opened the same way as the single-prompt baseline's
(voice/retell/rig_adapter.py), on a voice agent kept for the rig whose response engine is our
socket on the eval instance (voice/retell_llm/llm_socket.py, `/eval/retell-llm/` behind Caddy).
The agent has no webhook: the socket posts every line and closes the call on the tool server, so
nothing is posted twice. The mock is reset and earlier calls are forgotten before every call, and
the call is started on the eval tool server under the rig's id before Retell opens the socket.
"""

import asyncio

from voice.retell import build
from voice.retell import rig_adapter as single_prompt
from voice.retell.eval_adapter import RetellNotReady

from .llm_socket import ROOT as REPO
from .llm_socket import dead_air_checks, socket_key

TEST_AGENT_NAME = "Summit Air rig test (Retell custom LLM, audio bake-off only)"
# Kept from the single-prompt agent's dials; the rest are the worker's, because the socket now
# answers them: the dead-air check-ins (config/models.yaml dead_air), and no hang-up of Retell's
# own before the socket's goodbye.
CHECK_IN_MS = 120_000


def socket_url(eval_base: str, api_key: str) -> str:
    """wss://<host>/eval/retell-llm/<key>; Retell adds the call id."""
    base = eval_base.replace("https://", "wss://", 1).rstrip("/")
    return f"{base}/retell-llm/{socket_key(api_key)}"


def rig_agent_body(eval_base: str, api_key: str, voice: dict) -> dict:
    """The production voice agent's voice and dials, on our socket instead of Retell's LLM."""
    body = build.agent_body("", eval_base, voice.get("voice_id"), voice.get("voice_model"))
    body["agent_name"] = TEST_AGENT_NAME
    body["response_engine"] = {"type": "custom-llm",
                               "llm_websocket_url": socket_url(eval_base, api_key)}
    for field in ("webhook_url", "webhook_events"):
        body.pop(field, None)
    checks = dead_air_checks(REPO / "config" / "models.yaml")
    body["reminder_trigger_ms"] = CHECK_IN_MS
    body["reminder_max_count"] = checks
    body["end_call_after_silence_ms"] = CHECK_IN_MS * (checks + 1)
    return {k: v for k, v in body.items() if v is not None}


class Entrant(single_prompt.Entrant):
    name = "retell-llm-rig"

    async def start(self) -> None:
        chat = await asyncio.to_thread(self.retell.request, "GET",
                                       f"/get-chat-agent/{self.env['RETELL_CHAT_AGENT_ID']}")
        eval_base = chat["webhook_url"].removesuffix("/retell/webhook")
        listed = await asyncio.to_thread(self.retell.request, "POST", "/v2/list-agents", {})
        items = listed.get("items", []) if isinstance(listed, dict) else listed
        voice = next((a for a in items if a.get("agent_name") == build.AGENT_NAME), None)
        if voice is None:
            raise RetellNotReady("no production Retell voice agent to copy the voice from")
        body = rig_agent_body(eval_base, self.env["RETELL_API_KEY"], voice)
        existing = next((a for a in items if a.get("agent_name") == TEST_AGENT_NAME), None)
        if existing:
            self.agent_id = existing["agent_id"]
            await asyncio.to_thread(self.retell.request, "PATCH",
                                    f"/update-agent/{self.agent_id}", body)
        else:
            created = await asyncio.to_thread(self.retell.request, "POST", "/create-agent", body)
            self.agent_id = created["agent_id"]


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
