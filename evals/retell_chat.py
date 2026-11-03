"""The Retell baseline as an agent under test, through Retell's text chat API.

Retell runs its own model and calls our tool server through its custom functions, so the code
checks read the same record they read for our stack, from that server's /debug/calls/{id}. What
this needs from the Retell lane (brief 05): a chat agent built from the same prompts, the tool
server it calls running on a scratch copy of the mock, and the chat id used as our call id.
Until then a Retell run stops with a clear message instead of guessing.

Endpoints (SOURCE: https://docs.retellai.com/api-references/create-chat.md and
create-chat-completion.md): POST /create-chat {agent_id, metadata, retell_llm_dynamic_variables}
returns chat_id; POST /create-chat-completion {chat_id, content} returns the new messages.
"""
import json
import os
import time

import httpx

from evals.results import TurnTiming
from evals.stacks import AgentTurn
from evals.world import ToolServer

BASE_URL = "https://api.retellai.com"
NEEDED = ("RETELL_API_KEY", "RETELL_CHAT_AGENT_ID", "RETELL_EVAL_TOOL_SERVER", "DEBUG_TOKEN")


class RetellNotReady(RuntimeError):
    pass


class RetellChatAgent:
    def __init__(self, http: httpx.Client, agent_id: str, caller_id: str, call_id: str):
        self.http = http
        self.agent_id = agent_id
        self.caller_id = caller_id
        self.call_id = call_id

    def start(self) -> AgentTurn:
        t0 = time.perf_counter()
        body = {
            "agent_id": self.agent_id,
            "metadata": {"eval_call_id": self.call_id},
            "retell_llm_dynamic_variables": {"caller_id": self.caller_id},
        }
        response = self.http.post("/create-chat", json=body)
        response.raise_for_status()
        # The Retell lane maps the chat id to the tool server's call id.
        self.call_id = response.json()["chat_id"]
        delay = (time.perf_counter() - t0) * 1000
        return AgentTurn("", TurnTiming(kind="plain", reply_delay_ms=delay, model_first_token_ms=0))

    def hear(self, caller_text: str) -> AgentTurn:
        t0 = time.perf_counter()
        response = self.http.post(
            "/create-chat-completion", json={"chat_id": self.call_id, "content": caller_text}
        )
        response.raise_for_status()
        delay = (time.perf_counter() - t0) * 1000
        messages = response.json()["messages"]
        said = [m["content"] for m in messages if m["role"] == "agent" and m.get("content")]
        calls = [m for m in messages if m["role"] == "tool_call_invocation"]
        ended = any(
            m.get("type") == "end_call"
            or (m.get("name") == "end_call" and _hang_up(messages, m["tool_call_id"]))
            for m in calls
        )
        # No streaming over chat, so the whole reply time is the delay; Retell's per-turn voice
        # timings come from its call data, mapped by the Retell lane.
        timing = TurnTiming(kind="lookup" if calls else "plain", reply_delay_ms=delay,
                            model_first_token_ms=delay)
        return AgentTurn(" ".join(said), timing, ended=ended)

    def end(self, reason: str) -> None:
        """The chat-ended webhook closes the call on the tool server (brief 05)."""


def _hang_up(messages: list[dict], tool_call_id: str) -> bool:
    for m in messages:
        if m["role"] == "tool_call_result" and m.get("tool_call_id") == tool_call_id:
            try:
                return bool(json.loads(m["content"]).get("data", {}).get("hang_up"))
            except (ValueError, AttributeError):
                return False
    return False


def open_retell(call_id: str, caller_id: str, env: dict | None = None):
    env = env if env is not None else os.environ
    missing = [k for k in NEEDED if not env.get(k)]
    if missing:
        raise RetellNotReady(
            f"Retell runs need {', '.join(missing)}; the Retell lane (brief 05) sets them up"
        )
    http = httpx.Client(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {env['RETELL_API_KEY']}"},
        timeout=30,
    )
    server = ToolServer(httpx.Client(base_url=env["RETELL_EVAL_TOOL_SERVER"], timeout=30),
                        env["DEBUG_TOKEN"])
    return RetellChatAgent(http, env["RETELL_CHAT_AGENT_ID"], caller_id, call_id), server
