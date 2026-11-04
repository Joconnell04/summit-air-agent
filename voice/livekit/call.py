"""One live conversation: posts every caller and agent line to the tool server in order, speaks the
safety lines the server returns, and keeps the turn-taking settings in step with what the agent
just asked."""

import asyncio
import json
import logging
from collections.abc import AsyncIterable

from livekit import rtc
from livekit.agents import Agent, ModelSettings, StopResponse, llm
from livekit.agents.llm import ChatContext, ChatMessage

from prompts.assemble import SENTENCE_END, SpokenReply, server_notes

from .bridge import Bridge
from .server import UNREACHABLE, ToolServer
from .tools import ToolLog
from .turns import LlmLog, agent_timings, asked_for_digits, caller_timings

log = logging.getLogger("voice.call")

# Turn results whose text the worker speaks itself, word for word, before the model answers.
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")


class Call:
    """What the worker knows about one call, shared by the agent and the session's events."""

    def __init__(
        self,
        server: ToolServer,
        bridge: Bridge,
        tool_log: ToolLog,
        endpointing: dict,
        llm_log: LlmLog | None = None,
    ):
        self.server = server
        self.bridge = bridge
        self.tool_log = tool_log
        self.llm_log = llm_log or LlmLog()
        self.endpointing = endpointing
        self.session = None
        self.caller_stopped_at: float | None = None
        self.hang_up_task: asyncio.Task | None = None
        # Set by the worker: says the tenant's server-down line (which carries the 911 line) and
        # ends the call. Without the server there is no safety check, so the call can't go on.
        self.server_down = None
        self._last_post: asyncio.Task | None = None
        # What of the model's replies the caller hears (prompts/assemble.py SpokenReply).
        self.reply = SpokenReply()

    def post(self, speaker: str, text: str, timings: dict[str, float]) -> asyncio.Task:
        """Queue a line for the server. Lines go one at a time, in the order they happened,
        because the server numbers turns as they arrive."""
        previous = self._last_post

        async def send() -> dict:
            if previous:
                await asyncio.gather(previous, return_exceptions=True)
            return await self.server.turn(speaker, text, timings)

        self._last_post = asyncio.create_task(send())
        return self._last_post

    async def caller_line(self, message: ChatMessage) -> None:
        """Runs before the model answers. Raises StopResponse when the worker spoke for it."""
        text = message.text_content or ""
        if not text.strip():
            return
        self.bridge.caller_turn()
        self.caller_stopped_at = message.metrics.get("stopped_speaking_at")
        result = await self.post("caller", text, caller_timings(message.metrics))
        if result["refused"] == UNREACHABLE:
            self.server_down()
            raise StopResponse()
        data = result.get("data") or {}
        notes = server_notes(data.get("hints", []), data.get("known"))
        if notes:
            message.content.append("\n" + "\n".join(notes))
        safety = data.get("safety")
        self.reply.heard(safety, text)
        if safety and safety["action"] in SPOKEN_ACTIONS and safety.get("text"):
            # Spoken from the server's text, never generated, and the caller can't cut it short.
            self.session.say(safety["text"], allow_interruptions=False)
            raise StopResponse()

    def agent_line(self, message: ChatMessage) -> None:
        """Called when an agent line has been spoken (or cut off)."""
        text = message.text_content or ""
        if not text.strip():
            return
        tool_ms, bridge = self.tool_log.take()
        timings = agent_timings(
            message.metrics, self.caller_stopped_at, tool_ms, bridge, self.llm_log.take()
        )
        self.post("agent", text, timings)
        mode = "digits" if asked_for_digits(text) else "normal"
        self.session.update_options(endpointing_opts=self.endpointing[mode])

    async def flush(self) -> None:
        """Wait for agent lines still being posted, before the call is closed."""
        if self._last_post:
            await asyncio.gather(self._last_post, return_exceptions=True)


class SummitAgent(Agent):
    def __init__(self, call: Call, instructions: str, tools: list):
        super().__init__(instructions=instructions, tools=tools)
        self.call = call

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage):
        await self.call.caller_line(new_message)

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterable[llm.ChatChunk | str]:
        """The model's reply, a sentence at a time through the reply rule (prompts/assemble.py
        SpokenReply), so a read-back announcement or a restated ZIP or number is never spoken.
        While the rule is holding, the text waits for the end of the reply and is dropped if the
        reply calls a tool whose result the worker speaks itself."""
        reply = self.call.reply
        reply.start_reply()
        holding = reply.holding()
        pending, held = "", []
        calls: list[tuple[str, dict]] = []
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            text = chunk if isinstance(chunk, str) else None
            if isinstance(chunk, llm.ChatChunk) and chunk.delta:
                text = chunk.delta.content
                calls += [(c.name, _arguments(c.arguments)) for c in chunk.delta.tool_calls]
                if text or chunk.delta.tool_calls:
                    chunk = chunk.model_copy(
                        update={"delta": chunk.delta.model_copy(update={"content": None})}
                    )
            if text:
                pending += text
                *done, pending = split_sentences(pending)
                if holding:
                    held += done
                else:
                    for sentence in done:
                        if kept := reply.sentence(sentence):
                            yield kept + " "
            if not isinstance(chunk, str):
                yield chunk
        if holding:
            kept = reply.text(" ".join([*held, pending]), calls)
        else:
            kept = reply.sentence(pending) if pending.strip() else ""
        if kept:
            yield kept

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        # The reply is synthesized while a bridge clip plays, but its first sound waits for the
        # clip to end, so the two never overlap.
        async for frame in Agent.default.tts_node(self, text, model_settings):
            await self.call.bridge.wait_clear()
            yield frame


def split_sentences(text: str) -> list[str]:
    """Complete sentences, then what is left after the last one (possibly "")."""
    return SENTENCE_END.split(text)


def _arguments(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
