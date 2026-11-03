"""POST /debug/reset: a fresh world for the eval instance, refused everywhere else."""

import shutil
from datetime import UTC, datetime

import pytest
from conftest import TENANT_DIR, Api

from servicetitan_mock.app import create_app

LATER = datetime(2026, 11, 5, 14, 0, tzinfo=UTC)  # not the day the fixture world starts on


@pytest.fixture
def resettable(seeded_db, tmp_path):
    def build(**options):
        path = tmp_path / "world.db"
        shutil.copy(seeded_db, path)
        app = create_app(path, TENANT_DIR, tmp_path / "world.log", clock=lambda: LATER, **options)
        api = Api(app)
        return api, api.client.get("/debug/scenarios").json()

    return build


def job_status(api: Api, job_id: int) -> str:
    return api.get(f"/jpm/jobs/{job_id}").json()["jobStatus"]


def cancel(api: Api, job_id: int) -> None:
    response = api.request("PUT", f"/jpm/jobs/{job_id}/cancel", json={"reasonId": 401})
    assert response.status_code == 200


def test_reset_is_refused_when_the_flag_is_off(resettable):
    api, scenarios = resettable(allow_reset=False)
    job_id = scenarios["repeatCallerOpenJob"]["jobId"]
    cancel(api, job_id)
    response = api.client.post("/debug/reset")
    assert response.status_code == 403
    assert response.json()["code"] == "reset_disabled"
    assert job_status(api, job_id) == "Canceled"  # nothing was touched


def test_reset_is_off_unless_the_environment_flag_says_one(resettable, monkeypatch):
    monkeypatch.delenv("ST_MOCK_ALLOW_RESET", raising=False)
    api, _ = resettable()
    assert api.client.post("/debug/reset").status_code == 403
    monkeypatch.setenv("ST_MOCK_ALLOW_RESET", "true")  # only "1" turns it on
    api, _ = resettable()
    assert api.client.post("/debug/reset").status_code == 403
    monkeypatch.setenv("ST_MOCK_ALLOW_RESET", "1")
    api, _ = resettable()
    assert api.client.post("/debug/reset").status_code == 200


def test_reset_needs_the_token_when_one_is_set(resettable):
    api, scenarios = resettable(allow_reset=True, reset_token="tok")
    job_id = scenarios["repeatCallerOpenJob"]["jobId"]
    cancel(api, job_id)
    for headers in ({}, {"X-Debug-Token": "wrong"}):
        response = api.client.post("/debug/reset", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "reset_forbidden"
    assert job_status(api, job_id) == "Canceled"
    assert api.client.post("/debug/reset", headers={"X-Debug-Token": "tok"}).status_code == 200


def test_reset_brings_back_the_seeded_world(resettable):
    api, scenarios = resettable(allow_reset=True)
    job_id = scenarios["repeatCallerOpenJob"]["jobId"]
    before = job_status(api, job_id)
    cancel(api, job_id)
    assert job_status(api, job_id) == "Canceled"
    response = api.client.post("/debug/reset")
    assert response.status_code == 200
    assert response.json()["reset"] is True
    assert job_status(api, job_id) == before
    # The world is rebuilt from today on the mock's clock, and the same API keeps working.
    assert api.client.get("/debug/scenarios").json()["startDate"] == LATER.date().isoformat()
    assert api.get("/crm/customers", pageSize=1).json()["totalCount"] == 300


def test_reset_can_run_twice_in_a_row(resettable, tmp_path):
    api, _ = resettable(allow_reset=True)
    assert api.client.post("/debug/reset").status_code == 200
    assert api.client.post("/debug/reset").status_code == 200
    assert not list(tmp_path.glob("world.db.fresh*"))  # the scratch copy is cleaned up
