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
from datetime import datetime
from zoneinfo import ZoneInfo

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
# Server-led, the hold phrase covers the wait on every tool but the goodbye.
NO_HOLD_TOOLS = ("end_call",)
# Server-led, a sentence of the model's that narrates its own step ("I'm booking that now.",
# "Confirming the address and phone now.", "Let me check.") is never said.
NARRATES = re.compile(
    r"^\W*((okay|ok|alright|got it|sure),?\s+)?(let me\b|i'?ll (check|look|book|confirm|pull|get|"
    r"find|search|see)\b|(i'?m |i am )?(booking|checking|confirming|looking|pulling|getting|"
    r"searching|finding)\b)",
    re.IGNORECASE,
)
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


# Thanking the caller for a wait (Jackson's change set 24: never).
THANKS_FOR_WAITING = re.compile(
    r"\bthanks?( you)?( so much)? for (waiting|holding|your patience|holding on|bearing with)",
    re.IGNORECASE,
)
# A goodbye of the model's own; the socket says the closing itself (Jackson's change set 24).
GOODBYE = re.compile(
    r"\b(thanks?|thank you)( so much)? for calling\b|\bhave a (great|good|nice|wonderful|lovely)\b"
    r"|\btake care\b|\bgoodbye\b",
    re.IGNORECASE,
)
WEEKDAY_DATE = re.compile(
    r"\b(today|tomorrow),?\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday),?"
    r"(\s+(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2}(st|nd|rd|th)?)?",
    re.IGNORECASE,
)
# A name said to the caller at the end of a sentence: "You're welcome, Evelyn."
VOCATIVE = re.compile(r",\s+([A-Z][a-z]+)(?=[.!?]?$)")
# The server's own short connector at the start of a sentence (app/flow.py ACKS).
CONNECTOR = re.compile(r"^(Got it|Okay, thanks|Perfect|Okay)\.\s+")


def without_vocative(sentence: str, known: set[str]) -> str:
    """Server-led: a caller's name said at the end of a sentence comes out (Jackson: never say
    the caller's name)."""
    match = VOCATIVE.search(sentence.strip())
    if not match or match[1].lower() not in known:
        return sentence
    return sentence.strip()[: match.start()] + sentence.strip()[match.end(1):]


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def plain_day(sentence: str) -> str:
    """Today and tomorrow are said alone, never with the weekday or date (change set 19)."""
    return WEEKDAY_DATE.sub(lambda m: m[1], sentence)


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
        # Server-led: a tool is running or being written, the wait the hold phrase covers.
        self.tool_wait = ""
        # When the first tool call of the wait went to the tool server.
        self.tool_began: float | None = None
        self.hold_ms: float | None = None

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
        hold_after_s: float = 0.7,
        hold_from: str = "caller",
        tz: str = "America/New_York",
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
        # Server-led: when a step's tool work hasn't finished this long after the caller stopped
        # talking, one hold phrase ("One sec."), the tenant's, in turn, never twice in a row
        # (config/models.yaml voice.hold_phrase_after_s).
        self.hold_after_s = hold_after_s
        # "caller": counted from the caller's turn (Jackson's ask); "tool": from the moment the
        # tool itself starts, so the model writing the call doesn't count.
        self.hold_from = hold_from
        self.hold_phrases = list(lines.get("hold_phrases") or ["One sec."])
        self.hold_stats = {"waits": 0, "fired": 0}
        # Whether the last caller turn got a hold phrase: never two turns in a row.
        self.held_last_turn = False
        self.drops: dict[str, int] = {}
        # For the closing's nicety, fit to the tenant's time of day.
        self.tz = tz
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
        watch = asyncio.create_task(self._hold_watch(out, began)) if self.server_led else None
        try:
            await self._hops(out, stages)
        finally:
            if watch is not None:
                watch.cancel()
        if out.hold_ms is not None:
            stages["hold_ms"] = out.hold_ms
        self.stages.append(stages)

    def _clean(self, sentence: str) -> tuple[str, str]:
        """Server-led: a sentence of the model's as the caller will hear it, and why it was cut,
        if it was. A question is never cut, only tidied, so the filter can't take the actual
        question away (DECISIONS 112: the custom stack's filter left half replies)."""
        tidy = plain_day(without_vocative(unnarrated(sentence), self._known_names()))
        if tidy.rstrip().endswith("?"):
            return tidy, ""
        if NARRATES.match(tidy):
            return "", "narrates"
        if THANKS_FOR_WAITING.search(tidy):
            return "", "thanks_for_waiting"
        return tidy, ""

    def _dropped(self, sentence: str, reason: str) -> None:
        """Every sentence the filter cuts is logged in full, and counted per call."""
        self.drops[reason] = self.drops.get(reason, 0) + 1
        log.info("%s: dropped (%s) %r", self.server.call_id, reason, sentence)

    def _known_names(self) -> set[str]:
        """Every word the caller said and every name on the accounts the call found."""
        return self.reply._caller_words | self.reply.names

    def _closing(self) -> str:
        """The tenant's closing plus one nicety fit to the time of day, chosen per call, never two
        (Jackson's change set 24)."""
        base = self.lines.get("closing") or self.lines["goodbye"]
        niceties = self.lines.get("closing_niceties") or {}
        part = part_of_day(datetime.now(ZoneInfo(self.tz)).hour)
        options = niceties.get(part) or []
        if not options:
            return base
        pick = options[sum(map(ord, self.server.call_id or "")) % len(options)]
        return f"{base} {pick}"

    def _wait_on(self, out: Response, tool: str) -> None:
        """A tool is being written or run: the wait the hold phrase may cover, counted once."""
        if out.tool_wait or tool in NO_HOLD_TOOLS:
            return
        out.tool_wait = tool
        self.hold_stats["waits"] += 1

    async def _hold_watch(self, out: Response, began: float) -> None:
        """One hold phrase per wait: only when a tool's work hasn't finished `hold_after_s` after
        the caller stopped talking and the caller has heard nothing yet. Logged when it fires."""
        if self.held_last_turn:
            self.held_last_turn = False
            return
        while not out.closed and not out.stale() and not out.said:
            waited = time.perf_counter() - began
            start = out.tool_began if self.hold_from == "tool" else began
            due = start is not None and time.perf_counter() - start >= self.hold_after_s
            if out.tool_wait and due:
                phrase = self.hold_phrases[self.hold_stats["fired"] % len(self.hold_phrases)]
                self.hold_stats["fired"] += 1
                out.hold_ms = round(waited * 1000, 1)
                self.held_last_turn = True
                log.info("%s: hold phrase %r at %.0f ms, waiting on %s", self.server.call_id,
                         phrase, out.hold_ms, out.tool_wait)
                await out.say(phrase)
                return
            await asyncio.sleep(0.05)

    async def _hops(self, out: Response, stages: dict) -> None:
        tool_ms = 0.0
        for hop in range(MAX_HOPS):
            self.reply.start_reply()
            holding = self.reply.holding()
            pending, held, kept = "", [], []
            # Server-led: the model's goodbyes wait for the end of the reply (the socket says the
            # closing when it ends the call), and the raw sentences, for the fallback.
            goodbyes, raw = [], []
            reply = Reply()
            async for delta in self.brain.stream(self.system, self.messages, self.tools, reply):
                if out.stale():
                    break
                pending += delta
                *done, pending = SENTENCE_END.split(pending)
                if self.server_led and reply.tool_started:
                    self._wait_on(out, reply.tool_started)
                if holding:
                    held += done
                    continue
                for sentence in done:
                    if self.server_led:
                        raw.append(sentence)
                        sentence, reason = self._clean(sentence)
                        if reason:
                            self._dropped(raw[-1], reason)
                            continue
                        if GOODBYE.search(sentence):
                            goodbyes.append(sentence)
                            continue
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
                rest = pending
                if self.server_led and pending.strip():
                    raw.append(pending)
                    rest, reason = self._clean(pending)
                    if reason:
                        self._dropped(pending, reason)
                    elif GOODBYE.search(rest):
                        goodbyes.append(rest)
                        rest = ""
                last = self.reply.sentence(rest) if rest.strip() else ""
            ending = any(name == "end_call" for name, _ in calls)
            for goodbye in goodbyes:
                if ending:
                    self._dropped(goodbye, "goodbye_with_end_call")
                elif said := self.reply.sentence(goodbye):
                    kept.append(said)
                    await out.say(said)
            if (self.server_led and not reply.tool_uses and not kept and not last and raw
                    and not out.said):
                # The filter cut the whole reply: say it as the model wrote it rather than leave
                # the caller in silence, and log that it happened.
                last = " ".join(" ".join(raw).split())
                self._dropped(last, "fallback_spoke_whole_reply")
            # Server-led, the unfinished text that comes with a tool call is never said: it is the
            # lead-in or a question the result answers. The model's next request writes the reply
            # when the server has nothing to say.
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

    async def _run_tools(self, out: Response, uses: list[dict]) -> tuple[list[dict], bool]:
        """Each call goes to the tool server; the server's own lines are said as written. True
        when the turn is over: a server line was said, the call is ending or going to a person."""
        results, finished = [], False
        for use in uses:
            name = use["name"]
            if self.server_led:
                self._wait_on(out, name)
                if out.tool_began is None and name not in NO_HOLD_TOOLS:
                    out.tool_began = time.perf_counter()
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
                if out.said:
                    # After "One sec." or the model's own "Okay, thanks.", the server's "Perfect."
                    # would stack two openers.
                    say = CONNECTOR.sub("", say, count=1)
                # Interruptible, so the caller can correct a read-back; a safety refusal is not.
                await out.say(say, fixed=envelope.get("refused") == "gate_safety")
                if line or ends_turn(envelope):
                    finished = True
            elif line:
                await out.say(line, fixed=True)
                finished = True
            elif spoken_first(envelope):
                await out.say(spoken_first(envelope), fixed=True)
            if ending and data.get("hang_up") and self.server_led and not say:
                # The closing is the socket's alone; the model's own goodbye in this reply was cut.
                await out.say(self._closing(), fixed=True)
                self.ended = "agent_end"
                finished = True
            elif ending and data.get("hang_up"):
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
        return await self.server.tool(name, args, self.writes.get(name, False))

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
            if self.server_led:
                # Jackson's check-in after about 8 seconds of silence, at most twice (the agent's
                # reminder settings); the call stays open, and Retell ends it after a long silence.
                await out.say(self.lines.get("are_you_there") or self.lines["still_here"])
                await out.done()
                return
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
        log.info("%s ended (%s), model tokens %s, hold phrases %s, drops %s", self.server.call_id,
                 self.ended, json.dumps(self.tokens, sort_keys=True), json.dumps(self.hold_stats),
                 json.dumps(self.drops, sort_keys=True))
