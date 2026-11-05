"""Fixtures for the Retell bridge tests: the real tool server on a scratch copy of the mock
ServiceTitan, the bridge in front of it, and a stand-in for Retell's API that records what the
bridge asked it to do."""

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tool_server"))

from retell_kit import KEY, NOW_MS, SPOKEN
from toolkit import START, TENANT_DIR, Clock, make_server

from app.tools import TOOLS
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world
from voice.retell.bridge import Bridge, ToolServer, build_app
from voice.retell.ledger import Ledger


class FakeRetell:
    """Records live-call control instead of calling Retell."""

    def __init__(self):
        self.said: list[tuple[str, str]] = []
        self.context: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.unrecorded: list[str] = []

    def say_next(self, call_id: str, context: str) -> None:
        self.said.append((call_id, context))

    def add_context(self, call_id: str, context: str) -> None:
        self.context.append((call_id, context))

    def stop_call(self, call_id: str) -> None:
        self.stopped.append(call_id)

    def keep_no_recording(self, call_id: str) -> None:
        self.unrecorded.append(call_id)


class Later:
    """Holds scheduled work until a test runs it."""

    def __init__(self):
        self.pending: list[tuple[float, object]] = []

    def __call__(self, delay, fn):
        self.pending.append((delay, fn))

    def run(self):
        pending, self.pending = self.pending, []
        for _, fn in pending:
            fn()


@pytest.fixture(scope="session")
def seeded_world(tmp_path_factory):
    path = tmp_path_factory.mktemp("st") / "world.db"
    build_world(path, TENANT_DIR, START)
    return path


@pytest.fixture
def tool_app(seeded_world, tmp_path):
    path = tmp_path / "world.db"
    shutil.copy(seeded_world, path)
    clock = Clock()
    mock = create_mock(path, TENANT_DIR, tmp_path / "world.log", clock=clock)
    return make_server(tmp_path, clock, mock)


@pytest.fixture
def retell():
    return FakeRetell()


@pytest.fixture
def later():
    return Later()


@pytest.fixture
def bridge(tool_app, tmp_path, retell, later):
    tools = ToolServer(TestClient(tool_app, raise_server_exceptions=False))
    return Bridge(
        KEY,
        tools,
        Ledger(tmp_path / "bridge.db"),
        retell,
        instructions=lambda start: "INSTRUCTIONS for " + start["tenant"]["name"],
        spoken=lambda slug: SPOKEN,
        notes=lambda hints: [f"[server] {h}" for h in hints if h in ("commercial", "do_not_call")],
        default_tenant="summit-air",
        tool_names=set(TOOLS),
        now_ms=lambda: NOW_MS,
        later=later,
    )


@pytest.fixture
def client(bridge):
    return TestClient(build_app(bridge))
