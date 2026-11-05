"""The Retell Conversation Flow agent on the simulated audio rig (evals/audio_rig, DECISIONS.md 026),
run the way the single-prompt agent ran in evals/rounds/bakeoff-audio-01.md: a Retell web call on a
voice agent kept for the rig, against the eval instance's bridge, tool server and mock, with the
mock reset and earlier calls forgotten before each call.

The single-prompt adapter (voice/retell/rig_adapter.py) does the call itself. What differs is the
agent: the voice flow (greeting first, transfers wired) built for the eval bridge, on the voice
flow agent's voice and dials, pushed at the start of each run.
"""

import asyncio
from pathlib import Path

from prompts.assemble import job_types, tool_specs
from voice.retell import rig_adapter as single

from . import build, push

TEST_AGENT_NAME = "Summit Air rig test (Retell flow, audio bake-off only)"


def rig_agent_body(flow_id: str, eval_base: str, voice: dict) -> dict:
    body = build.agent_body(flow_id, eval_base, voice.get("voice_id"), voice.get("voice_model"))
    body["agent_name"] = TEST_AGENT_NAME
    return {k: v for k, v in body.items() if v is not None}


class Entrant(single.Entrant):
    name = "retell-flow-rig"

    def __init__(self, ctx):
        super().__init__(ctx)
        if not self.env.get("RETELL_FLOW_CHAT_AGENT_ID"):
            raise single.RetellNotReady("Retell flow rig runs need RETELL_FLOW_CHAT_AGENT_ID")
        # The eval bridge's address is read from the flow's chat agent.
        self.env["RETELL_CHAT_AGENT_ID"] = self.env["RETELL_FLOW_CHAT_AGENT_ID"]

    async def start(self) -> None:
        chat = await asyncio.to_thread(
            self.retell.request, "GET", f"/get-chat-agent/{self.env['RETELL_CHAT_AGENT_ID']}"
        )
        eval_base = chat["webhook_url"].removesuffix("/retell/webhook")
        live = await asyncio.to_thread(push.find_agent, self.retell, build.AGENT_NAME)
        voice = await asyncio.to_thread(
            self.retell.request, "GET", f"/get-agent/{live['agent_id']}"
        )
        tenant = Path(self.env.get("TENANTS_DIR", "tenants")) / "summit-air"
        flow = build.flow_body(
            tool_specs(job_types(tenant)),
            eval_base,
            self.model,
            push.tenant_settings(tenant),
            chat=False,
        )
        log: list[str] = []
        self.agent_id = await asyncio.to_thread(
            push.ensure_agent,
            self.retell,
            "agent",
            TEST_AGENT_NAME,
            lambda flow_id: rig_agent_body(flow_id, eval_base, voice),
            flow,
            log,
        )
        for line in log:
            print(line, flush=True)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
