"""Shared fakes for the audio-rig adapter tests: the real tool server and mock in process, a
listener that records what an adapter tells it, and a WebSocket that records what is sent."""

import asyncio
import json
import shutil
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.tools import TOOLS
from evals.world import ToolServer
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world
from tests.tool_server.toolkit import START, TENANT_DIR, Agent, Clock, make_server

TOKEN = "test-token"


def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("rig-st") / "world.db"
    build_world(path, TENANT_DIR, START)
    return path


def tool_app(seed_path, tmp_path):
    clock = Clock()
    path = tmp_path / "world.db"
    shutil.copy(seed_path, path)
    mock = create_mock(path, TENANT_DIR, tmp_path / "world.log", clock=clock)
    return make_server(tmp_path, clock, mock)


def world_for(app):
    """What the rig hands an adapter: an object whose `server` is the sync tool-server client."""
    return SimpleNamespace(server=ToolServer(TestClient(app, raise_server_exceptions=False), TOKEN))


def agent_for(app) -> Agent:
    return Agent(app, app.state.services.clock)


def turns(app, call_id: str) -> list[tuple[str, str]]:
    trace = world_for(app).server.trace(call_id)
    return [(e["speaker"], e["text"]) for e in trace["events"] if e["type"] == "turn"]


def tools_called(app, call_id: str) -> list[str]:
    trace = world_for(app).server.trace(call_id)
    return [e["tool"] for e in trace["events"] if e["type"] == "tool" and e["tool"] in TOOLS]


class FakeListener:
    def __init__(self):
        self.audio_bytes = 0
        self.said_lines: list[str] = []
        self.done_count = 0
        self.hung = False
        self.playout = SimpleNamespace(end=0.0)

    def clock(self) -> float:
        return 1.0

    def audio(self, pcm: bytes, rate: int, live: bool = False) -> None:
        self.audio_bytes += len(pcm)

    def said(self, text: str) -> None:
        self.said_lines.append(text)

    def done(self) -> None:
        self.done_count += 1

    def hung_up(self) -> None:
        self.hung = True


class FakeSocket:
    """Records what the adapter sends; yields the server messages it is fed."""

    def __init__(self, incoming: list[dict] | None = None):
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue = asyncio.Queue()
        for message in incoming or []:
            self.incoming.put_nowait(message)
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True
        await self.incoming.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == kind]
