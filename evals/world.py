"""A scratch world per scenario: a fresh copy of the seeded mock ServiceTitan and the real tool
server on its own database, both on one clock that starts at the scenario's time.

Each scenario gets its own call id and its own databases, so nothing one call does can satisfy a
gate in the next (the earlier practice agent shared one call id and an early case passed a later
gate).
"""
import shutil
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world

ROOT = Path(__file__).resolve().parent.parent
TENANT_DIR = ROOT / "tenants" / "summit-air"
SEED_START = date(2026, 11, 2)
DIALED = "(646) 624-0418"
DEBUG_TOKEN = "eval-debug-token"
_seed_lock = threading.Lock()


class Clock:
    """Starts at the scenario's time and runs at real speed, so timings stay honest."""

    def __init__(self, start: datetime):
        self.start = start.astimezone(UTC)
        self.t0 = time.monotonic()

    def __call__(self) -> datetime:
        return self.start + timedelta(seconds=time.monotonic() - self.t0)


def seeded_db(cache_dir: Path) -> tuple[Path, dict]:
    """The seeded mock database, built once per cache directory."""
    path = cache_dir / "seed.db"
    with _seed_lock:
        if not path.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            build_world(path, TENANT_DIR, SEED_START)
    scenarios = TestClient(create_mock(path, TENANT_DIR, cache_dir / "seed.log")).get(
        "/debug/scenarios"
    ).json()
    return path, scenarios


class ToolServer:
    """Speaks the tool contract (docs/tools.md) over any httpx-style client."""

    def __init__(self, client, debug_token: str):
        self.client = client
        self.debug_token = debug_token

    def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        response = self.client.post(path, json=body, headers=headers or {})
        response.raise_for_status()
        return response.json()

    def start(self, call_id: str, caller_id: str, dialed: str = DIALED) -> dict:
        body = {"call_id": call_id, "caller_id": caller_id, "dialed_number": dialed}
        return self._post("/calls/start", body)

    def turn(self, call_id: str, speaker: str, text: str, timings: dict | None = None) -> dict:
        body = {"speaker": speaker, "text": text, "timings": timings or {}}
        return self._post(f"/calls/{call_id}/turn", body)

    def tool(self, name: str, call_id: str, args: dict, key: str | None = None) -> dict:
        headers = {"Idempotency-Key": key} if key else {}
        return self._post(f"/tools/{name}", {**args, "call_id": call_id}, headers)

    def end(self, call_id: str, reason: str) -> dict:
        return self._post(f"/calls/{call_id}/end", {"reason": reason})

    def trace(self, call_id: str) -> dict:
        response = self.client.get(
            f"/debug/calls/{call_id}", headers={"X-Debug-Token": self.debug_token}
        )
        response.raise_for_status()
        return response.json()

    def final_trace(self, call_id: str, wait_s: float = 30, every_s: float = 1) -> dict:
        """The call's record once the call has ended. On Retell the end arrives by webhook a
        moment after the chat ends; a call that never ends is returned as it is."""
        deadline = time.monotonic() + wait_s
        trace = self.trace(call_id)
        while trace["call"]["ended_at"] is None and time.monotonic() < deadline:
            time.sleep(every_s)
            trace = self.trace(call_id)
        return trace


class World:
    def __init__(self, workdir: Path, seed: tuple[Path, dict], start: datetime):
        workdir.mkdir(parents=True, exist_ok=True)
        st_db = workdir / "servicetitan.db"
        shutil.copy(seed[0], st_db)
        self.scenarios = seed[1]
        self.clock = Clock(start)
        mock = create_mock(st_db, TENANT_DIR, workdir / "servicetitan.log", clock=self.clock)
        settings = Settings(
            db_path=workdir / "tool_server.db",
            tenants_dir=TENANT_DIR.parent,
            debug_token=DEBUG_TOKEN,
            jobs_interval_seconds=0,
            environment="development",
        )
        fsm = TestClient(mock, raise_server_exceptions=False)
        app = create_app(settings, clock=self.clock, fsm_clients={"summit-air": fsm})
        self.server = ToolServer(TestClient(app, raise_server_exceptions=False), DEBUG_TOKEN)


def resolve_caller(caller_id: str, seed_scenarios: dict) -> str:
    """unknown, seed:<key>[.<field or index>], or a literal number."""
    if caller_id == "unknown":
        return "(212) 555-0999"
    if not caller_id.startswith("seed:"):
        return caller_id
    value = seed_scenarios
    for part in caller_id.removeprefix("seed:").split("."):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value["phone"] if isinstance(value, dict) else value
