"""One call on Retell's custom-LLM socket. Retell hears the caller, takes turns and speaks; this
class writes every reply the way the LiveKit worker does (voice/livekit/call.py and tools.py):

- each caller line goes to the tool server before the model sees it, and the safety lines, the
  card stop and the other server lines are sent as the server wrote them, uninterruptible;
- the model's text is passed a sentence at a time through the reply rule
  (prompts/assemble.py SpokenReply), held for the whole reply while the rule says so;
- tool results the server speaks (the read-back, the sign-off, a booking's confirmation) are sent
  word for word and end the turn;
- the call ends only when the server's end_call says so, and goes to a person only when the
  server's handoff or transfer says so.

Agent lines are posted to the tool server from Retell's own transcript, so the record holds what
the caller heard, cut-offs included, not what was sent."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

from prompts.assemble import (
    SENTENCE_END,
    SpokenReply,
    for_model,
    server_notes,
    spoken_by_server,
    spoken_first,
)
from voice.livekit.server import UNREACHABLE, ToolServer

from .model import Brain, Reply

log = logging.getLogger("voice.retell_llm")

MAX_HOPS = 6
# Turn results whose text is spoken as the server wrote it, before the model answers.
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
CONNECTED = "(The call has connected.)"

Send = Callable[[dict], Awaitable[None]]


def compact(result: dict) -> str:
    """What the model reads: the envelope, without empty fields (voice/livekit/tools.py)."""
    return json.dumps({k: v for k, v in result.items() if v not in (None, "", {})}, default=str)


def caller_text(transcript: list[dict]) -> str:
    """What the caller has said since the agent last spoke."""
    lines = []
    for entry in reversed(transcript):
        if entry.get("role") == "agent":
            break
        lines.append((entry.get("content") or "").strip())
    return " ".join(line for line in reversed(lines) if line)


def heard_after(transcript: list[dict], index: int) -> bool:
    """Whether the agent said anything after the first `index` entries."""
    return any(e.get("role") == "agent" and (e.get("content") or "").strip()
               for e in transcript[index:])


def reply_ms(transcript: list[dict], index: int) -> float | None:
    """The caller's last word to the agent's first, from Retell's word timings, in ms."""
    words = transcript[index].get("words") or []
    before = transcript[index - 1].get("words") or [] if index > 0 else []
    if not words or not before or transcript[index - 1].get("role") != "user":
        return None
    return round((words[0]["start"] - before[-1]["end"]) * 1000, 1)


class Response:
    """The chunks of one response id. Nothing is sent once a newer request has replaced it."""

    def __init__(self, call: "LlmCall", response_id: int):
        self.call = call
        self.id = response_id
        self.said = False
        self.closed = False

    def stale(self) -> bool:
        return self.id != self.call.current_id

    async def say(self, text: str, fixed: bool = False) -> None:
        text = " ".join(text.split())
        if not text or self.stale():
            return
        if self.said:
            text = " " + text
        self.said = True
        event = {"response_type": "response", "response_id": self.id, "content": text,
                 "content_complete": False}
        if fixed:
            event["no_interruption_allowed"] = True
        await self.call.send(event)

    async def done(self, end_call: bool = False, transfer_number: str | None = None) -> None:
        """Always sent, even for an empty reply: an open response leaves Retell waiting."""
        if self.closed or self.stale():
            return
        self.closed = True
        event = {"response_type": "response", "response_id": self.id, "content": "",
                 "content_complete": True}
        if end_call:
            event["end_call"] = True
        if transfer_number:
            event["transfer_number"] = transfer_number
        await self.call.send(event)


class LlmCall:
    def __init__(
        self,
        server: ToolServer,
        brain: Brain,
        send: Send,
        instructions: str,
        specs: list[dict],
        lines: dict,
        phone: bool,
        dead_air_checks: int = 4,
    ):
        self.server = server
        self.brain = brain
        self.send = send
        self.system = instructions
        self.tools = [
            {"name": s["name"], "description": s["description"], "input_schema": s["parameters"]}
            for s in specs
        ]
        self.writes = {s["name"]: bool(s.get("write")) for s in specs}
        self.lines = lines
        # A web call (the audio rig) has no line to transfer; only a phone call can go to a person.
        self.phone = phone
        self.dead_air_checks = dead_air_checks
        self.reminders = 0
        self.messages: list[dict] = []
        self.reply = SpokenReply()
        self.current_id = -1
        self.turn_lock = asyncio.Lock()
        self.post_lock = asyncio.Lock()
        # Transcript entries already posted to the tool server as agent lines.
        self.posted = 0
        self.transcript: list[dict] = []
        # Where the last turn started, in the history and in Retell's transcript, so a reply the
        # caller never heard (they kept talking) can be taken back out of the history.
        self.last_turn: tuple[int, int] | None = None
        # Stage timings of each reply, attached to the agent line Retell reports for it.
        self.stages: list[dict] = []
        self.ended = "caller_hangup"
        self.handoff_id: int | None = None
        self.transfer_id: int | None = None
        self.transfer_number: str | None = None
        self.began = 0.0
        self.closed = False

    async def begin(self, greeting: str) -> None:
        """The first line, already built by whoever started the call on the tool server."""
        self.messages += [{"role": "user", "content": CONNECTED},
                          {"role": "assistant", "content": greeting}]
        await self.send({"response_type": "response", "response_id": 0, "content": greeting,
                         "content_complete": True})

    def request(self, response_id: int) -> None:
        """A newer request replaces every earlier one at once, before its turn starts."""
        self.current_id = max(self.current_id, response_id)

    async def seen(self, transcript: list[dict]) -> None:
        """Retell's latest transcript: agent lines that are finished go to the tool server."""
        self.transcript = transcript
        await self._post_heard(len(transcript) - 1)

    async def _post_heard(self, upto: int) -> None:
        async with self.post_lock:
            transcript = self.transcript
            while self.posted < min(upto, len(transcript)):
                index = self.posted
                self.posted += 1
                entry = transcript[index]
                text = (entry.get("content") or "").strip()
                if entry.get("role") != "agent" or not text:
                    continue
                timings = self.stages.pop(0) if self.stages and index > 0 else {}
                ms = reply_ms(transcript, index)
                if ms is not None:
                    timings = {**timings, "reply_ms": ms}
                # Shielded: a cancelled turn must not leave a line half posted.
                await asyncio.shield(self.server.turn("agent", text, timings))

    async def respond(self, response_id: int, transcript: list[dict]) -> None:
        async with self.turn_lock:
            out = Response(self, response_id)
            if out.stale():
                return
            began = self.began = time.perf_counter()
            self.transcript = transcript
            if self.ended in ("agent_end", "stepped_away"):
                # The goodbye was cut off and Retell dropped the hang-up: the caller is still
                # talking, so the model answers and asks the server again before ending.
                self.ended = "caller_hangup"
            self._forget_unheard(transcript)
            text = caller_text(transcript)
            # Every finished agent line goes before the caller's, as the server numbers turns.
            await self._post_heard(len(transcript))
            if not text:
                await out.done()
                return
            self.last_turn = (len(self.messages), len(transcript))
            try:
                await self._caller_turn(out, text, began)
            except asyncio.CancelledError:
                # A newer request replaced this turn (the socket cancels it at once).
                self._settle()
                raise
            finally:
                await out.done(end_call=self.ended in ("agent_end", "stepped_away", "error"),
                               transfer_number=self.transfer_number)

    def _settle(self) -> None:
        """After a cancelled turn every tool call in the history still needs its result, or the
        next request is refused: calls without one come out, with the text kept."""
        last = self.messages[-1] if self.messages else None
        if not last or last["role"] != "assistant" or not isinstance(last["content"], list):
            return
        kept = [b for b in last["content"] if b.get("type") != "tool_use"]
        if len(kept) == len(last["content"]):
            return
        if kept:
            last["content"] = kept
        else:
            self.messages.pop()

    def _forget_unheard(self, transcript: list[dict]) -> None:
        """The last reply was never heard if Retell's transcript has no agent line after the
        caller's: the caller kept talking and Retell dropped it. Its turn comes out of the model's
        history, and the caller's whole line is answered now. Writes it made stand; they are
        idempotent, and the server's record only ever holds what was heard."""
        if self.last_turn is None:
            return
        mark, at = self.last_turn
        if not heard_after(transcript, at):
            del self.messages[mark:]
            self.stages.clear()
        self.last_turn = None

    async def _caller_turn(self, out: Response, text: str, began: float) -> None:
        result = await asyncio.shield(self.server.turn("caller", text, {}))
        server_ms = round((time.perf_counter() - began) * 1000, 1)
        if result.get("refused") == UNREACHABLE:
            # No server, no safety check: the tenant's server-down line, then the call ends.
            await self._from_server(out, text, self.lines["server_down"])
            self.ended = "error"
            return
        data = result.get("data") or {}
        notes = server_notes(data.get("hints", []), data.get("known"))
        handoff = data.get("handoff")
        if handoff and result.get("say"):
            await self._hand_off(out, text, result["say"], handoff)
            return
        safety = data.get("safety")
        self.reply.heard(safety, text, data.get("hints"))
        if safety and safety.get("action") in SPOKEN_ACTIONS and safety.get("text"):
            await self._from_server(out, text, safety["text"])
            return
        spoken = data.get("spoken")
        if spoken and spoken.get("text"):
            content = "\n".join([text, *notes])
            await self._from_server(out, content, spoken["text"])
            return
        if (data.get("hold") or {}).get("state") == "aside":
            # Said on hold to someone else: the model says nothing.
            self.messages.append({"role": "user", "content": text})
            return
        if (data.get("recording") or {}).get("action") == "stop" and data["recording"].get("text"):
            await out.say(data["recording"]["text"])
        self.messages.append({"role": "user", "content": "\n".join([text, *notes])})
        await self._think(out, began, server_ms)

    async def _from_server(self, out: Response, content: str, line: str) -> None:
        self.messages += [{"role": "user", "content": content},
                          {"role": "assistant", "content": line}]
        await out.say(line, fixed=True)
        self.stages.append({"spoken_from_code_ms": round((time.perf_counter() - self.began) * 1000, 1)})

    async def _hand_off(self, out: Response, text: str, say: str, handoff: dict) -> None:
        """The caller demanded a person (voice/livekit/call.py). With nothing to send the call
        through (a web call), the server is told at once and the stored callback line is said."""
        plan = handoff.get("transfer")
        if plan and not self.phone:
            done = await self.server.handoff_done(plan["id"], "failed")
            await self._from_server(out, text, done.get("say") or handoff["fallback"])
            return
        await self._from_server(out, text, say)
        if plan:
            self.handoff_id = plan["id"]
            self.transfer_number = plan["phone"]
            self.ended = "transfer"

    async def _think(self, out: Response, began: float, server_ms: float) -> None:
        stages: dict[str, float] = {"server_ms": server_ms}
        tool_ms = 0.0
        for hop in range(MAX_HOPS):
            self.reply.start_reply()
            holding = self.reply.holding()
            pending, held, kept = "", [], []
            reply = Reply()
            async for delta in self.brain.stream(self.system, self.messages, self.tools, reply):
                if out.stale():
                    break
                pending += delta
                *done, pending = SENTENCE_END.split(pending)
                if holding:
                    held += done
                    continue
                for sentence in done:
                    if said := self.reply.sentence(sentence):
                        kept.append(said)
                        await out.say(said)
            if hop == 0:
                stages["llm_first_token_ms"] = round(reply.first_token_ms, 1)
            if out.stale():
                # Replaced mid-stream: keep what was sent, as a line the caller may have heard.
                if kept:
                    self.messages.append({"role": "assistant", "content": " ".join(kept)})
                return
            calls = [(u["name"], u["input"]) for u in reply.tool_uses]
            if holding:
                last = self.reply.text(" ".join([*held, pending]), calls)
            else:
                last = self.reply.sentence(pending) if pending.strip() else ""
            if last:
                kept.append(last)
                await out.say(last)
            # The history keeps what the caller heard of the model's text, as LiveKit's does.
            content = [b for b in reply.content if b.get("type") != "text"]
            if kept:
                content.insert(0, {"type": "text", "text": " ".join(kept)})
            if content:
                self.messages.append({"role": "assistant", "content": content})
            if not reply.tool_uses:
                break
            called = time.perf_counter()
            results, finished = await self._run_tools(out, reply.tool_uses)
            tool_ms += (time.perf_counter() - called) * 1000
            self.messages.append({"role": "user", "content": results})
            if finished or out.stale():
                break
        if tool_ms:
            stages["tool_ms"] = round(tool_ms, 1)
        self.stages.append(stages)

    async def _run_tools(self, out: Response, uses: list[dict]) -> tuple[list[dict], bool]:
        """Each call goes to the tool server; the server's own lines are said as written. True
        when the turn is over: a server line was said, the call is ending or going to a person."""
        results, finished = [], False
        for use in uses:
            name = use["name"]
            envelope = await self.server.tool(name, dict(use["input"]), self.writes.get(name, False))
            self.reply.tool_done(name, envelope)
            data = envelope.get("data") or {}
            ending = name == "end_call" and envelope.get("ok")
            parts = data.get("parts") if envelope.get("ok") else None
            line = spoken_by_server(name, envelope)
            if parts:
                # A read-back the caller asked to hear slower. Retell has no per-sentence speed,
                # so the parts are said at the call's one speed.
                await out.say(" ".join(p["text"] for p in parts), fixed=True)
                finished = True
            elif line:
                await out.say(line, fixed=True)
                finished = True
            elif spoken_first(envelope):
                await out.say(spoken_first(envelope), fixed=True)
            if ending and data.get("hang_up"):
                if not out.said and not data.get("signed_off"):
                    await out.say(self.lines["goodbye"], fixed=True)
                self.ended = "agent_end"
                finished = True
            person = name == "flag_person" and envelope.get("ok") and self.phone
            if person and await self._put_through(out, envelope):
                finished = True
            if ending:
                finished = True
            results.append({"type": "tool_result", "tool_use_id": use["id"],
                            "content": compact(for_model(envelope))})
        return results, finished

    async def _put_through(self, out: Response, flagged: dict) -> bool:
        """After a person flag the server may send the caller to a person now (app/transfer.py).
        Retell's own cold transfer does the dialing, after the server's line."""
        data = flagged["data"]
        asked = await self.server.transfer(data["flag_id"], data.get("callback_id"))
        plan = (asked.get("data") or {}).get("transfer") if asked.get("ok") else None
        if not plan:
            return False
        await out.say(asked.get("say") or "", fixed=True)
        self.transfer_id = plan["id"]
        self.transfer_number = plan["phone"]
        self.ended = "transfer"
        return True

    async def remind(self, response_id: int, transcript: list[dict]) -> None:
        """Dead air (DECISIONS.md 081): Retell asks after each stretch of the caller's silence;
        the tenant's check-in line until the last, then its goodbye and the call ends."""
        async with self.turn_lock:
            out = Response(self, response_id)
            if out.stale():
                return
            self.transcript = transcript
            await self._post_heard(len(transcript))
            self.reminders += 1
            if self.reminders < self.dead_air_checks:
                await out.say(self.lines["still_here"])
                await out.done()
                return
            await out.say(self.lines["stepped_away"], fixed=True)
            self.ended = "stepped_away"
            await out.done(end_call=True)

    async def close(self) -> None:
        """The socket has gone for good: the last lines, the transfer's outcome, the call's end."""
        if self.closed:
            return
        self.closed = True
        await self._post_heard(len(self.transcript))
        if self.handoff_id is not None:
            await self.server.handoff_done(self.handoff_id, "handed_off")
        if self.transfer_id is not None:
            await self.server.transfer_done(self.transfer_id, "handed_off")
        await self.server.end(self.ended)
        await self.server.aclose()
