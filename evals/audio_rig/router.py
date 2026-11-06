"""One local address for many scratch worlds. A voice program that runs outside the rig's process
(the LiveKit worker, the ElevenLabs brain) reaches the tool server by URL; this router sends each
request to the world built for its call, found from the call id in the path or the body, so every
scenario still gets its own fresh mock (evals/world.py).
"""

import asyncio
import json
import socket
import threading
import time
from typing import Self

import uvicorn
from fastapi import FastAPI, Request, Response

from evals.world import World


class Worlds:
    def __init__(self):
        self._by_call: dict[str, World] = {}
        self._lock = threading.Lock()

    def add(self, call_id: str, world: World) -> World:
        with self._lock:
            self._by_call[call_id] = world
        return world

    def get(self, call_id: str) -> World | None:
        with self._lock:
            return self._by_call.get(call_id)

    def drop(self, call_id: str) -> None:
        with self._lock:
            self._by_call.pop(call_id, None)


def call_id_of(path: str, body: dict) -> str:
    """`/calls/<id>/...` and `/debug/calls/<id>` carry it in the path; the rest in the body."""
    parts = path.strip("/").split("/")
    if parts[0] == "calls" and len(parts) > 2:
        return parts[1]
    if parts[:2] == ["debug", "calls"] and len(parts) > 2:
        return parts[2]
    return str(body.get("call_id", ""))


def build_app(worlds: Worlds) -> FastAPI:
    app = FastAPI()

    @app.post("/outbound/claim")
    def no_outbound() -> dict:
        # The rig places no callbacks; the worker's poller gets nothing to do.
        return {"ok": True, "say": "", "data": {"outbound": None}, "refused": None}

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request) -> Response:
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        world = worlds.get(call_id_of(path, body if isinstance(body, dict) else {}))
        if world is None:
            return Response(status_code=404, content=b'{"detail": "no world for this call"}')
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() in ("idempotency-key", "x-debug-token", "content-type")
        }
        client = world.server.client
        # The world's tool server is an in-process test client; run it off the event loop.
        sent = await asyncio.to_thread(
            client.request, request.method, "/" + path, content=raw, headers=headers
        )
        return Response(
            sent.content, status_code=sent.status_code, media_type=sent.headers.get("content-type")
        )

    return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Serving:
    """A FastAPI app on a local port in a background thread, for the length of a run."""

    def __init__(self, app, port: int | None = None):
        self.port = port or free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Self:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
