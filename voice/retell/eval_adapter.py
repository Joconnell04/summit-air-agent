"""The Retell baseline as an agent under test, through Retell's text chat API.

This is the Retell side of the eval harness (evals/stacks.py `load_adapter`, which loads
`voice.<stack>.eval_adapter` by name), so nothing outside voice/ imports this folder and deleting it
after the bake-off stays clean (docs/architecture.md).

Retell runs its own model and calls our tool server through its custom functions, so the code
checks read the same record they read for our stack, from that server's /debug/calls/{id}.
`chat.open_chat` starts the call on the eval tool server under our call id, creates the chat and
returns its id and opening line. The eval bridge posts every line to the tool server from Retell's
webhooks, so the harness posts none.

The eval instance has one mock ServiceTitan, shared by every scenario, so `open_agent` first asks
it to rebuild the seeded world (`reset_world`), and Retell runs go one scenario at a time
(`--workers 1`). Without that, bookings from earlier scenarios would change what later ones see.

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

from .chat import open_chat

BASE_URL = "https://api.retellai.com"
NEEDED = (
    "RETELL_API_KEY", "RETELL_CHAT_AGENT_ID", "RETELL_EVAL_TOOL_SERVER", "RETELL_EVAL_DEBUG_TOKEN"
)


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


def reset_world(env: dict, client: httpx.Client | None = None) -> None:
    """Ask the eval instance's mock to rebuild its seeded world (POST /debug/reset, which the mock
    allows only when started with ST_MOCK_ALLOW_RESET=1). The default address is the mock behind the
    eval tool server's proxy; RETELL_EVAL_MOCK_RESET_URL overrides it, for example with an ssh
    tunnel to the server's 127.0.0.1:8111."""
    url = env.get("RETELL_EVAL_MOCK_RESET_URL") or (
        env["RETELL_EVAL_TOOL_SERVER"].rstrip("/") + "/mock/debug/reset"
    )
    client = client or httpx.Client(timeout=60)
    try:
        response = client.post(url, headers={"X-Debug-Token": env["RETELL_EVAL_DEBUG_TOKEN"]})
    except httpx.HTTPError as err:
        raise RetellNotReady(f"could not reach the eval mock's reset at {url}: {err}") from err
    if response.status_code != 200:
        raise RetellNotReady(
            f"the eval mock refused its reset ({response.status_code} at {url}): "
            f"{response.text[:200]}. It needs ST_MOCK_ALLOW_RESET=1 and a route to it "
            "(deploy/retell/Caddyfile, voice/retell/README.md)"
        )


def open_agent(call_id: str, caller_id: str, env: dict | None = None):
    """The agent under test and the tool server whose record the checks read, on a clean mock."""
    env = env if env is not None else os.environ
    missing = [k for k in NEEDED if not env.get(k)]
    if missing:
        raise RetellNotReady(
            f"Retell runs need {', '.join(missing)}; the Retell lane (brief 05) sets them up"
        )
    reset_world(env)
    http = httpx.Client(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {env['RETELL_API_KEY']}"},
        timeout=30,
    )
    # The proxy in front of the eval tool server wants its token on every request.
    token = env["RETELL_EVAL_DEBUG_TOKEN"]
    server = ToolServer(
        httpx.Client(base_url=env["RETELL_EVAL_TOOL_SERVER"], headers={"X-Debug-Token": token},
                     timeout=30),
        token,
    )
    chat_env = {**env, "DEBUG_TOKEN": token}
    return RetellChatAgent(http, open_chat, caller_id, call_id, chat_env), server
