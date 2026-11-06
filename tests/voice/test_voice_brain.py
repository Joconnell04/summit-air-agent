"""What the worker's model builder sends to Anthropic on a call, checked on a faked stream
(issue #30: the first reply of each turn took 1.5 s because the model was left to think)."""

import asyncio
import json

import anthropic
import httpx2
from livekit.agents import llm, utils

from prompts import assemble
from tests.tool_server.toolkit import TENANT_DIR
from voice.archive.livekit.brain import ClaudeLLM, build_llm, thinking_request
from voice.archive.livekit.bridge import Bridge
from voice.archive.livekit.settings import Profile, load_profile
from voice.archive.livekit.tools import ToolLog, build_tools

START = {
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
}
EVENTS = [
    ("message_start", START),
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


def ask(profile: Profile, lines: list[str]) -> list[dict]:
    """The requests the worker's model sends for a call with these caller lines."""
    sent: list[dict] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        body = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in EVENTS)
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = anthropic.AsyncAnthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    )
    specs = assemble.tool_specs(assemble.job_types(TENANT_DIR))
    tools = build_tools(
        specs, None, Bridge([], None, lambda: True), ToolLog(), lambda *a: None, lambda t: None
    )

    async def run() -> None:
        async with utils.http_context.open():
            model = ClaudeLLM(
                model=profile.model,
                api_key="test",
                client=client,
                caching="ephemeral",
                thinking=thinking_request(profile),
            )
            ctx = llm.ChatContext()
            ctx.add_message(role="system", content="You answer the phone.")
            for line in lines:
                ctx.add_message(role="user", content=line)
                async with model.chat(chat_ctx=ctx, tools=tools) as stream:
                    async for _ in stream:
                        pass
                ctx.add_message(role="assistant", content="Got it.")

    asyncio.run(run())
    return sent


def test_a_profile_with_thinking_off_says_so_on_every_request():
    # Left out, Haiku 5.5 thinks before it speaks (1.5 s to the first word, docs/latency.md).
    sent = ask(load_profile("fast"), ["My heat is out.", "It's a gas boiler."])
    assert [r["thinking"] for r in sent] == [{"type": "disabled"}, {"type": "disabled"}]


def test_each_profile_turns_thinking_off_the_way_its_model_allows():
    assert thinking_request(load_profile("fast-prev")) == {"type": "disabled"}
    assert thinking_request(load_profile("quality")) == {"type": "between_tools"}
    assert thinking_request(Profile("x", "anthropic", "claude-haiku-5-5", True)) == {
        "type": "adaptive"
    }


def test_the_cached_prefix_is_the_same_on_every_request_of_a_call():
    # Prompt caching matches a prefix: tools, then system, then the earlier messages. Only the end
    # of the request may differ from one turn to the next.
    first, second, third = ask(load_profile("fast"), ["Heat is out.", "A boiler.", "Last night."])
    assert first["system"] == second["system"] == third["system"]
    assert first["tools"] == second["tools"] == third["tools"]
    assert "cache_control" in first["system"][-1]
    assert "cache_control" in first["tools"][-1]
    firsts = [r["messages"][0]["content"][0]["text"] for r in (first, second, third)]
    assert firsts == ["Heat is out."] * 3


def test_the_worker_builds_the_profiles_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    model = build_llm(load_profile("fast"))
    assert isinstance(model, ClaudeLLM) and model.model == "claude-haiku-5-5"
    assert build_llm().model == load_profile().model
