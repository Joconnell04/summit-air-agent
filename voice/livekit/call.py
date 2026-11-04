"""One live conversation: posts every caller and agent line to the tool server in order, speaks the
safety lines the server returns, and keeps the turn-taking settings in step with what the agent
just asked."""

import asyncio
import logging
from collections.abc import AsyncIterable

from livekit import rtc
from livekit.agents import Agent, ModelSettings, StopResponse
from livekit.agents.llm import ChatContext, ChatMessage

from prompts.assemble import server_notes

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
        # Set by the worker: sends a caller the server handed off to a person (transfer.py).
        self.handoff = None
        self._last_post: asyncio.Task | None = None

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
        notes = server_notes(data.get("hints", []))
        if notes:
            message.content.append("\n" + "\n".join(notes))
        handoff = data.get("handoff")
        if handoff and result["say"]:
            # The caller demanded a person: the server's line, then the transfer, or the stored
            # callback line when there is no number to send them to. The model doesn't answer.
            line = self.session.say(result["say"], allow_interruptions=False)
            if handoff.get("transfer") and self.handoff:
                self.handoff(line, handoff["transfer"], handoff["fallback"])
            raise StopResponse()
        safety = data.get("safety")
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

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        # The reply is synthesized while a bridge clip plays, but its first sound waits for the
        # clip to end, so the two never overlap.
        async for frame in Agent.default.tts_node(self, text, model_settings):
            await self.call.bridge.wait_clear()
            yield frame
