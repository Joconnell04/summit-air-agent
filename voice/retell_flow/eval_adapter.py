"""The Retell Conversation Flow agent as an agent under test, through Retell's text chat API.

The harness loads it by name as stack `retell_flow` (evals/stacks.py `load_adapter`). It is the
single-prompt adapter (voice/retell/eval_adapter.py) pointed at the flow's chat agent: the same eval
instance (tool server, mock and bridge on the summit-air server), the same mock reset before every
scenario, one scenario at a time. Two differences:

- RETELL_FLOW_CHAT_AGENT_ID names the chat agent (printed by `make retell-flow-push EVAL_URL=...`).
- The flow's end node ends the chat itself, so ending it again is not an error.
"""

import os

from voice.retell import eval_adapter as single

NEEDED = (
    "RETELL_API_KEY",
    "RETELL_FLOW_CHAT_AGENT_ID",
    "RETELL_EVAL_TOOL_SERVER",
    "RETELL_EVAL_DEBUG_TOKEN",
)


class FlowChatAgent(single.RetellChatAgent):
    def end(self, reason: str) -> None:
        if self.server is not None:
            self.server.end(self.call_id, reason)
        response = self.http.patch(f"/end-chat/{self.chat_id}")
        # A chat the flow's end node closed answers 4xx; it is already ended, which is the point.
        if response.status_code >= 500:
            response.raise_for_status()


def open_agent(call_id: str, caller_id: str, env: dict | None = None):
    env = dict(env if env is not None else os.environ)
    missing = [k for k in NEEDED if not env.get(k)]
    if missing:
        raise single.RetellNotReady(
            f"Retell flow runs need {', '.join(missing)}; see voice/retell_flow/README.md"
        )
    env["RETELL_CHAT_AGENT_ID"] = env["RETELL_FLOW_CHAT_AGENT_ID"]
    agent, server = single.open_agent(call_id, caller_id, env)
    flow_agent = FlowChatAgent(agent.http, agent.open_chat, caller_id, call_id, agent.env)
    flow_agent.server = server
    return flow_agent, server

