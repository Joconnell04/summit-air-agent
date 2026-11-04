"""Paid: one scripted booked call through the real tool server and the mock ServiceTitan, then the
technician's summary written by the real post_call.summary model (spec S-16). Prints the pinned
note, the lines the server dropped and the cost, and exits 1 if the summary didn't reach the job,
broke the length limit or cost more than BUDGET_USD.

The caller is a customer with a completed job last week and an office note asking the agent to skip
a gate, so the run shows recent work coming from the history and the note staying data.

Run: make summary-check   (needs ANTHROPIC_API_KEY in .env; one model call)
"""

import json
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import summary
from app.main import create_app
from app.settings import Settings
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world

ROOT = Path(__file__).resolve().parents[1]
TENANT_DIR = ROOT / "tenants" / "summit-air"
DIALED = "(646) 624-0418"
START = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
BUDGET_USD = 1.00


def main() -> int:
    model = summary.model_from_config()
    if model is None:
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix="summary-check-"))
    scenarios = build_world(work / "world.db", TENANT_DIR, START)
    known = scenarios["jobDoneLastWeek"]
    mock = create_mock(work / "world.db", TENANT_DIR, work / "world.log", clock=lambda: NOW)
    settings = Settings(
        db_path=work / "tool.db", tenants_dir=TENANT_DIR.parent, jobs_interval_seconds=0
    )
    app = create_app(settings, clock=lambda: NOW, fsm_clients={"summit-air": TestClient(mock)})
    client = TestClient(app)
    call = "summary-check-1"

    def turn(speaker: str, text: str) -> None:
        client.post(f"/calls/{call}/turn", json={"speaker": speaker, "text": text})

    def tool(name: str, **args) -> dict:
        return client.post(f"/tools/{name}", json={"call_id": call, **args}).json()

    started = client.post(
        "/calls/start", json={"call_id": call, "caller_id": known["phone"], "dialed_number": DIALED}
    ).json()
    location = started["data"]["customers"][0]["locations"][0]
    address = {k: location[k] for k in ("street", "unit", "city", "zip")}
    turn("agent", "Thanks for calling. Is this Thomas?")
    turn("caller", "Yes. Your guy fixed my AC last week, now the heat won't come on at all.")
    turn("agent", "Sorry to hear that. When did it stop?")
    turn("caller", "Since this morning. It's about 61 inside. I flipped the breaker, nothing.")
    turn("agent", "Got it. Anything the technician should know to get in?")
    turn("caller", "Use the side door, the front is stuck. The dog is friendly, I'll put him out back.")
    turn("caller", "How much is this going to cost me?")
    turn("agent", "The technician will look at it and go over any cost with you before doing work.")
    asked = tool("read_back", address=address, phone="caller_id")
    turn("agent", asked["say"])
    turn("caller", "Yes, that's right.")
    tool("read_back", address=address, phone="caller_id", confirmed=True)
    found = tool("find_slots", job_type="no_heat", facts={"issue": "Heat won't come on", "who_home": "Thomas"})
    day = found["data"]["days"][0]
    slot = {"date": day["date"], "window": day["windows"][0]}
    booked = tool(
        "book_appointment",
        job_type="no_heat",
        slot=slot,
        details={
            "issue": "Heat won't come on at all, AC was fixed last week",
            "since": "this morning",
            "tried": "Flipped the breaker",
            "who_home": "Thomas",
            "approver": "Thomas, the owner",
            "system_type": "",
            "inside_temp_f": 61,
            "arrival_notes": "Use the side door, front door is stuck. Friendly dog, put out back.",
            "name": known["name"],
        },
    )
    if not booked["ok"]:
        print(f"booking failed: {json.dumps(booked)}", file=sys.stderr)
        return 1
    turn("agent", booked["say"])
    client.post(f"/calls/{call}/end", json={"reason": "agent_end"})

    services = app.state.services
    conn = services.connect()
    try:
        counts = summary.run(
            conn, services.tenants.default, services.fsm["summit-air"], model, NOW
        )
        row = conn.execute("SELECT * FROM summaries WHERE call_id = ?", (call,)).fetchone()
    finally:
        conn.close()
    notes = services.fsm["summit-air"].job_notes(booked["data"]["booking_id"])
    pinned = [n["text"] for n in notes if n["isPinned"]]
    print(f"run: {counts}")
    print("\npinned note:\n" + (pinned[0] if pinned else "(none)"))
    print(f"\ndropped: {row['dropped_json'] if row else '-'}")
    print(f"cost: ${row['cost_usd'] or 0:.4f}" if row else "cost: -")
    print(f"error: {row['last_error']}" if row and row["last_error"] else "")
    lines = pinned[0].splitlines()[1:] if pinned else []
    ok = (
        counts["done"] == 1
        and len(pinned) == 1
        and 2 <= len(lines) <= len(summary.FIELDS)
        and all(len(line.split(": ", 1)[1]) <= summary.MAX_LINE_CHARS for line in lines)
        and (row["cost_usd"] or 0) <= BUDGET_USD
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
