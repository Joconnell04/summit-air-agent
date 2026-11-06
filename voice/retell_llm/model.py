"""The model behind the custom-LLM socket: the same profile as the LiveKit worker
(config/models.yaml, `fast` by default), streamed, so the first sentence can go to Retell before
the rest of the reply is written."""

import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import anthropic

from voice.livekit.settings import Profile, load_profile


@dataclass
class Reply:
    """One finished model request: its content blocks for the history, and its tool calls."""

    content: list[dict] = field(default_factory=list)
    tool_uses: list[dict] = field(default_factory=list)
    first_token_ms: float = 0.0
    # The first tool call's name, as soon as the model starts writing it.
    tool_started: str = ""
    # Input (uncached, cache read, cache write) and output tokens, for the run's cost.
    tokens: dict[str, int] = field(default_factory=dict)


def thinking_request(profile: Profile) -> dict:
    """As voice/livekit/brain.py: thinking left on put 1 to 2 s before the first word."""
    if profile.thinking:
        return {"type": "adaptive"}
    return {"type": profile.thinking_off}


class Brain:
    def __init__(self, profile: Profile | None = None, client=None):
        self.profile = profile or load_profile()
        if self.profile.provider != "anthropic":
            raise ValueError(f"the custom-LLM socket runs Anthropic profiles only, not {self.profile.name}")
        self.client = client or anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    async def stream(
        self, system: str, messages: list[dict], tools: list[dict], reply: Reply
    ) -> AsyncIterator[str]:
        """Yields text as it arrives and fills `reply` when the request ends. Leaving the loop
        early closes the request."""
        params = {
            "model": self.profile.model,
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "tools": tools,
            "thinking": thinking_request(self.profile),
            # Caches the instructions, tools and history so each turn pays for its new lines only.
            "cache_control": {"type": "ephemeral"},
        }
        started = time.perf_counter()
        async with self.client.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and not reply.first_token_ms:
                    reply.first_token_ms = (time.perf_counter() - started) * 1000
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield event.delta.text
                if (event.type == "content_block_start" and event.content_block.type == "tool_use"
                        and not reply.tool_started):
                    reply.tool_started = event.content_block.name
                    # Nothing to say, but the caller of this stream learns a tool is coming.
                    yield ""
            message = await stream.get_final_message()
        reply.content = [b.model_dump(exclude_none=True) for b in message.content]
        usage = message.usage
        reply.tokens = {
            "input": usage.input_tokens,
            "cache_read": usage.cache_read_input_tokens or 0,
            "cache_write": usage.cache_creation_input_tokens or 0,
            "output": usage.output_tokens,
        }
        reply.tool_uses = [
            {"id": b.id, "name": b.name, "input": b.input}
            for b in message.content
            if b.type == "tool_use"
        ]
