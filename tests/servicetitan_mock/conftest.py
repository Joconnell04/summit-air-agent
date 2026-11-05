import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from servicetitan_mock import repo, worldkit
from servicetitan_mock.app import create_app
from servicetitan_mock.config import TenantConfig
from servicetitan_mock.db import connect, create_schema
from servicetitan_mock.seed import build_world

ROOT = Path(__file__).resolve().parents[2]
TENANT_DIR = ROOT / "tenants" / "summit-air"
START = date(2026, 11, 2)  # a Monday
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # 9:00 a.m. New York time
TOMORROW = date(2026, 11, 3)  # a Tuesday
SERVICE, INSTALL = 11, 12
NO_HEAT, NO_COOLING, TUNE_UP = 201, 202, 203
CFG = TenantConfig.load(TENANT_DIR)


class Api:
    """A TestClient that signs in and expands /crm/customers to /crm/v2/tenant/1000/customers."""

    def __init__(self, app, tenant_id: int = 1000):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.tenant_id = tenant_id
        auth = app.state.cfg.auth
        token = self.client.post(
            "/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": auth["client_id"],
                "client_secret": auth["client_secret"],
            },
        ).json()["access_token"]
        self.headers = {"Authorization": f"Bearer {token}", "ST-App-Key": auth["app_key"]}
        self.app = app

    def url(self, path: str) -> str:
        module, rest = path.lstrip("/").split("/", 1)
        return f"/{module}/v2/tenant/{self.tenant_id}/{rest}"

    def request(self, method: str, path: str, headers: dict | None = None, **kwargs):
        return self.client.request(
            method, self.url(path), headers={**self.headers, **(headers or {})}, **kwargs
        )

    def get(self, path: str, **params):
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None, key: str | None = None, **headers):
        extra = {"Idempotency-Key": key} if key else {}
        return self.request("POST", path, headers={**extra, **headers}, json=body)


def _z(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def window_times(day: date, window_id: str) -> dict:
    start, end = CFG.window_bounds(day, CFG.window(window_id))
    return {
        "start": _z(start),
        "end": _z(end),
        "arrivalWindowStart": _z(start),
        "arrivalWindowEnd": _z(end),
    }


def job_body(
    customer_id: int,
    location_id: int,
    day: date,
    window_id: str,
    job_type: int = TUNE_UP,
    unit: int = SERVICE,
    **extra,
) -> dict:
    return {
        "customerId": customer_id,
        "locationId": location_id,
        "businessUnitId": unit,
        "jobTypeId": job_type,
        "priority": "Normal",
        "campaignId": 301,
        "summary": "test",
        "appointments": [window_times(day, window_id)],
        **extra,
    }


def capacity_frame(
    api: Api, day: date, window_id: str, job_type: int = TUNE_UP, unit: int = SERVICE
) -> dict:
    """A window's capacity, from the 30-minute block frames capacity answers with: a technician is
    available when free in every block of it."""
    start, end = CFG.window_bounds(day, CFG.window(window_id))
    body = {
        "startsOnOrAfter": start.isoformat(),
        "endsOnOrBefore": end.isoformat(),
        "businessUnitIds": [unit],
        "jobTypeId": job_type,
        "skillBasedAvailability": True,
    }
    frames = api.post("/dispatch/capacity", body).json()["availabilities"]
    assert frames, body
    if len(frames) == 1:
        return frames[0]
    busy = {
        t["id"] for f in frames for t in f["technicians"] if t["status"] != "Available"
    }
    technicians = [
        {**t, "status": "Unavailable" if t["id"] in busy else "Available"}
        for t in frames[0]["technicians"]
    ]
    free = [t for t in technicians if t["status"] == "Available" and t["hasRequiredSkills"]]
    return {
        **frames[0],
        "end": frames[-1]["end"],
        "endUtc": frames[-1]["endUtc"],
        "technicians": technicians,
        "isAvailable": bool(free),
        "openAvailability": len(free),
    }


def open_slots(api: Api, day: date, window_id: str, job_type: int = TUNE_UP) -> int:
    """Technicians free in the window who have the skills for the job type."""
    frame = capacity_frame(api, day, window_id, job_type)
    return sum(t["status"] == "Available" and t["hasRequiredSkills"] for t in frame["technicians"])


def build_small_world(
    path: Path, techs: int = 3, skills=("maintenance", "heating", "cooling")
) -> dict:
    """A tiny company: a few technicians, three days of shifts, and one customer of each kind."""
    create_schema(path)
    conn = connect(path)
    conn.execute("BEGIN")
    for i in range(techs):
        worldkit.add_technician(conn, CFG, 7001 + i, f"Tech {i}", SERVICE, 1, list(skills))
    days = [date(2026, 11, d) for d in (3, 4, 5)]
    for day in days:
        for i in range(techs):
            worldkit.add_shift(conn, CFG, 7001 + i, day, "08:00", "20:00")
        worldkit.add_shift(conn, CFG, 7001, day, "20:00", "08:00", "OnCall", ends_next_day=True)
    ids = {}
    for key, name, kind, zip_code in (
        ("home", "Pat Home", "Residential", "11201"),
        ("commercial", "Corner Deli", "Commercial", "11201"),
        ("far", "Far Away", "Residential", "11501"),
    ):
        customer_id = repo.insert_customer(conn, NOW, name, kind)
        location_id = repo.insert_location(
            conn,
            CFG,
            NOW,
            customer_id,
            "1 Main St",
            {"street": "1 Main St", "city": "Brooklyn", "state": "NY", "zip": zip_code},
        )
        ids[key] = (customer_id, location_id)
    conn.execute("COMMIT")
    conn.close()
    return ids


@pytest.fixture
def small(tmp_path):
    """(api, ids) for a three-technician company: three technicians are free in every window."""
    ids = build_small_world(tmp_path / "small.db")
    app = create_app(tmp_path / "small.db", TENANT_DIR, tmp_path / "small.log", clock=lambda: NOW)
    return Api(app), ids


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("seed") / "world.db"
    build_world(path, TENANT_DIR, START)
    return path


@pytest.fixture
def world(seeded_db, tmp_path):
    """(api, scenarios) for the full Summit Air world, on a private copy so tests can write."""
    path = tmp_path / "world.db"
    shutil.copy(seeded_db, path)
    app = create_app(path, TENANT_DIR, tmp_path / "world.log", clock=lambda: NOW)
    api = Api(app)
    return api, api.client.get("/debug/scenarios").json()
