"""One live conversation: posts every caller and agent line to the tool server in order, speaks the
safety lines the server returns, keeps the turn-taking settings in step with what the agent just
asked, and speaks at the pace the server sets (app/pace.py)."""

import asyncio
import json
import logging
import time
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
# What Cartesia sonic-3 accepts for `speed` (SOURCE: the LiveKit Cartesia plugin's docs). The server
# keeps its paces inside it too; this guards the voice against a bad value either way.
SPEED_MIN, SPEED_MAX = 0.6, 2.0


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
        # The end of the caller's last line, until the first agent line after it is timed.
        self.caller_stopped_at: float | None = None
        # How long that line's trip to the tool server took, before the model could answer.
        self.server_ms: float | None = None
        self.hang_up_task: asyncio.Task | None = None
        # Set by the worker: says the tenant's server-down line (which carries the 911 line) and
        # ends the call. Without the server there is no safety check, so the call can't go on.
        self.server_down = None
        # Set by the worker: sends a caller the server handed off to a person (transfer.py).
        self.handoff = None
        # Set by the worker: stops the call's recording when the server says the caller objected.
        self.stop_recording = None
        self._last_post: asyncio.Task | None = None
        # What of the model's replies the caller hears (prompts/assemble.py SpokenReply).
        self.reply = SpokenReply()
        # The speaking rate the server set for this call; None leaves the voice at its default.
        self.speed: float | None = None
        # Text the worker speaks at its own speed (the parts of a slower read-back), by its text.
        self.planned: dict[str, float] = {}
        # What each speech was synthesized at, by its first words, so its line reports the speed.
        self._spoken: list[tuple[str, float]] = []

    def apply_pace(self, data: dict) -> None:
        """Takes the speed from a server result. The worker never decides a pace itself."""
        pace = data.get("pace")
        if pace and pace.get("speed"):
            self.speed = min(SPEED_MAX, max(SPEED_MIN, float(pace["speed"])))

    def speed_for(self, first_text: str) -> float | None:
        """The speed for a speech that starts with this text: a planned part's own speed, else the
        call's. Recorded so the agent line can say what it was spoken at."""
        speed = self.planned.pop(first_text.strip(), self.speed)
        if speed is not None:
            self._spoken.append((first_text.strip(), speed))
        return speed

    def drop_unspoken(self) -> None:
        """Parts of a slower read-back that were never synthesized (the caller cut in first) are
        logged and forgotten, so a later line with the same words isn't spoken at their speed."""
        if self.planned:
            log.warning("read-back parts never spoken: %s", list(self.planned))
            self.planned.clear()

    def spoken_speed(self, text: str) -> float | None:
        """The speed a finished agent line was synthesized at."""
        for i, (start, speed) in enumerate(self._spoken):
            if text.strip().startswith(start):
                del self._spoken[i]
                return speed
        return self.speed

    def say_parts(self, parts: list[dict]) -> object:
        """Says the server's sentence in its parts, each at the speed the server gave it (the
        address alone slower), and returns the last part's speech handle."""
        self.drop_unspoken()
        handle = None
        for part in parts:
            speed = min(SPEED_MAX, max(SPEED_MIN, float(part["speed"])))
            self.planned[part["text"].strip()] = speed
            handle = self.session.say(part["text"])
        return handle

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
        self.drop_unspoken()
        self.bridge.caller_turn()
        self.caller_stopped_at = message.metrics.get("stopped_speaking_at")
        began = time.perf_counter()
        result = await self.post("caller", text, caller_timings(message.metrics))
        self.server_ms = round((time.perf_counter() - began) * 1000, 1)
        if result["refused"] == UNREACHABLE:
            self.server_down()
            raise StopResponse()
        data = result.get("data") or {}
        self.apply_pace(data)
        # The caller objected to being recorded: stop at once.
        stop = (data.get("recording") or {}).get("action") == "stop"
        if stop and self.stop_recording:
            asyncio.create_task(self.stop_recording())
        notes = server_notes(data.get("hints", []), data.get("known"))
        if notes:
            message.content.append("\n" + "\n".join(notes))
        handoff = data.get("handoff")
        if handoff and result["say"]:
            # The caller demanded a person: the server's line, then the transfer, or the stored
            # callback line when there is no number to send them to. The model doesn't answer.
            plan = handoff.get("transfer")
            if plan and self.handoff is None:
                # Nothing here can send the call, so the caller gets the stored callback line,
                # never a handoff line with no transfer after it.
                await self.server.handoff_done(plan["id"], "failed")
                self.session.say(handoff["fallback"], allow_interruptions=False)
                raise StopResponse()
            line = self.session.say(result["say"], allow_interruptions=False)
            if plan:
                self.handoff(line, plan, handoff["fallback"])
            raise StopResponse()
        safety = data.get("safety")
        self.reply.heard(safety, text)
        if safety and safety["action"] in SPOKEN_ACTIONS and safety.get("text"):
            # Spoken from the server's text, never generated, and the caller can't cut it short.
            self.session.say(safety["text"], allow_interruptions=False)
            raise StopResponse()
        if stop and data["recording"].get("text"):
            # The server's line, said before the model answers the rest of what the caller said.
            self.session.say(data["recording"]["text"])

    def agent_line(self, message: ChatMessage) -> None:
        """Called when an agent line has been spoken (or cut off)."""
        text = message.text_content or ""
        if not text.strip():
            return
        tool_ms, bridge = self.tool_log.take()
        timings = agent_timings(
            message.metrics, self.caller_stopped_at, tool_ms, bridge, self.llm_log.take(),
            clip_started_at=self.bridge.clip_started_at,
        )
        if self.caller_stopped_at is not None and self.server_ms is not None:
            timings["server_ms"] = self.server_ms
        # Only the first line after the caller's is the reply the caller waited for; a line after
        # a tool call in the same turn is timed from it no longer.
        self.caller_stopped_at = None
        self.server_ms = None
        speed = self.spoken_speed(text)
        if speed is not None:
            timings["speed"] = speed
        self.post("agent", text, timings)
        mode = "digits" if asked_for_digits(text) else "normal"
        # A copy: LiveKit writes into the dict it is given, which would overwrite the normal wait
        # with the digits wait for the rest of the call.
        self.session.update_options(endpointing_opts=dict(self.endpointing[mode]))

    async def flush(self) -> None:
        """Wait for agent lines still being posted, before the call is closed."""
        self.drop_unspoken()
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
        # The speed is set before the voice stream opens, because Cartesia's stream copies its
        # options when it starts: each speech is spoken at one speed.
        chunks = aiter(text)
        first = ""
        async for chunk in chunks:
            first += chunk
            if first.strip():
                break
        speed = self.call.speed_for(first)
        if speed is not None:
            self.session.tts.update_options(speed=speed)

        async def whole() -> AsyncIterable[str]:
            yield first
            async for chunk in chunks:
                yield chunk

        # The reply is synthesized while a bridge clip plays, but its first sound waits for the
        # clip to end, so the two never overlap.
        async for frame in Agent.default.tts_node(self, whole(), model_settings):
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
