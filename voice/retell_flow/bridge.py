"""The bridge for the Retell Conversation Flow agent: the single-prompt bridge (voice/retell/bridge.py)
plus the endpoints the flow's custom functions call. It holds no rules. It turns the tool server's
answers into the dynamic variables the flow's edges read (voice/retell_flow/build.py):

- `route`: where the call goes next. `safety`, `line`, `end_line`, `end`, `handoff` and `transfer`
  each lead to one node; `model` leaves the call with the model.
- `line`: the server's sentence for a static-sentence node, word for word.
- `stage`, `notes`: the server's call stage and this turn's [server] notes (turn check only).
- `transfer_number`, `transfer_id`, `handoff_id`, `transfer_summary`: what a transfer node dials and
  what the bridge reports back when nobody answers.

Every answer carries every routing field, so a value left from an earlier answer never fires an
edge. Which sentences the server speaks itself is `prompts/assemble.spoken_by_server`, the same
rule our worker and the eval harness use.

Run: uvicorn voice.retell_flow.bridge:create_app --factory --port 8102 (it serves both agents)
"""

import json
import logging
import os
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.tools import TOOLS
from prompts.assemble import instructions, server_notes, spoken_by_server
from voice.retell import conversation
from voice.retell.api import Retell
from voice.retell.bridge import Bridge, ToolServer, build_app, compact
from voice.retell.ledger import Ledger
from voice.retell.transcript import lines, retell_id

log = logging.getLogger("retell.flow")

SAFETY_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
ROUTING = (
    "route",
    "line",
    "transfer_number",
    "transfer_id",
    "transfer_summary",
    "handoff_id",
)


def routed(route: str = "model", line: str = "", **extra) -> dict:
    """Every routing field, so the flow never reads a stale one."""
    out = {key: "" for key in ROUTING}
    out.update(route=route, line=line)
    out.update({k: str(v) for k, v in extra.items()})
    return out


class FlowBridge(Bridge):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Retell call id -> ("handoff" or "transfer", our id), for the transfer being dialed. Only
        # the outcome report needs it; a restart mid-transfer leaves it to the server's own timeout.
        self.dialing: dict[str, tuple[str, str]] = {}
        self.lock = threading.Lock()

    # ---- the turn check --------------------------------------------------------------------

    def turn_check(self, body: dict) -> dict:
        """Post the lines not yet posted, the caller's last among them, and say where the call goes:
        the server's hand-off, its safety lines, a line it wants said, or the stage it puts the call
        in with this turn's notes."""
        kind = "chat" if "chat" in body else "call"
        obj = body.get(kind) or {}
        rid = retell_id(obj)
        call_id = self.ensure_started(obj, kind)
        posted = self.ledger.sync(call_id, lines(obj), self.tools.turn, True)
        caller = [answer for speaker, answer in posted if speaker == "caller"]
        if not caller:
            return routed(stage="opening", notes="")
        hints, known, stage = [], {}, "opening"
        safety_text = ""
        for answer in caller:
            data = answer.get("data") or {}
            hints += data.get("hints") or []
            known = data.get("known") or known
            stage = data.get("stage") or stage
            safety = data.get("safety") or {}
            if safety.get("action") in SAFETY_ACTIONS and safety.get("text"):
                safety_text = safety["text"]
        last = caller[-1]
        data = last.get("data") or {}
        notes = "\n".join(server_notes(hints, known))
        handoff = data.get("handoff")
        if handoff and last.get("say"):
            return self._handoff(kind, rid, call_id, last["say"], handoff) | {
                "stage": stage, "notes": notes
            }
        if safety_text:
            return routed("safety", safety_text, stage=stage, notes=notes)
        recording = data.get("recording") or {}
        if recording.get("action") == "stop" and recording.get("text"):
            # Retell's own recording can't be stopped from here (exploration 017); the line is said.
            return routed("line", recording["text"], stage=stage, notes=notes)
        return routed(stage=stage, notes=notes)

    def _handoff(self, kind: str, rid: str, call_id: str, say: str, handoff: dict) -> dict:
        """A caller who demanded a person (app/tools.demand_handoff). `say` is the hand-off line when
        the server has a number to send the call to, otherwise the stored callback line."""
        plan = handoff.get("transfer")
        if not plan:
            return routed("line", say)
        if kind == "chat":
            # A chat can't transfer. The eval harness reports the transfer as sent, as its phone
            # company does for our stack (evals/stacks.py), and the chat ends on the line.
            self.tools.handoff_done(call_id, plan["id"], "handed_off")
            return routed("end_line", say)
        with self.lock:
            self.dialing[rid] = ("handoff", str(plan["id"]))
        return routed(
            "handoff", say, transfer_number=plan["phone"], handoff_id=plan["id"]
        )

    # ---- model tools -----------------------------------------------------------------------

    def flow_tool(self, name: str, body: dict) -> dict:
        kind = "chat" if "chat" in body else "call"
        obj = body.get(kind) or {}
        rid = retell_id(obj)
        call_id = self.ensure_started(obj, kind)
        _, notes = self.sync(obj, call_id)
        envelope = self.tools.tool(call_id, name, body.get("args") or {})
        answer = compact(envelope)
        if notes:
            answer["server_notes"] = notes
        data = envelope.get("data") or {}
        line = spoken_by_server(name, envelope) or ""
        if name == "end_call" and envelope.get("ok") and data.get("hang_up"):
            return answer | routed("end_line" if line else "end", line)
        if name == "flag_person" and envelope.get("ok") and kind == "call":
            put_through = self._offer_transfer(rid, call_id, envelope)
            if put_through:
                return answer | put_through
        return answer | routed("line" if line else "model", line)

    def _offer_transfer(self, rid: str, call_id: str, flagged: dict) -> dict | None:
        """After a person flag, the server says whether the call goes to a person now (app/transfer.py
        plan); nothing is dialed unless it sends a plan."""
        data = flagged["data"]
        asked = self.tools.transfer(call_id, data["flag_id"], data.get("callback_id"))
        plan = ((asked or {}).get("data") or {}).get("transfer") if asked and asked["ok"] else None
        if not plan or not plan.get("phone"):
            return None
        with self.lock:
            self.dialing[rid] = ("transfer", str(plan["id"]))
        return routed(
            "transfer",
            asked["say"],
            transfer_number=plan["phone"],
            transfer_id=plan["id"],
            transfer_summary=plan.get("summary") or "",
        )

    # ---- transfers -------------------------------------------------------------------------

    def transfer_failed(self, kind: str, body: dict) -> dict:
        """The transfer node's failed edge: tell the server, and say the callback line it returns."""
        obj = body.get("call") or {}
        rid = retell_id(obj)
        call_id = self.ensure_started(obj, "call")
        key = "handoff_id" if kind == "handoff" else "transfer_id"
        our_id = (body.get("args") or {}).get(key) or ""
        with self.lock:
            self.dialing.pop(rid, None)
        if not our_id.isdigit():
            return routed()
        if kind == "handoff":
            done = self.tools.handoff_done(call_id, int(our_id), "failed")
        else:
            done = self.tools.transfer_done(call_id, int(our_id), "no_answer")
        say = (done or {}).get("say") or ""
        return routed("line" if say else "model", say)

    def webhook(self, event: dict) -> bool:
        """The single-prompt bridge's events, plus the outcome of a transfer that went through."""
        call = event.get("call") or {}
        rid = retell_id(call)
        if event.get("event") == "call_ended" and rid:
            with self.lock:
                dialing = self.dialing.pop(rid, None)
            if dialing and call.get("disconnection_reason") == "call_transfer":
                call_id = self.ensure_started(call, "call")
                kind, our_id = dialing
                if kind == "handoff":
                    self.tools.handoff_done(call_id, int(our_id), "handed_off")
                else:
                    self.tools.transfer_done(call_id, int(our_id), "connected")
        return super().webhook(event)


class FlowToolServer(ToolServer):
    """The tool server calls only the flow needs, beside the single-prompt ones."""

    def transfer(self, call_id: str, flag_id: int, callback_id: int | None) -> dict | None:
        return self._post(
            f"/calls/{call_id}/transfer", {"flag_id": flag_id, "callback_id": callback_id}
        )

    def transfer_done(self, call_id: str, transfer_id: int, outcome: str) -> dict | None:
        return self._post(f"/calls/{call_id}/transfer/{transfer_id}/done", {"outcome": outcome})

    def handoff_done(self, call_id: str, handoff_id: int, outcome: str) -> dict | None:
        return self._post(f"/calls/{call_id}/handoff/{handoff_id}/done", {"outcome": outcome})


def build_flow_app(bridge: FlowBridge) -> FastAPI:
    """The single-prompt bridge's routes, which still serve the baseline agent, and the flow's."""
    app = build_app(bridge)

    async def signed(request: Request) -> dict | None:
        raw = await request.body()
        if not bridge.verified(raw, request.headers.get("x-retell-signature")):
            return None
        try:
            body = json.loads(raw)
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    @app.post("/retell/flow/turn")
    async def turn(request: Request):
        body = await signed(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        return await run_in_threadpool(bridge.turn_check, body)

    @app.post("/retell/flow/tools/{name}")
    async def tool(request: Request, name: str):
        body = await signed(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        if name not in bridge.tool_names:
            return JSONResponse({"detail": f"no tool {name}"}, 404)
        return await run_in_threadpool(bridge.flow_tool, name, body)

    @app.post("/retell/flow/handoff-failed")
    async def handoff_failed(request: Request):
        body = await signed(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        return await run_in_threadpool(bridge.transfer_failed, "handoff", body)

    @app.post("/retell/flow/transfer-failed")
    async def transfer_failed(request: Request):
        body = await signed(request)
        if body is None:
            return JSONResponse({"detail": "bad signature"}, 401)
        return await run_in_threadpool(bridge.transfer_failed, "transfer", body)

    return app


def create_app() -> FastAPI:
    api_key = os.environ["RETELL_API_KEY"]
    tool_server = os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8100")
    tenants_dir = Path(os.environ.get("TENANTS_DIR", "tenants"))
    ledger = Ledger(Path(os.environ.get("RETELL_BRIDGE_DB", "data/retell_bridge.db")))
    tools = FlowToolServer(httpx.Client(base_url=tool_server, timeout=10.0))
    bridge = FlowBridge(
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
    return build_flow_app(bridge)
