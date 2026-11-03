"""The Retell baseline as an agent under test, through Retell's text chat API.

Retell runs its own model and calls our tool server through its custom functions, so the code
checks read the same record they read for our stack, from that server's /debug/calls/{id}. The
Retell lane (brief 05) provides the chat agent, an eval tool server with its own mock, and
`voice.retell.chat.open_chat`, which starts the call on that server under our call id, creates the
chat and returns its id and opening line. Until those exist a Retell run stops with a clear message
instead of guessing.

On Retell the safety lines are said by its model (the server checks the wording), not spoken from
code: Retell chat can't insert them mid-reply. That is a real bake-off difference.

Endpoints (SOURCE: https://docs.retellai.com/api-references/create-chat-completion.md):
POST /create-chat-completion {chat_id, content} returns the new messages; PATCH /end-chat/{chat_id}
ends the chat, and its webhook closes the call on the tool server.
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


def _open_chat():
    try:
        from voice.retell.chat import open_chat
    except ImportError as err:
        raise RetellNotReady("voice.retell.chat is not here yet; it comes with brief 05") from err
    return open_chat


class RetellNotReady(RuntimeError):
    pass


class RetellChatAgent:
    def __init__(self, http: httpx.Client, open_chat, caller_id: str, call_id: str, env: dict):
        self.http = http
        self.open_chat = open_chat
        self.caller_id = caller_id
        # Our call id stays the tool server's id; the bridge maps the chat to it.
        self.call_id = call_id
        self.chat_id = ""
        self.env = env

    def start(self) -> AgentTurn:
        t0 = time.perf_counter()
        self.chat_id, opening = self.open_chat(self.call_id, self.caller_id, self.env)
        delay = (time.perf_counter() - t0) * 1000
        timing = TurnTiming(kind="plain", reply_delay_ms=delay, model_first_token_ms=delay)
        return AgentTurn(opening or "", timing)

    def hear(self, caller_text: str) -> AgentTurn:
        t0 = time.perf_counter()
        response = self.http.post(
            "/create-chat-completion", json={"chat_id": self.chat_id, "content": caller_text}
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
        """Ending the chat fires Retell's webhook, which closes the call on the tool server."""
        self.http.patch(f"/end-chat/{self.chat_id}").raise_for_status()


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
    open_chat = _open_chat()
    http = httpx.Client(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {env['RETELL_API_KEY']}"},
        timeout=30,
    )
    server = ToolServer(httpx.Client(base_url=env["RETELL_EVAL_TOOL_SERVER"], timeout=30),
                        env["DEBUG_TOKEN"])
    return RetellChatAgent(http, open_chat, caller_id, call_id, dict(env)), server
