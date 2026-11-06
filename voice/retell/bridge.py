"""The bridge between Retell and the tool server. It holds no rules: it checks that each request
came from Retell, turns Retell's shapes into the tool contract (docs/tools.md), and does the few
things Retell can't do on its own, as close as it allows to what the LiveKit worker does.

- Inbound webhook (before the call connects): /calls/start, so the lookup happens before hello and
  its result goes into the prompt as dynamic variables.
- Every custom function: post any lines not yet posted to /calls/{id}/turn, then forward the call
  to /tools/<name> with call_id and an Idempotency-Key.
- Call webhooks: transcript_updated posts new lines as they happen; call_ended posts the rest and
  calls /calls/{id}/end; call_analyzed keeps Retell's latency and cost for the bake-off.
- Safety: when the turn check says to speak the safety lines, the bridge hands Retell the exact
  text through Update Live Call and makes the agent reply at once. Retell has no way to speak text
  from code, so the model says it; the server only counts it as said when an agent line contains
  it word for word.
- Hang-up: the built-in end-call tool is off, so only the server's end_call can end a call. When it
  allows one, the bridge stops the call through Retell's API once the goodbye has had time to play.

Run: uvicorn voice.retell.bridge:create_app --factory --port 8102 (make retell-bridge)
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.tools import TOOLS
from prompts.assemble import instructions, server_notes

from . import conversation, signature
from .api import Retell, RetellError
from .ledger import Ledger
from .letters import count_letters
from .timings import call_summary
from .transcript import PREFIX, lines, our_call_id, retell_id

log = logging.getLogger("retell.bridge")
logging.basicConfig(level=logging.INFO)

SAY_RETRY = "Sorry, give me just a moment."
# The dynamic variable Retell's transfer_call tool dials (voice/retell/transfer_tool.py).
TRANSFER_VARIABLE = "transfer_number"
SAFETY_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
# The server counts the safety lines as said only when the agent's words contain them exactly.
SAY_NOW = (
    "Say this to the caller now, before anything else, exactly as written with no word or "
    "punctuation changed: {text}"
)

# The server's notes, as the native agent (voice/archive/retell_native) gets them mid-call: marked as
# private, since the bare "[server] ..." text read to its model as something it had said itself
# ("Sorry, that was just the reference info", Jackson's call 2). Agents listed in
# RETELL_PRIVATE_NOTE_AGENTS get the marked form; the others are unchanged.
PRIVATE_NOTE = (
    "Private note for your next reply. The caller didn't say this and can't hear it; never mention "
    "it or apologise for it: {notes}"
)

# How long to let the agent's goodbye play before stopping the call: a fixed allowance plus a rate
# per word of the agent's last line (ASSUMED; checked against the first real calls).
HANGUP_BASE_S = 3.0
HANGUP_PER_WORD_S = 0.35
HANGUP_MAX_S = 15.0
# After the final line has played, the same pause the LiveKit worker keeps before hanging up
# (config/models.yaml voice.end_call_grace_s).
HANGUP_GRACE_S = 1.5
# How many times the hang-up looks again for a final line that is still coming or growing.
HANGUP_CHECKS = 4

END_REASONS = {
    "user_hangup": "caller_hangup",
    "agent_hangup": "agent_end",
    "call_transfer": "transfer",
}


def end_reason(disconnection: str | None, hung_up: bool) -> str:
    if hung_up:
        return "agent_end"
    if not disconnection:
        return "dropped"
    if disconnection.startswith(("error", "dial_")):
        return "error"
    return END_REASONS.get(disconnection, "dropped")


# Who ended an eval chat, as the harness put it in the chat's metadata before ending it
# (voice/retell/eval_adapter.py). A chat has no caller to hang up, so anything else is the agent's.
CHAT_END_REASONS = ("caller_hangup", "agent_end", "transfer")


def chat_end_reason(chat: dict) -> str:
    reason = (chat.get("metadata") or {}).get("end_reason")
    return reason if reason in CHAT_END_REASONS else "agent_end"


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    """Derived from call id, tool and arguments (docs/tools.md), so a repeat returns the first
    answer."""
    raw = json.dumps([call_id, tool, args], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


# A caller line that is only thanks or a goodbye ("Thanks. Bye.", "Okay, you too."): no reason to
# keep a call open that the server already agreed to end.
GOODBYE_ONLY = re.compile(
    r"^(?:(?:ok(?:ay)?|alright|all right|great|perfect|yeah|yes|yep|sure|thanks|thank you(?: so much)?"
    r"|thx|bye(?: bye)?|goodbye|good bye|you too|same to you|have a good (?:one|day|night)"
    r"|take care|see you|later|cheers|appreciate it)[\s,.!]*)+$",
    re.IGNORECASE,
)
# The agent's own goodbye: the tenant's ("Thanks for calling Summit Air. Have a great rest of your
# day.") or any line with a goodbye in it.
AGENT_GOODBYE = re.compile(
    r"\b(?:thanks for calling|have a great (?:rest of your )?day|take care|goodbye|bye)\b",
    re.IGNORECASE,
)


def only_goodbye(text: str) -> bool:
    return bool(text.strip()) and bool(GOODBYE_ONLY.match(text.strip()))


def agent_goodbye(text: str) -> bool:
    return bool(AGENT_GOODBYE.search(text or ""))


def last_line(said: list, speaker: str) -> str:
    texts = [line.text for line in said if line.speaker == speaker]
    return texts[-1] if texts else ""


def hangup_delay(agent_text: str) -> float:
    """Time for a line to play, then the grace."""
    played = min(HANGUP_BASE_S + HANGUP_PER_WORD_S * len(agent_text.split()), HANGUP_MAX_S)
    return played + HANGUP_GRACE_S


def compact(answer: dict) -> dict:
    """What the model reads: the envelope without empty fields, as the LiveKit worker sends it."""
    return {k: v for k, v in answer.items() if v not in (None, "", {})}


class ToolServer:
    """Our tool server, as the voice layer sees it. Lifecycle calls return None when the server
    can't be reached, so a sync can try again later; tool calls always return an envelope."""

    def __init__(self, client: httpx.Client):
        self.client = client

    def _post(self, path: str, body: dict, headers: dict | None = None) -> dict | None:
        try:
            response = self.client.post(path, json=body, headers=headers or {})
            if response.status_code != 200:
                log.warning("tool server %s answered %s", path, response.status_code)
                return None
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("tool server %s failed: %s", path, exc)
            return None

    def start(
        self, call_id: str, caller_id: str, dialed: str, direction: str, flow: str = ""
    ) -> dict | None:
        body = {
            "call_id": call_id,
            "caller_id": caller_id,
            "dialed_number": dialed,
            "direction": direction,
        }
        if flow:
            body["flow"] = flow
        return self._post("/calls/start", body)

    def turn(
        self, call_id: str, speaker: str, text: str, timings: dict, replaces_last: bool = False
    ) -> dict | None:
        body = {"speaker": speaker, "text": text, "timings": timings}
        if replaces_last:
            body["replaces_last"] = True
        return self._post(f"/calls/{call_id}/turn", body)

    def end(self, call_id: str, reason: str) -> dict | None:
        return self._post(f"/calls/{call_id}/end", {"reason": reason})

    def transfer_done(self, call_id: str, transfer_id: int, outcome: str) -> dict | None:
        return self._post(f"/calls/{call_id}/transfer/{transfer_id}/done", {"outcome": outcome})

    def tool(self, call_id: str, name: str, args: dict) -> dict:
        headers = {"Idempotency-Key": idempotency_key(call_id, name, args)}
        answer = self._post(f"/tools/{name}", {**args, "call_id": call_id}, headers)
        if answer is None:
            return {"ok": False, "say": SAY_RETRY, "data": {}, "refused": "server_error"}
        return answer


class Bridge:
    def __init__(
        self,
        api_key: str,
        tools: ToolServer,
        ledger: Ledger,
        retell: Retell,
        instructions: Callable[[dict], str],
        spoken: Callable[[str], dict],
        notes: Callable[[list[str]], list[str]],
        default_tenant: str,
        tool_names: set[str],
        now_ms: Callable[[], int] | None = None,
        later: Callable[[float, Callable[[], None]], None] | None = None,
    ):
        self.api_key = api_key
        self.tools = tools
        self.ledger = ledger
        self.retell = retell
        # From prompts/assemble.py and the tenant's agent.yaml, as the LiveKit worker uses them.
        self.instructions = instructions
        self.spoken = spoken
        self.notes = notes
        self.default_tenant = default_tenant
        self.tool_names = tool_names
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.later = later or _timer
        self.private_note_agents = set(filter(None, os.environ.get(
            "RETELL_PRIVATE_NOTE_AGENTS", "").split(",")))
        # Agents that get no mid-call notes at all; the safety lines still go by say_now, and
        # tool results still carry their notes. Jackson's agent said "that was just the reference
        # material" three times on one call after notes came in this way
        # (call_17353a5115066cd2e0ad091ddad, no knowledge base attached).
        self.no_note_agents = set(filter(None, os.environ.get(
            "RETELL_NO_NOTE_AGENTS", "").split(",")))
        # The socket agent's calls run the server-led flow on this same tool server
        # (docs/ai-process/briefs/socket-cutover.md); every other agent's calls start as before.
        self.server_led_agents = set(filter(None, os.environ.get(
            "RETELL_SERVER_LED_AGENTS", "").split(",")))
        # Agents whose calls aren't Summit Air calls, such as the hold line a transfer rings: Retell
        # sends their events here too (the account's webhook), and a call logged for one stayed
        # open on the tool server and blocked the live-world reset (6 November).
        self.ignored_agents = set(filter(None, os.environ.get(
            "RETELL_IGNORE_AGENTS", "").split(",")))
        # The last call object each call's events brought, for the word timings the hang-up
        # reads when get-call has none.
        self.seen: dict[str, dict] = {}
        # Safety lines already asked for on each call: a caller line that grows between Retell's
        # events is posted again, and each post asked for the same lines again (three times off
        # one line on Jackson's call call_0246b91f77e01a4b18583e6ca8b). A hazard's lines go out
        # once per call; new text from the server (another hazard) still goes.
        self.safety_sent: dict[str, set[str]] = {}
        # The warm transfer the server allowed on each call (request_transfer): our call id and
        # the transfer id, so Retell's transfer webhooks can report how it went.
        self.transfers: dict[str, tuple[str, int]] = {}

    def verified(self, raw: bytes, header: str | None) -> bool:
        return signature.verify(raw, self.api_key, header, self.now_ms())

    # ---- call lifecycle ------------------------------------------------------------------------

    def inbound(self, event: dict) -> dict:
        """Before the call connects: the lookup before hello, and the prompt and greeting built
        from it. Never rejects a call; if the tool server is down, the agent says so and the call
        ends, as on the LiveKit stack."""
        inbound = event.get("call_inbound") or {}
        rid = inbound.get("call_id", "")
        call_id = PREFIX + rid
        flow = "server_led" if inbound.get("agent_id") in self.server_led_agents else ""
        start = self.tools.start(
            call_id, inbound.get("from_number", ""), inbound.get("to_number", ""), "inbound", flow
        )
        self.ledger.remember(rid, call_id, "call")
        if start is None or not start.get("ok"):
            variables = conversation.server_down(call_id, self.spoken(self.default_tenant))
            self.hang_up_later(rid, hangup_delay(variables["greeting"]))
        else:
            self.ledger.mark(rid, started=1)
            variables = conversation.for_call(call_id, start, self.instructions, self.spoken)
        return {
            "call_inbound": {
                "dynamic_variables": variables,
                "metadata": {"summit_call_id": call_id},
            }
        }

    def ensure_started(self, obj: dict, kind: str) -> str:
        """Our call id for this Retell call or chat, starting it on the tool server if nothing
        has yet (a web call from Retell's dashboard has no inbound webhook)."""
        rid, call_id = retell_id(obj), our_call_id(obj)
        self.ledger.remember(rid, call_id, kind)
        row = self.ledger.get(rid)
        if not row["started"]:
            caller = obj.get("from_number") or kind
            dialed = obj.get("to_number") or ""
            start = self.tools.start(call_id, caller, dialed, obj.get("direction") or "inbound")
            if start and start.get("ok"):
                self.ledger.mark(rid, started=1)
        return call_id

    def sync(self, obj: dict, call_id: str, settled: bool = True) -> tuple[list[dict], list[str]]:
        """Post new lines. Returns the safety actions the turn check asked for, and the notes for
        the model from its hints (commercial, do-not-call)."""
        actions, hints = [], []
        rid = retell_id(obj)
        said = lines(obj)
        if obj.get("start_timestamp") and any(line.words for line in said):
            self.seen[rid] = obj
        posted = self.ledger.sync(call_id, said, self.tools.turn, settled)
        for speaker, answer in posted:
            if speaker == "caller" and not only_goodbye(last_line(said, "caller")):
                # The caller said something more after an allowed end_call: keep the call open.
                # A "Thanks. Bye." in the grace keeps the hang-up (Retell call
                # call_1840576e622e3d729b760085142 ran to its 5-minute limit after one).
                self.ledger.mark(rid, hangup_pending=0)
            data = answer.get("data") or {}
            safety = data.get("safety")
            if safety and safety.get("action") in SAFETY_ACTIONS and safety.get("text"):
                actions.append(safety)
            # The server's own words for the turn (the electrical lines, the card stop, the billing
            # callback) go to the agent the same way: they were dropped, so the electrical lines
            # were said in neither electrical case (Retell eval suite round 1).
            spoken = data.get("spoken")
            if spoken and spoken.get("text"):
                actions.append({"action": "speak_lines", "kind": spoken.get("kind"),
                                "text": spoken["text"]})
            hints += data.get("hints") or []
        # The model said its goodbye again after the hang-up was called off, without end_call:
        # the hang-up is armed again for that goodbye.
        # The line may still be playing (not yet posted); the hang-up waits for it to finish.
        row = self.ledger.get(rid)
        last = said[-1].text if said and said[-1].speaker == "agent" else ""
        if (
            row and row["end_allowed"] and not row["hangup_pending"] and not row["hung_up"]
            and not row["ended"] and obj.get("call_id") and agent_goodbye(last)
        ):
            self.hang_up_later(rid, hangup_delay(last), last)
        return actions, self.notes(hints)

    def webhook(self, event: dict) -> bool:
        """Handle one call or chat event. False when the call could not be closed on the tool
        server, so Retell sends the event again."""
        name = event.get("event", "")
        if "chat" in event:
            return self.chat_event(name, event["chat"] or {})
        call = dict(event.get("call") or {})
        rid = retell_id(call)
        if not rid or call.get("agent_id") in self.ignored_agents:
            return True
        # transcript_updated sends the transcript beside the call object, not inside it.
        if event.get("transcript_with_tool_calls") and not call.get("transcript_with_tool_calls"):
            call["transcript_with_tool_calls"] = event["transcript_with_tool_calls"]
        log.info(
            "%s %s: %d lines (event keys %s; call has %s)",
            name,
            rid,
            len(lines(call)),
            sorted(event),
            sorted(k for k in call if k.startswith("transcript")),
        )
        call_id = self.ensure_started(call, "call")
        if name == "transcript_updated":
            safety, notes = self.sync(call, call_id, settled=False)
            if safety and self.first_time(rid, safety[-1]["text"]):
                self.say_now(rid, SAY_NOW.format(text=safety[-1]["text"]))
            elif notes and call.get("agent_id") not in self.no_note_agents:
                text = " ".join(notes)
                if call.get("agent_id") in self.private_note_agents:
                    text = PRIVATE_NOTE.format(notes=text)
                self.tell(rid, text)
        elif name == "call_ended":
            self.sync(call, call_id)
            row = self.ledger.get(rid)
            if not row["ended"]:
                reason = end_reason(call.get("disconnection_reason"), bool(row["hung_up"]))
                if self.tools.end(call_id, reason) is None:
                    return False
                self.ledger.mark(rid, ended=1, hangup_pending=0)
        elif name in ("transfer_bridged", "transfer_cancelled"):
            self.transfer_event(name, rid)
        elif name == "call_analyzed":
            self.ledger.mark(rid, summary_json=json.dumps(call_summary(call)))
        return True

    def chat_event(self, name: str, chat: dict) -> bool:
        rid = retell_id(chat)
        if not rid:
            return True
        call_id = self.ensure_started(chat, "chat")
        if name == "transcript_updated":
            # A chat can't be told to say anything mid-reply, so a safety action here reaches the
            # model only through its next tool result (and the server's gates).
            self.sync(chat, call_id, settled=False)
        elif name == "chat_ended":
            self.sync(chat, call_id)
            row = self.ledger.get(rid)
            if not row["ended"]:
                if self.tools.end(call_id, chat_end_reason(chat)) is None:
                    return False
                self.ledger.mark(rid, ended=1)
        return True

    # ---- custom functions ----------------------------------------------------------------------

    def tool(self, name: str, body: dict) -> dict:
        kind = "chat" if "chat" in body else "call"
        obj = body.get(kind) or {}
        rid = retell_id(obj)
        call_id = self.ensure_started(obj, kind)
        safety, notes = self.sync(obj, call_id)
        args = body.get("args") or {}
        answer = compact(self.tools.tool(call_id, name, args))
        # Lines that came in with this tool call go to the model in its result: bare, and ahead
        # of the result's own sentence, which the agent says as written. The SAY_NOW wrapper is
        # only for update-live-call's context; in a result the model read it aloud ("Please say
        # this now, exactly as written:", Retell eval suite on Jackson's agent).
        if safety and self.first_time(rid, safety[-1]["text"]):
            lines_ = safety[-1]["text"]
            answer["speak_first"] = lines_
            answer["say"] = " ".join(x for x in (lines_, answer.get("say") or "") if x)
        # The server's top-priority or raised-priority line, said through Retell now, and taken
        # out of the result's say, so the model can't drop it (it said only the window on
        # Jackson's call call_0246b91f77e01a4b18583e6ca8b).
        first = (answer.get("data") or {}).get("spoken_first")
        if first and kind == "call":
            self.say_now(rid, SAY_NOW.format(text=first))
            say = answer.get("say") or ""
            if say.startswith(first):
                answer["say"] = say[len(first):].strip()
        if name == "request_transfer" and kind == "call" and answer.get("ok"):
            answer = self.allow_transfer(rid, call_id, answer)
        if notes:
            answer["server_notes"] = notes
        hang_up = answer.get("ok") and (answer.get("data") or {}).get("hang_up")
        if name == "end_call" and kind == "call" and hang_up:
            self.ledger.mark(rid, end_allowed=1)
            so_far = lines(obj)
            said = [line.text for line in so_far if line.speaker == "agent"]
            last = said[-1] if said else ""
            # An end_call before the agent has said anything this turn is timed from the goodbye
            # still to come, not from the agent's line before the caller's: that line was 26 words
            # long and the hang-up came 4.7 s after the goodbye (call_5600e8a36929e14b4b3915c9fcb).
            this_turn = last if so_far and so_far[-1].speaker == "agent" else ""
            # The tool's own sentence may still be said after the lines so far: its time counts,
            # but the line compared later is the transcript's own (PR #158 review: comparing the
            # two together never matched and added a whole extra wait after the goodbye).
            say = answer.get("say") or ""
            final = (
                this_turn if say and say in this_turn
                else " ".join(x for x in (this_turn, say) if x)
            )
            self.hang_up_later(rid, hangup_delay(final), last)
        return answer

    def allow_transfer(self, rid: str, call_id: str, answer: dict) -> dict:
        """The server said yes: give the call the number its transfer_call tool dials. The tool's
        destination is {{transfer_number}}, which nothing else sets, so a transfer_call the model
        makes without the server's yes has no number and fails. The number itself stays out of
        what the model reads. If Retell can't be told, the transfer is reported failed and the
        caller hears the stored callback line."""
        data = dict(answer.get("data") or {})
        plan = data.pop("transfer", None) or {}
        try:
            self.retell.set_variables(rid, {TRANSFER_VARIABLE: plan["number"]})
        except (RetellError, httpx.HTTPError, KeyError) as exc:
            log.warning("transfer number for %s not set: %s", rid, exc)
            if plan.get("id"):
                self.tools.transfer_done(call_id, plan["id"], "failed")
            return {"ok": False, "say": data.get("fallback", ""), "refused": "transfer_failed"}
        self.transfers[rid] = (call_id, plan["id"])
        return {**answer, "data": data}

    def transfer_event(self, name: str, rid: str) -> None:
        """Retell's word on the transfer: bridged means the office line picked up and the caller
        is with it; cancelled means it rang out or failed, and the agent has the caller back."""
        found = self.transfers.pop(rid, None)
        if found is None:
            return
        outcome = "connected" if name == "transfer_bridged" else "no_answer"
        self.tools.transfer_done(found[0], found[1], outcome)

    def hang_up_later(self, rid: str, delay: float, last_line: str = "", checks: int = 0) -> None:
        """Stop the call once its final line has played, plus the grace. end_call can come at the
        start of the turn, before the goodbye is said (Jackson's call on the Retell number hung up
        after "I'm sorry,"), so before stopping, the bridge reads the call again: a final agent
        line that has appeared or grown since gets its own time to play."""
        self.ledger.mark(rid, hangup_pending=1)

        def stop():
            row = self.ledger.get(rid)
            if not row or not row["hangup_pending"] or row["ended"]:
                self.seen.pop(rid, None)
                return
            call = self.read_call(rid)
            said = [line for line in lines(call) if line.speaker == "agent"]
            latest = said[-1].text if said else ""
            left = self.grace_left(call)
            if left is None and rid in self.seen:
                left = self.grace_left(self.seen[rid])
            if left is not None:
                # Retell's word timings say when the agent last spoke: stop the grace after it.
                if left > 0 and checks < HANGUP_CHECKS:
                    self.hang_up_later(rid, left, latest, checks + 1)
                    return
            elif latest and latest != last_line and checks < HANGUP_CHECKS:
                self.hang_up_later(rid, hangup_delay(latest), latest, checks + 1)
                return
            try:
                self.retell.stop_call(rid)
                self.ledger.mark(rid, hung_up=1, hangup_pending=0)
                self.seen.pop(rid, None)
            except (RetellError, httpx.HTTPError) as exc:
                log.warning("stop-call %s failed: %s", rid, exc)

        self.later(delay, stop)

    def read_call(self, rid: str) -> dict:
        """The call as Retell has it now, or {} if it can't be read."""
        try:
            return self.retell.get_call(rid) or {}
        except (RetellError, httpx.HTTPError) as exc:
            log.warning("get-call %s failed: %s", rid, exc)
            return {}

    def grace_left(self, call: dict) -> float | None:
        """Seconds until the grace after the agent's last spoken word has passed (zero or less:
        stop now), or None when the call has no word timings to tell."""
        said = [line for line in lines(call) if line.speaker == "agent" and line.words]
        start = call.get("start_timestamp")
        if not said or not start:
            return None
        spoken_until = start + float(said[-1].words[-1].get("end", 0)) * 1000
        return HANGUP_GRACE_S - (self.now_ms() - spoken_until) / 1000

    def first_time(self, rid: str, text: str) -> bool:
        """True the first time these safety lines are asked for on this call."""
        sent = self.safety_sent.setdefault(rid, set())
        if text in sent:
            return False
        sent.add(text)
        return True

    def say_now(self, rid: str, context: str) -> None:
        try:
            self.retell.say_next(rid, context)
        except (RetellError, httpx.HTTPError) as exc:
            log.warning("update-live-call %s failed: %s", rid, exc)

    def tell(self, rid: str, context: str) -> None:
        """A note for the model's next reply, without making it reply now."""
        try:
            self.retell.add_context(rid, context)
        except (RetellError, httpx.HTTPError) as exc:
            log.warning("update-live-call %s failed: %s", rid, exc)


def _timer(delay: float, fn: Callable[[], None]) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()


def build_app(bridge: Bridge) -> FastAPI:
    app = FastAPI(title="Retell bridge", version="0.1.0")
    app.state.bridge = bridge

    async def signed_json(request: Request) -> dict | None:
        raw = await request.body()
        if not bridge.verified(raw, request.headers.get("x-retell-signature")):
            return None
        try:
            body = json.loads(raw)
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    @app.post("/retell/inbound")
    async def inbound(request: Request):
        body = await signed_json(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        return await run_in_threadpool(bridge.inbound, body)

    @app.post("/retell/webhook")
    async def webhook(request: Request):
        body = await signed_json(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        if not await run_in_threadpool(bridge.webhook, body):
            return JSONResponse({"detail": "tool server unavailable"}, 503)
        return Response(status_code=204)

    @app.post("/retell/tools/{name}")
    async def tool(request: Request, name: str):
        body = await signed_json(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        if name not in bridge.tool_names:
            return JSONResponse({"detail": f"no tool {name}"}, 404)
        return await run_in_threadpool(bridge.tool, name, body)

    @app.post("/retell/letters")
    async def letters(request: Request):
        """Not a tool server tool: the bridge counts the letters itself (letters.py)."""
        body = await signed_json(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        args = body.get("args") or {}
        return count_letters(str(args.get("word", "")), str(args.get("letter", "")))

    @app.get("/retell/healthz")
    def healthz():
        return {"ok": True}

    return app


def create_app() -> FastAPI:
    api_key = os.environ["RETELL_API_KEY"]
    tool_server = os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8100")
    tenants_dir = Path(os.environ.get("TENANTS_DIR", "tenants"))
    ledger = Ledger(Path(os.environ.get("RETELL_BRIDGE_DB", "data/retell_bridge.db")))
    tools = ToolServer(httpx.Client(base_url=tool_server, timeout=10.0))
    bridge = Bridge(
        api_key,
        tools,
        ledger,
        Retell(api_key),
        instructions=instructions,
        spoken=lambda slug: conversation.spoken_lines(tenants_dir, slug),
        notes=server_notes,
        default_tenant=os.environ.get("DEFAULT_TENANT", "summit-air"),
        tool_names=set(TOOLS),
    )
    return build_app(bridge)
