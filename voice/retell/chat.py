"""Opening a Retell chat for the eval harness (evals/retell_chat.py, brief 04), so a scenario
runs through the same Retell LLM, prompt and tools as a phone call. The harness then sends caller
lines with /create-chat-completion; the eval bridge posts every line to the tool server from
Retell's chat webhooks and custom functions, as it does for calls.

Needs RETELL_API_KEY, RETELL_CHAT_AGENT_ID (printed by `make retell-push EVAL_URL=...`),
RETELL_EVAL_TOOL_SERVER, the eval tool server that the chat agent's bridge forwards to, and
DEBUG_TOKEN, that server's token (the proxy in front of it asks for it on every request).
"""

import os
from pathlib import Path

import httpx

from prompts.assemble import instructions

from . import conversation
from .api import Retell
from .bridge import ToolServer

RETELL_NUMBER = "(678) 336-6730"


def open_chat(call_id: str, caller_id: str, env: dict | None = None) -> tuple[str, str]:
    """Start the call on the tool server, then the chat with the per-call prompt and greeting.
    Returns the Retell chat id and the agent's opening line."""
    env = env if env is not None else os.environ
    http = httpx.Client(
        base_url=env["RETELL_EVAL_TOOL_SERVER"],
        headers={"X-Debug-Token": env["DEBUG_TOKEN"]},
        timeout=30,
    )
    tools = ToolServer(http)
    start = tools.start(call_id, caller_id, RETELL_NUMBER, "inbound")
    if not start or not start.get("ok"):
        raise RuntimeError(f"/calls/start failed for {call_id}: {start}")
    tenants = Path(env.get("TENANTS_DIR", "tenants"))
    variables = conversation.for_call(
        call_id, start, instructions, lambda slug: conversation.spoken_lines(tenants, slug)
    )
    chat = Retell(env["RETELL_API_KEY"]).request(
        "POST",
        "/create-chat",
        json={
            "agent_id": env["RETELL_CHAT_AGENT_ID"],
            "retell_llm_dynamic_variables": variables,
            "metadata": {"summit_call_id": call_id},
        },
    )
    return chat["chat_id"], variables["greeting"]
