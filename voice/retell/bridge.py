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
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from prompts.assemble import instructions
from voice.livekit.turns import server_notes

from . import conversation, signature
from .api import Retell, RetellError
from .ledger import Ledger
from .timings import call_summary
from .transcript import PREFIX, lines, our_call_id, retell_id

log = logging.getLogger("retell.bridge")
logging.basicConfig(level=logging.INFO)

SAY_RETRY = "Sorry, give me just a moment."
SAFETY_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
SAY_NOW = "Say this to the caller now, word for word, before anything else: {text}"

# How long to let the agent's goodbye play before stopping the call: a fixed allowance plus a rate
# per word of the agent's last line (ASSUMED; checked against the first real calls).
HANGUP_BASE_S = 3.0
HANGUP_PER_WORD_S = 0.35
HANGUP_MAX_S = 15.0

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


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    """Derived from call id, tool and arguments (docs/tools.md), so a repeat returns the first
    answer."""
    raw = json.dumps([call_id, tool, args], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def hangup_delay(agent_text: str) -> float:
    return min(HANGUP_BASE_S + HANGUP_PER_WORD_S * len(agent_text.split()), HANGUP_MAX_S)


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

    def start(self, call_id: str, caller_id: str, dialed: str, direction: str) -> dict | None:
        body = {
            "call_id": call_id,
            "caller_id": caller_id,
            "dialed_number": dialed,
            "direction": direction,
        }
        return self._post("/calls/start", body)

    def turn(self, call_id: str, speaker: str, text: str, timings: dict) -> dict | None:
        body = {"speaker": speaker, "text": text, "timings": timings}
        return self._post(f"/calls/{call_id}/turn", body)

    def end(self, call_id: str, reason: str) -> dict | None:
        return self._post(f"/calls/{call_id}/end", {"reason": reason})

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
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.later = later or _timer

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
        start = self.tools.start(
            call_id, inbound.get("from_number", ""), inbound.get("to_number", ""), "inbound"
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
            if self.tools.start(call_id, caller, dialed, obj.get("direction") or "inbound"):
                self.ledger.mark(rid, started=1)
        return call_id

    def sync(self, obj: dict, call_id: str) -> tuple[list[dict], list[str]]:
        """Post new lines. Returns the safety actions the turn check asked for, and the notes for
        the model from its hints (commercial, do-not-call)."""
        actions, hints = [], []
        for speaker, answer in self.ledger.sync(call_id, lines(obj), self.tools.turn):
            if speaker == "caller":
                # The caller spoke after an allowed end_call: keep the call open.
                self.ledger.mark(retell_id(obj), hangup_pending=0)
            data = answer.get("data") or {}
            safety = data.get("safety")
            if safety and safety.get("action") in SAFETY_ACTIONS and safety.get("text"):
                actions.append(safety)
            hints += data.get("hints") or []
        return actions, self.notes(hints)

    def webhook(self, event: dict) -> None:
        name = event.get("event", "")
        if "chat" in event:
            self.chat_event(name, event["chat"] or {})
            return
        call = event.get("call") or {}
        rid = retell_id(call)
        if not rid:
            return
        log.info("%s %s: %d lines", name, rid, len(lines(call)))
        call_id = self.ensure_started(call, "call")
        if name == "transcript_updated":
            safety, notes = self.sync(call, call_id)
            if safety:
                self.say_now(rid, SAY_NOW.format(text=safety[-1]["text"]))
            elif notes:
                self.tell(rid, " ".join(notes))
        elif name == "call_ended":
            self.sync(call, call_id)
            row = self.ledger.get(rid)
            if not row["ended"]:
                reason = end_reason(call.get("disconnection_reason"), bool(row["hung_up"]))
                if self.tools.end(call_id, reason) is not None:
                    self.ledger.mark(rid, ended=1, hangup_pending=0)
        elif name == "call_analyzed":
            self.ledger.mark(rid, summary_json=json.dumps(call_summary(call)))

    def chat_event(self, name: str, chat: dict) -> None:
        rid = retell_id(chat)
        if not rid:
            return
        call_id = self.ensure_started(chat, "chat")
        if name == "transcript_updated":
            # A chat can't be told to say anything mid-reply, so a safety action here reaches the
            # model only through its next tool result (and the server's gates).
            self.sync(chat, call_id)
        elif name == "chat_ended":
            self.sync(chat, call_id)
            row = self.ledger.get(rid)
            if not row["ended"] and self.tools.end(call_id, "agent_end") is not None:
                self.ledger.mark(rid, ended=1)

    # ---- custom functions ----------------------------------------------------------------------

    def tool(self, name: str, body: dict) -> dict:
        kind = "chat" if "chat" in body else "call"
        obj = body.get(kind) or {}
        rid = retell_id(obj)
        call_id = self.ensure_started(obj, kind)
        safety, notes = self.sync(obj, call_id)
        args = body.get("args") or {}
        answer = compact(self.tools.tool(call_id, name, args))
        # Lines that came in with this tool call go to the model in its result.
        if safety:
            answer["speak_first"] = SAY_NOW.format(text=safety[-1]["text"])
        if notes:
            answer["server_notes"] = notes
        hang_up = answer.get("ok") and (answer.get("data") or {}).get("hang_up")
        if name == "end_call" and kind == "call" and hang_up:
            said = [line for line in lines(obj) if line.speaker == "agent"]
            self.hang_up_later(rid, hangup_delay(said[-1].text if said else ""))
        return answer

    def hang_up_later(self, rid: str, delay: float) -> None:
        self.ledger.mark(rid, hangup_pending=1)

        def stop():
            row = self.ledger.get(rid)
            if not row or not row["hangup_pending"] or row["ended"]:
                return
            try:
                self.retell.stop_call(rid)
                self.ledger.mark(rid, hung_up=1, hangup_pending=0)
            except (RetellError, httpx.HTTPError) as exc:
                log.warning("stop-call %s failed: %s", rid, exc)

        self.later(delay, stop)

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
        await run_in_threadpool(bridge.webhook, body)
        return Response(status_code=204)

    @app.post("/retell/tools/{name}")
    async def tool(request: Request, name: str):
        body = await signed_json(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        return await run_in_threadpool(bridge.tool, name, body)

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
    )
    return build_app(bridge)
