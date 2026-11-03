"""This layer's way to the tool server (docs/tools.md): it posts and returns the envelope, and
never raises, so a server it can't reach comes back as a refusal. The same contract as the
LiveKit worker's client (voice/livekit/server.py), kept here so this folder can be deleted on its
own after the bake-off."""

import hashlib
import json
import logging

import httpx

log = logging.getLogger("voice.elevenlabs.server")

UNREACHABLE = "server_unreachable"


def envelope(ok: bool, say: str = "", data: dict | None = None, refused: str | None = None) -> dict:
    return {"ok": ok, "say": say, "data": data or {}, "refused": refused}


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    raw = json.dumps([call_id, tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


class ToolServer:
    def __init__(self, base_url: str, timeout_s: float = 4.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s,
                                       transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        try:
            response = await self._http.post(path, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("tool server %s failed: %s", path, type(exc).__name__)
            return envelope(False, refused=UNREACHABLE)

    async def start(self, call_id: str, caller_id: str, dialed: str) -> dict:
        body = {"call_id": call_id, "caller_id": caller_id, "dialed_number": dialed,
                "direction": "inbound"}
        return await self._post("/calls/start", body)

    async def turn(self, call_id: str, speaker: str, text: str, timings: dict | None = None) -> dict:
        body = {"speaker": speaker, "text": text, "timings": timings or {}}
        return await self._post(f"/calls/{call_id}/turn", body)

    async def end(self, call_id: str, reason: str) -> dict:
        return await self._post(f"/calls/{call_id}/end", {"reason": reason})

    async def tool(self, call_id: str, name: str, args: dict, write: bool) -> dict:
        headers = {"Idempotency-Key": idempotency_key(call_id, name, args)} if write else None
        return await self._post(f"/tools/{name}", {"call_id": call_id, **args}, headers)
