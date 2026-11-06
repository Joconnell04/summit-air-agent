"""The real tool server in process, reached over HTTP the way the worker reaches it."""

import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world
from tests.tool_server.toolkit import START, TENANT_DIR, Agent, Clock, make_server
from voice.archive.livekit.server import ToolServer


@pytest.fixture(scope="session")
def seeded_world(tmp_path_factory):
    path = tmp_path_factory.mktemp("st") / "world.db"
    build_world(path, TENANT_DIR, START)
    return path


@pytest.fixture
def mock(seeded_world, tmp_path):
    clock = Clock()
    path = tmp_path / "world.db"
    shutil.copy(seeded_world, path)
    return create_mock(path, TENANT_DIR, tmp_path / "world.log", clock=clock), clock


@pytest.fixture
def app(mock, tmp_path):
    return make_server(tmp_path, mock[1], mock[0])


@pytest.fixture
def scenarios(mock):
    return TestClient(mock[0]).get("/debug/scenarios").json()


@pytest.fixture
def agent(app):
    """The test's own view of the server, for setup and for checking what was stored."""
    return Agent(app, app.state.services.clock)


@pytest.fixture
def connect(app):
    """A worker-side client for one call id, talking to the server over ASGI."""

    def make(call_id: str) -> ToolServer:
        return ToolServer("http://tool-server", call_id, transport=httpx.ASGITransport(app=app))

    return make
