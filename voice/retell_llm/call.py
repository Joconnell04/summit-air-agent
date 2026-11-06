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
import re
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
# Server-led, a tool still running after this long gets one of the tenant's holding lines, so the
# caller isn't left in silence and Retell doesn't drop the socket (a 4.2 s find_slots on a busy box
# ended call_9cae55b8f0de04239f783dacecd with error_llm_websocket_runtime).
HOLD_AFTER_S = 1.5
# Server-led, the look-ups whose result the caller would otherwise wait for in silence: the moment
# the model starts calling one, the caller hears a short acknowledgment from code, so the reply
# starts at the model's first token rather than after the tool and a second model request.
ACK_TOOLS = (
    "find_address", "check_service_area", "find_slots", "read_back", "find_customer",
    "get_customer_history", "lookup_hvac_info",
)
ACKS = ("Okay.", "Got it.")
# Turn results whose text is spoken as the server wrote it, before the model answers.
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
CONNECTED = "(The call has connected.)"

Send = Callable[[dict], Awaitable[None]]


def compact(result: dict) -> str:
    """What the model reads: the envelope, without empty fields (voice/livekit/tools.py)."""
    return json.dumps({k: v for k, v in result.items() if v not in (None, "", {})}, default=str)


def as_heard(envelope: dict) -> dict:
    """Server-led: the result as the model sees it once its sentence was spoken as written. The
    sentence moves out of `say`, so the model knows the caller heard it and doesn't say it again."""
    said = envelope.get("say") or ""
    return {**envelope, "say": "", "said_to_caller": said}


def ends_turn(envelope: dict) -> bool:
    """Server-led: whether the result's sentence is the whole turn. A refusal's sentence and a
    question are (the flow's `next` says to wait for the caller); a statement that went through
    ("someone will call you by 8 PM") leaves the model to carry on as `next` says."""
    if not envelope.get("ok"):
        return True
    data = envelope.get("data") or {}
    # An offer waits for the caller's pick, whether or not its sentence ends in a question ("Today
    # I have 11:30 to 12 or 12 to 12:30.").
    return bool(data.get("offer") or data.get("windows")) or (
        (envelope.get("say") or "").rstrip().endswith("?")
    )


NEXT_STEP = re.compile(r"^\s*(next|now|first|then),\s+", re.IGNORECASE)


def unnarrated(sentence: str) -> str:
    """Server-led: a sentence the model opens with its step ("Next, could you spell the street?")
    loses the opener; the flow's steps are the server's to name, never said aloud."""
    rest = NEXT_STEP.sub("", sentence, count=1)
    return rest[:1].upper() + rest[1:] if rest != sentence else sentence


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
        server_led: bool = False,
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
        # The server-led flow (app/flow.py, SERVER_LED=1 on the tool server): every sentence a
        # tool result carries is spoken here as written, and the model writes only the rest.
        self.server_led = server_led
        # The tenant's holding lines without the ones that narrate ("Let me pull that up."), in turn.
        self.holding = [h for h in lines.get("holding") or [] if not h.lower().startswith("let me")]
        self.held = 0
        self.acks = 0
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
        self.tokens: dict[str, int] = {}

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
            # Server-led, the latest finished sentence waits for the next one: the sentence just
            # before a tool call ("I'm booking that now.") is the one that narrates, and it is
            # dropped when the server then speaks.
            tail = ""
            reply = Reply()
            async for delta in self.brain.stream(self.system, self.messages, self.tools, reply):
                if out.stale():
                    break
                pending += delta
                *done, pending = SENTENCE_END.split(pending)
                if self.server_led and reply.tool_started in ACK_TOOLS and not out.said:
                    await out.say(ACKS[self.acks % len(ACKS)])
                    self.acks += 1
                if holding:
                    held += done
                    continue
                for sentence in done:
                    if self.server_led:
                        sentence, tail = tail, unnarrated(sentence)
                    if sentence and (said := self.reply.sentence(sentence)):
                        kept.append(said)
                        await out.say(said)
            for kind, count in reply.tokens.items():
                self.tokens[kind] = self.tokens.get(kind, 0) + count
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
                ends = [tail, unnarrated(pending) if self.server_led else pending]
                ends = [s for s in ends if s.strip()]
                last = " ".join(k for s in ends if (k := self.reply.sentence(s)))
            # Server-led, the text that comes with a tool call is never said: it is the lead-in
            # ("Got it, one sec.", "Confirming the address now.") or a question the result answers.
            # The model's next request writes the reply when the server has nothing to say.
            deferred = self.server_led and bool(reply.tool_uses)
            if last and not deferred:
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
            envelope = await self._tool(out, name, dict(use["input"]))
            self.reply.tool_done(name, envelope)
            data = envelope.get("data") or {}
            ending = name == "end_call" and envelope.get("ok")
            parts = data.get("parts") if envelope.get("ok") else None
            line = spoken_by_server(name, envelope)
            say = envelope.get("say") or ""
            if parts:
                # A read-back the caller asked to hear slower. Retell has no per-sentence speed,
                # so the parts are said at the call's one speed.
                await out.say(" ".join(p["text"] for p in parts), fixed=True)
                finished = True
            elif self.server_led and say:
                # Interruptible, so the caller can correct a read-back; a safety refusal is not.
                await out.say(say, fixed=envelope.get("refused") == "gate_safety")
                if line or ends_turn(envelope):
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
            spoken = self.server_led and say and not parts
            seen = as_heard(envelope) if spoken else for_model(envelope)
            results.append({"type": "tool_result", "tool_use_id": use["id"],
                            "content": compact(seen)})
        return results, finished

    async def _tool(self, out: Response, name: str, args: dict) -> dict:
        """One tool call. Server-led, one holding line if it runs long; the call itself is never
        cut short by a newer turn (writes are idempotent)."""
        task = asyncio.ensure_future(self.server.tool(name, args, self.writes.get(name, False)))
        if not self.server_led or not self.holding:
            return await task
        try:
            return await asyncio.wait_for(asyncio.shield(task), HOLD_AFTER_S)
        except TimeoutError:
            await out.say(self.holding[self.held % len(self.holding)])
            self.held += 1
            return await task

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
        log.info("%s ended (%s), model tokens %s", self.server.call_id, self.ended,
                 json.dumps(self.tokens, sort_keys=True))
