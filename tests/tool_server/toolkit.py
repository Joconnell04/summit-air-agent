"""Helpers for the tool server tests: a frozen clock and a client that speaks the contract."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import guidance_check
from app.main import create_app
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
TENANT_DIR = ROOT / "tenants" / "summit-air"
START = date(2026, 11, 2)  # a Monday
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # 9:00 a.m. New York time
DIALED = "(646) 624-0418"
UNKNOWN = "(212) 555-0999"


class FakeCheck:
    """The guidance check (app/guidance_check.py) without a model: refuses the texts in `refuse`
    with their reason, or every save while `down`, and keeps what it was asked."""

    def __init__(self, refuse: dict[str, str] | None = None):
        self.refuse = refuse or {}
        self.down = False
        self.asked: list[dict[str, str]] = []

    def __call__(self, tenant, items: dict[str, str]) -> dict[str, str]:
        self.asked.append(dict(items))
        if self.down:
            return {"content": guidance_check.UNAVAILABLE}
        return {field: self.refuse[text] for field, text in items.items() if text in self.refuse}


# For tests about versions rather than the check.
PASSES = FakeCheck()


class Clock:
    """One clock for the tool server and the mock, moved by tests."""

    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now += timedelta(**delta)


class Agent:
    """Calls the tool server the way a voice layer does."""

    def __init__(self, app, clock: Clock):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.app = app
        self.clock = clock
        self.calls = 0

    def start(
        self, caller_id: str = UNKNOWN, call_id: str | None = None, dialed: str = DIALED
    ) -> dict:
        self.calls += 1
        call_id = call_id or f"call-{self.calls}"
        body = {"call_id": call_id, "caller_id": caller_id, "dialed_number": dialed}
        response = self.client.post("/calls/start", json=body)
        assert response.status_code == 200
        return response.json()

    def turn(self, call_id: str, text: str, speaker: str = "caller", **timings) -> dict:
        body = {"speaker": speaker, "text": text, "timings": timings}
        response = self.client.post(f"/calls/{call_id}/turn", json=body)
        assert response.status_code == 200
        return response.json()

    def say(self, call_id: str, text: str) -> dict:
        return self.turn(call_id, text, speaker="agent")

    def tool(self, name: str, call_id: str, key: str | None = None, **args) -> dict:
        headers = {"Idempotency-Key": key} if key else {}
        response = self.client.post(
            f"/tools/{name}", json={"call_id": call_id, **args}, headers=headers
        )
        assert response.status_code == 200
        return response.json()

    def end(self, call_id: str, reason: str = "agent_end") -> dict:
        response = self.client.post(f"/calls/{call_id}/end", json={"reason": reason})
        assert response.status_code == 200
        return response.json()

    def db(self):
        return self.app.state.services.connect()

    def confirm_read_back(self, call_id: str, address: dict, phone: str = "caller_id") -> dict:
        """The two-step read-back with a caller yes in between."""
        asked = self.tool("read_back", call_id, address=address, phone=phone)
        assert asked["ok"], asked
        self.say(call_id, asked["say"])
        self.turn(call_id, "Yes, that's right.")
        confirmed = self.tool("read_back", call_id, address=address, phone=phone, confirmed=True)
        assert confirmed["ok"], confirmed
        return confirmed


DETAILS = {
    "issue": "No heat since last night, radiators are cold",
    "since": "last night",
    "tried": "Checked the thermostat",
    "who_home": "Caller",
    "approver": "Caller, the owner",
    "system_type": "gas boiler",
    "arrival_notes": "",
    "name": "Pat Caller",
}


def in_area_address(n: int = 1) -> dict:
    return {"street": f"{100 + n} Union St", "unit": "", "city": "Brooklyn", "zip": "11231"}


def ready_to_book(agent: Agent, caller_id: str = UNKNOWN, address: dict | None = None) -> str:
    """A call that has described the problem and confirmed the read-back."""
    call_id = agent.start(caller_id)["data"]["call_id"]
    agent.turn(call_id, "Hi, the heat's out in my house.")
    agent.confirm_read_back(call_id, address or in_area_address())
    return call_id


def book(agent: Agent, call_id: str, slot: dict, job_type: str = "tune_up", key=None, **details):
    return agent.tool(
        "book_appointment",
        call_id,
        key=key,
        job_type=job_type,
        slot=slot,
        details={**DETAILS, **details},
    )


def first_open(agent: Agent, call_id: str, job_type: str = "tune_up") -> dict:
    found = agent.tool("find_slots", call_id, job_type=job_type)
    day = found["data"]["days"][0]
    return {"date": day["date"], "window": day["windows"][0]}


def count(agent: Agent, sql: str, *params) -> int:
    conn = agent.db()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def make_server(tmp_path, clock, mock_app, side_model=None, guidance=None, **settings):
    """The tool server on a scratch database, talking to the in-process mock, with a guidance
    check that passes everything unless the test gives its own."""
    config = Settings(
        db_path=tmp_path / "tool.db",
        tenants_dir=TENANT_DIR.parent,
        debug_token="test-token",
        jobs_interval_seconds=0,
        **settings,
    )
    fsm = TestClient(mock_app, raise_server_exceptions=False)
    return create_app(config, clock=clock, fsm_clients={"summit-air": fsm}, side_model=side_model,
                      guidance_check=guidance or FakeCheck())
