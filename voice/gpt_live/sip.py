"""GPT-Live answering the phone. Twilio sends the call to OpenAI's SIP endpoint; OpenAI posts a
`live.transport.incoming` webhook here; this service starts the call on the tool server, accepts
the call with the session, and attaches a sideband WebSocket that carries the conversation's events
(session.py) while SIP carries the audio. The call ends through the hangup endpoint once the tool
server allows it, or when the caller hangs up.

Flow (SOURCE: https://developers.openai.com/api/docs/guides/voice-sip?api=live and
https://developers.openai.com/api/docs/guides/voice-server-controls?api=live, read 2026-11-05):
- Webhook: verify the signature (Standard Webhooks), skip repeated deliveries by `webhook-id`,
  answer 200 at once and handle the call in the background.
- `POST /v1/live/sessions/{id}/accept` with `{"session": {...}}`. SIP negotiates the audio format.
- `wss://api.openai.com/v1/live/sessions/{id}/attach`: no `session.start`, the session is running.
- `POST /v1/live/sessions/{id}/hangup` with no body.

Run: uvicorn --factory voice.gpt_live.sip:create_app --port 8104 (deploy/gpt_live/deploy.sh)
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import websockets
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from voice.gpt_live import session as live

log = logging.getLogger("gpt_live.sip")
logging.basicConfig(level=logging.INFO)

API = "https://api.openai.com/v1/live/sessions"
ATTACH = "wss://api.openai.com/v1/live/sessions/{id}/attach"
INCOMING = ("live.transport.incoming", "live.call.incoming")
CALL_PREFIX = "gptlive_"
# Standard Webhooks: refuse a delivery stamped more than five minutes away from now.
TOLERANCE_S = 300
SEEN_MAX = 1000
# A tool server that can't be reached gets a busy signal rather than an agent with no record.
BUSY = 486
END_REASONS = {"remote_hangup": "caller_hangup", "close_requested": "agent_end"}
CONFIG = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


def verify(secret: str, headers: dict, body: bytes, now: float | None = None) -> bool:
    """Standard Webhooks signature check: HMAC-SHA256 over "id.timestamp.body" with the base64
    secret after its "whsec_" prefix; the header lists "v1,<base64>" signatures."""
    wid, stamp = headers.get("webhook-id", ""), headers.get("webhook-timestamp", "")
    signatures = headers.get("webhook-signature", "")
    if not (secret and wid and stamp and signatures):
        return False
    try:
        if abs((now or time.time()) - int(stamp)) > TOLERANCE_S:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    signed = f"{wid}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in signatures.split():
        version, _, value = part.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


def number(headers: list[dict], name: str) -> str:
    """The phone number in a SIP From or To header ("sip:+14155550123@host"), or ""."""
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            match = re.search(r"sips?:(\+?\d+)@", header.get("value", ""))
            return match.group(1) if match else ""
    return ""


def end_reason(closed: dict | None) -> str:
    if not closed:
        return "dropped"
    return END_REASONS.get(closed.get("reason", ""), "dropped")


class ToolServer:
    """The tool server over HTTP, with the method shapes of evals/world.py ToolServer, so the same
    Conversation runs on the rig and here. A failed tool call returns the retry envelope the Retell
    bridge uses."""

    def __init__(self, client: httpx.Client):
        self.client = client

    def _post(self, path: str, body: dict, headers: dict | None = None) -> dict | None:
        try:
            response = self.client.post(path, json=body, headers=headers or {})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("tool server %s failed: %s", path, exc)
            return None

    def start(self, call_id: str, caller_id: str, dialed: str) -> dict | None:
        return self._post("/calls/start", {"call_id": call_id, "caller_id": caller_id,
                                           "dialed_number": dialed, "direction": "inbound"})

    def turn(self, call_id: str, speaker: str, text: str) -> dict | None:
        return self._post(f"/calls/{call_id}/turn", {"speaker": speaker, "text": text,
                                                      "timings": {}})

    def tool(self, name: str, call_id: str, args: dict, key: str | None = None) -> dict:
        headers = {"Idempotency-Key": key} if key else {}
        answer = self._post(f"/tools/{name}", {**args, "call_id": call_id}, headers)
        if answer is None:
            return {"ok": False, "say": "Sorry, give me just a moment.", "refused": "server_error"}
        return answer

    def end(self, call_id: str, reason: str) -> dict | None:
        return self._post(f"/calls/{call_id}/end", {"reason": reason})


class Phone:
    def __init__(self, api_key: str, webhook_secret: str, server: ToolServer, cfg: dict,
                 api: httpx.AsyncClient, connect=None):
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.server = server
        self.cfg = cfg
        self.api = api
        self.connect = connect or websockets.connect
        self.seen: dict[str, None] = {}
        self.calls: dict[str, asyncio.Task] = {}

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def first_delivery(self, webhook_id: str) -> bool:
        if webhook_id in self.seen:
            return False
        self.seen[webhook_id] = None
        if len(self.seen) > SEEN_MAX:
            self.seen.pop(next(iter(self.seen)))
        return True

    def incoming(self, event: dict) -> None:
        data = event.get("data") or {}
        session_id = data.get("session_id") or data.get("call_id") or ""
        if event.get("type") not in INCOMING or data.get("type", "sip") != "sip" or not session_id:
            return
        if session_id in self.calls:
            return
        task = asyncio.get_running_loop().create_task(self.answer(session_id, data))
        self.calls[session_id] = task
        task.add_done_callback(lambda _t: self.calls.pop(session_id, None))

    async def answer(self, session_id: str, data: dict) -> None:
        headers = data.get("sip_headers") or []
        caller, dialed = number(headers, "From"), number(headers, "To")
        call_id = CALL_PREFIX + session_id
        started = await asyncio.to_thread(self.server.start, call_id, caller, dialed)
        if not started or not started.get("ok"):
            log.warning("%s: tool server did not start the call; rejecting", call_id)
            await self.api.post(f"{API}/{session_id}/reject", headers=self.auth,
                                json={"status_code": BUSY})
            return
        start = started["data"]
        specs, voice = live.tenant_files(start)
        greeting = live.opening(start, started.get("say", ""), voice)
        config = {"type": "live", **live.session_config(start, specs, self.cfg, greeting)}
        accepted = await self.api.post(f"{API}/{session_id}/accept", headers=self.auth,
                                       json={"session": config})
        if accepted.status_code != 200:
            log.warning("%s: accept answered %s %s", call_id, accepted.status_code,
                        accepted.text[:500])
            await asyncio.to_thread(self.server.end, call_id, "error")
            return
        log.info("%s: accepted, caller %s", call_id, caller)
        await self.attach(session_id, call_id, specs, greeting)

    async def attach(self, session_id: str, call_id: str, specs: list[dict],
                     greeting: str) -> None:
        ws = await self.connect(ATTACH.format(id=session_id), additional_headers=self.auth,
                                max_size=None)

        async def send(event: dict) -> None:
            try:
                await ws.send(json.dumps(event))
            except websockets.ConnectionClosed:
                pass

        async def hang_up() -> None:
            log.info("%s: hanging up", call_id)
            response = await self.api.post(f"{API}/{session_id}/hangup", headers=self.auth)
            if response.status_code != 200:
                log.warning("%s: hangup answered %s", call_id, response.status_code)

        conversation = live.Conversation(
            call_id, self.server, {s["name"] for s in specs if s["write"]}, send, hang_up,
            price=self.cfg["price"])
        await send(live.greet_event(greeting))
        try:
            async for raw in ws:
                event = json.loads(raw)
                if event.get("type") in ("session.input_audio.append",
                                         "session.output_audio.delta"):
                    # Reflected audio: SIP carries the call, the sideband only gets copies.
                    if event["type"] == "session.output_audio.delta":
                        conversation.last_agent_delta = conversation.clock()
                    continue
                await conversation.handle(event)
                if conversation.closed:
                    break
        except websockets.ConnectionClosed:
            pass
        finally:
            await conversation.drain()
            await ws.close()
            reason = end_reason(conversation.closed)
            await asyncio.to_thread(self.server.end, call_id, reason)
            cost = conversation.cost(self.cfg["price"]["per_minute"])
            log.info("%s: ended (%s), %.0f s, cost %s", call_id, reason,
                     conversation.voice_seconds, {k: round(v, 4) for k, v in cost.items()})


def build_app(phone: Phone) -> FastAPI:
    app = FastAPI(title="GPT-Live phone", version="0.1.0")
    app.state.phone = phone

    @app.post("/gpt-live/webhook")
    async def webhook(request: Request):
        raw = await request.body()
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not verify(phone.webhook_secret, headers, raw):
            return JSONResponse({"detail": "bad signature"}, 401)
        if phone.first_delivery(headers["webhook-id"]):
            try:
                phone.incoming(json.loads(raw))
            except ValueError:
                return JSONResponse({"detail": "bad body"}, 400)
        return Response(status_code=200)

    @app.get("/gpt-live/healthz")
    def healthz():
        return {"ok": True, "calls": len(phone.calls)}

    return app


def create_app() -> FastAPI:
    cfg = yaml.safe_load(CONFIG.read_text())["realtime"]["gpt_live"]
    server = ToolServer(httpx.Client(
        base_url=os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8100"), timeout=10.0))
    phone = Phone(os.environ["OPENAI_API_KEY"], os.environ.get("OPENAI_WEBHOOK_SECRET", ""),
                  server, cfg, httpx.AsyncClient(timeout=15.0))
    return build_app(phone)
