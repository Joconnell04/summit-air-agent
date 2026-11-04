import shutil

import httpx
import pytest
from fastapi.testclient import TestClient
from toolkit import START, TENANT_DIR, Agent, Clock, make_server

from app import accounts
from app.fsm import FsmUnavailable
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world


@pytest.fixture(scope="session")
def seeded_world(tmp_path_factory):
    path = tmp_path_factory.mktemp("st") / "world.db"
    scenarios = build_world(path, TENANT_DIR, START)
    return path, scenarios


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch):
    """scrypt at its real cost takes tens of milliseconds a hash; tests sign in hundreds of times.
    The cost travels in each stored hash, so checking still runs the same code."""
    monkeypatch.setattr(accounts, "SCRYPT_N", 2**4)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def mock(seeded_world, tmp_path, clock):
    """A private copy of the seeded mock ServiceTitan, served in process."""
    path = tmp_path / "world.db"
    shutil.copy(seeded_world[0], path)
    app = create_mock(path, TENANT_DIR, tmp_path / "world.log", clock=clock)
    scenarios = TestClient(app).get("/debug/scenarios").json()
    return app, scenarios


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


@pytest.fixture
def scenarios(mock):
    return mock[1]


class Down(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture
def fsm(agent):
    """The adapter, with a way to swap its HTTP client and put it back."""
    adapter = agent.app.state.services.fsm["summit-air"]
    original = adapter.http

    class Switch:
        def down(self):
            adapter.http = httpx.Client(transport=Down(), base_url="http://testserver")

        def losing_job_responses(self):
            """The job reaches the FSM, but the answer never comes back."""
            real = adapter.create_job

            def create_job(*args, **kwargs):
                real(*args, **kwargs)
                raise FsmUnavailable("create_job: ReadTimeout")

            adapter.create_job = create_job

        def up(self):
            adapter.http = original
            adapter.__dict__.pop("create_job", None)

    yield Switch()
    Switch().up()
