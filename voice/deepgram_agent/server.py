"""The phone side of the Deepgram entry: Twilio calls the number's voice URL, gets TwiML that opens
a media stream to this app, and the app bridges that stream to a Deepgram agent session
(agent.py). Both sides speak 8 kHz mu-law, so the audio passes through untouched.

    uvicorn --factory voice.deepgram_agent.server:create_app

Routes: POST /deepgram/twiml (Twilio's webhook, signature checked) and the websocket
/deepgram/stream. Twilio's media stream messages (SOURCE:
https://www.twilio.com/docs/voice/media-streams/websocket-messages): connected, start, media,
stop in; media and clear out. The stream carries no signature, so the TwiML hands it a key
derived from the call's sid and the Twilio auth token, and a stream without it is closed.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
from xml.sax.saxutils import quoteattr

import httpx
import yaml
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

from voice.livekit.server import ToolServer
from voice.livekit.settings import ROOT

from .agent import Call, prepare

log = logging.getLogger("deepgram_agent")

TWILIO = "https://api.twilio.com/2010-04-01"
PHONE_AUDIO = {"input": {"encoding": "mulaw", "sample_rate": 8000},
               "output": {"encoding": "mulaw", "sample_rate": 8000, "container": "none"}}


def twilio_signature(token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's request signature (SOURCE: https://www.twilio.com/docs/usage/webhooks/webhooks-security)."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()


def stream_key(token: str, call_sid: str) -> str:
    return hmac.new(token.encode(), f"deepgram-stream:{call_sid}".encode(), hashlib.sha256).hexdigest()


def twiml(stream_url: str, call_sid: str, caller: str, dialed: str, key: str) -> str:
    params = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>"
                     for k, v in (("call_sid", call_sid), ("from", caller), ("to", dialed),
                                  ("key", key)))
    return (f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Connect>"
            f"<Stream url={quoteattr(stream_url)}>{params}</Stream></Connect></Response>")


class PhoneServer:
    """The tool server as agent.Call wants it, on top of the worker's client."""

    def __init__(self, tools: ToolServer):
        self.tools = tools

    async def turn(self, speaker: str, text: str) -> dict:
        return await self.tools.turn(speaker, text, {})

    async def tool(self, name: str, args: dict, key: str | None) -> dict:
        return await self.tools.tool(name, args, write=key is not None)

    async def end(self, reason: str) -> dict:
        result = await self.tools.end(reason)
        await self.tools.aclose()
        return result


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    cfg = config["realtime"]["deepgram_agent"]
    model = config["profiles"][cfg["brain"]]["model"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    public = os.environ["DEEPGRAM_AGENT_URL"].rstrip("/")
    tool_server_url = os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8100")
    app = FastAPI()

    @app.get("/deepgram/health")
    async def health():
        return {"ok": True}

    @app.post("/deepgram/twiml")
    async def answer(request: Request):
        form = {k: str(v) for k, v in (await request.form()).items()}
        expected = twilio_signature(token, f"{public}/deepgram/twiml", form)
        if not hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", "")):
            return Response(status_code=403)
        sid = form.get("CallSid", "")
        stream = public.replace("https://", "wss://") + "/deepgram/stream"
        body = twiml(stream, sid, form.get("From", ""), form.get("To", ""), stream_key(token, sid))
        return Response(body, media_type="application/xml")

    @app.websocket("/deepgram/stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        stream_sid = ""
        call: Call | None = None
        reason = "caller_hangup"

        async def play(audio: bytes) -> None:
            await ws.send_text(json.dumps({"event": "media", "streamSid": stream_sid,
                                           "media": {"payload": base64.b64encode(audio).decode()}}))

        async def clear() -> None:
            await ws.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))

        try:
            while True:
                message = json.loads(await ws.receive_text())
                event = message.get("event")
                if event == "start":
                    start = message["start"]
                    stream_sid = start["streamSid"]
                    params = start.get("customParameters") or {}
                    sid = params.get("call_sid", "")
                    if not hmac.compare_digest(params.get("key", ""), stream_key(token, sid)):
                        await ws.close(code=4403)
                        return

                    async def hang_up(sid=sid) -> None:
                        nonlocal reason
                        reason = "agent_end"
                        await complete(sid)

                    call_id = f"dg-{sid}"
                    tools = ToolServer(tool_server_url, call_id)
                    started = await tools.start(params.get("from", ""), params.get("to", ""))
                    message_, writes, goodbye = prepare(
                        started, cfg, model, os.environ["ANTHROPIC_API_KEY"], PHONE_AUDIO)
                    call = Call(PhoneServer(tools), writes, play, clear, hang_up,
                                os.environ["DEEPGRAM_API_KEY"])
                    await call.open(call_id, message_, goodbye)
                    log.info("%s: Deepgram agent on, caller %s", call_id, params.get("from"))
                elif event == "media" and call is not None:
                    await call.send(base64.b64decode(message["media"]["payload"]))
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            if call is not None:
                await call.close(reason)

    async def complete(call_sid: str) -> None:
        sid = os.environ["TWILIO_ACCOUNT_SID"]
        async with httpx.AsyncClient(auth=(sid, token), timeout=10) as http:
            response = await http.post(f"{TWILIO}/Accounts/{sid}/Calls/{call_sid}.json",
                                       data={"Status": "completed"})
            if response.status_code >= 400:
                log.warning("hang-up of %s failed: %s", call_sid, response.text[:200])

    return app
