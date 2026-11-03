"""The LiveKit Anthropic plugin runs on the anthropic SDK the eval harness needs (pyproject.toml,
override-dependencies). A faked Anthropic stream checks that our tool list is accepted and sent,
and that a reply streams back."""

import asyncio
import json

import anthropic
import httpx2
from livekit.agents import llm, utils
from livekit.plugins import anthropic as lk_anthropic

from prompts import assemble
from tests.tool_server.toolkit import TENANT_DIR
from voice.livekit.bridge import Bridge
from voice.livekit.tools import ToolLog, build_tools

EVENTS = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
    ),
    (
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Got it."},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 3},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


def test_the_anthropic_plugin_streams_a_reply_with_our_tools():
    sent = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        body = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in EVENTS)
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    # The anthropic 2.x SDK runs on httpx2, so its fake transport comes from there.
    client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    )
    specs = assemble.tool_specs(assemble.job_types(TENANT_DIR))
    tools = build_tools(
        specs, None, Bridge([], None, lambda: True), ToolLog(), lambda *a: None, lambda t: None
    )

    async def run() -> str:
        async with utils.http_context.open():
            model = lk_anthropic.LLM(
                model="claude-haiku-5-5", api_key="test", client=client, caching="ephemeral"
            )
            ctx = llm.ChatContext()
            ctx.add_message(role="system", content="You answer the phone.")
            ctx.add_message(role="user", content="My heat is out.")
            text = ""
            async with model.chat(chat_ctx=ctx, tools=tools) as stream:
                async for chunk in stream:
                    if chunk.delta and chunk.delta.content:
                        text += chunk.delta.content
            return text

    assert asyncio.run(run()) == "Got it."
    request = sent[0]
    assert {t["name"] for t in request["tools"]} == {s["name"] for s in specs}
    assert "temperature" not in request
