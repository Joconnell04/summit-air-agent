import shutil

import pytest
from fastapi.testclient import TestClient
from toolkit import START, TENANT_DIR, Agent, Clock, make_server

from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world


@pytest.fixture(scope="session")
def seeded_world(tmp_path_factory):
    path = tmp_path_factory.mktemp("st") / "world.db"
    scenarios = build_world(path, TENANT_DIR, START)
    return path, scenarios


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
