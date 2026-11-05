"""The worker's only way to the tool server (docs/tools.md). Every fact and rule is the server's;
this client just posts and returns the envelope. It never raises: a server it can't reach comes
back as a refusal, so the call keeps going and the model hears why."""

import hashlib
import json
import logging

import httpx

log = logging.getLogger("voice.server")

UNREACHABLE = "server_unreachable"


def envelope(ok: bool, say: str = "", data: dict | None = None, refused: str | None = None) -> dict:
    return {"ok": ok, "say": say, "data": data or {}, "refused": refused}


def idempotency_key(call_id: str, tool: str, args: dict) -> str:
    """Derived from call, tool and arguments, so a retried write returns the first result."""
    raw = json.dumps([call_id, tool, args], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


class ToolServer:
    def __init__(
        self,
        base_url: str,
        call_id: str,
        timeout_s: float = 4.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.call_id = call_id
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        try:
            response = await self._http.post(path, json=body, headers=headers)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # No retry here: a retry adds dead air, and the model can ask again.
            log.warning("tool server %s failed: %s", path, type(exc).__name__)
            return envelope(False, refused=UNREACHABLE)

    async def start(
        self,
        caller_id: str,
        dialed_number: str,
        direction: str = "inbound",
    ) -> dict:
        body = {
            "call_id": self.call_id,
            "caller_id": caller_id,
            "dialed_number": dialed_number,
            "direction": direction,
        }
        return await self._post("/calls/start", body)

    async def turn(self, speaker: str, text: str, timings: dict[str, float]) -> dict:
        body = {"speaker": speaker, "text": text, "timings": timings}
        return await self._post(f"/calls/{self.call_id}/turn", body)

    async def end(self, reason: str) -> dict:
        return await self._post(f"/calls/{self.call_id}/end", {"reason": reason})

    async def transfer(self, flag_id: int, callback_id: int | None) -> dict:
        """Whether a person flag goes to a person now (docs/tools.md, /calls/{id}/transfer)."""
        body = {"flag_id": flag_id, "callback_id": callback_id}
        return await self._post(f"/calls/{self.call_id}/transfer", body)

    async def transfer_done(self, transfer_id: int, outcome: str) -> dict:
        body = {"outcome": outcome}
        return await self._post(f"/calls/{self.call_id}/transfer/{transfer_id}/done", body)

    async def handoff_done(self, handoff_id: int, outcome: str) -> dict:
        body = {"outcome": outcome}
        return await self._post(f"/calls/{self.call_id}/handoff/{handoff_id}/done", body)

    async def recording(self, egress_id: str, object_key: str) -> dict:
        body = {"egress_id": egress_id, "object_key": object_key}
        return await self._post(f"/calls/{self.call_id}/recording", body)

    async def outbound_done(
        self, outbound_id: int, answered: bool, voicemail: bool, kind: str = ""
    ) -> dict:
        """A heads-up call (app/heads_up.py) reports to its own route; every other kind to
        /outbound/{id}/done."""
        body = {"answered": answered, "voicemail": voicemail}
        if kind == "heads_up":
            return await self._post(f"/outbound/heads-up/{outbound_id}/done", body)
        return await self._post(f"/outbound/{outbound_id}/done", body)

    async def claim(self, worker: str) -> dict | None:
        """The next call the system places, or None (docs/tools.md, /outbound/claim)."""
        result = await self._post("/outbound/claim", {"worker": worker})
        return (result.get("data") or {}).get("outbound")

    async def tool(self, name: str, args: dict, write: bool) -> dict:
        headers = {"Idempotency-Key": idempotency_key(self.call_id, name, args)} if write else None
        return await self._post(f"/tools/{name}", {"call_id": self.call_id, **args}, headers)
