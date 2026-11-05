"""Seed a day of calls for the owner view, through the real tool server and the mock ServiceTitan,
on a clock moved call by call. The calls are scripted, not a model: each one makes the tool calls a
voice layer would. A week of earlier bookings is seeded first so booked-and-kept has something to
read.

Run: make owner-demo   (seeds data/owner-demo/ and serves it on :8100; sign in as owner or staff)
Or:  uv run python -m scripts.seed_owner_day [--dir data/owner-demo] [--day YYYY-MM-DD]
"""

import argparse
import shutil
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import accounts, jobs, rules
from app.main import create_app
from app.settings import Settings
from evals import gate, results
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world

ROOT = Path(__file__).resolve().parents[1]
TENANT_DIR = ROOT / "tenants" / "summit-air"
DIALED = "(646) 624-0418"
# The real eval gate runs on owner drafts (paid, $0.39, $0.38 and $0.38 MEASURED): each draft and its
# result become a version on the Rules page, so the demo shows checked versions without a live run.
# Each entry is the gate run, its rerun of the scenarios that missed (None when nothing missed, or
# for the runs made before the gate had one), the note and the time it was saved. The third is the
# second's edit sent again after the must-pass fixes (evals/rounds/reliability-01.md): it was the
# scenarios and the prompt that failed, not the edit.
GATE_RUNS = [
    ("20261103-193944-livekit-fast-gate.json", None, "Ask about pets; clear a path", time(14, 10)),
    ("20261103-200110-livekit-fast-gate.json", None, "Clear a path only", time(15, 40)),
    ("20261103-225314-livekit-fast-gate.json", None, "Clear a path, checked again", time(16, 50)),
]
NEW_YORK = ZoneInfo("America/New_York")
DETAILS = {
    "since": "this morning",
    "tried": "Checked the thermostat and the breaker",
    "who_home": "Caller",
    "approver": "Caller, the owner",
    "system_type": "gas boiler",
    "arrival_notes": "",
}


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def at(self, day: date, hour: int, minute: int = 0) -> None:
        self.now = datetime.combine(day, time(hour, minute), NEW_YORK)


class Voice:
    """What a voice layer sends the tool server, without the voice."""

    def __init__(self, app, clock: Clock, mock: TestClient):
        self.client = TestClient(app)
        self.app = app
        self.clock = clock
        self.mock = mock  # the mock ServiceTitan, to log the calls its phone system would

    def start(self, call_id: str, caller: str, direction: str = "inbound") -> str:
        body = {"call_id": call_id, "caller_id": caller, "dialed_number": DIALED}
        body["direction"] = direction
        self.client.post("/calls/start", json=body)
        return call_id

    def caller(self, call_id: str, text: str) -> dict:
        # A few seconds a turn, so the call page reads as a conversation in time.
        self.clock.now += timedelta(seconds=6)
        return self.client.post(
            f"/calls/{call_id}/turn", json={"speaker": "caller", "text": text}
        ).json()

    def agent(self, call_id: str, text: str) -> None:
        self.clock.now += timedelta(seconds=9)
        self.client.post(f"/calls/{call_id}/turn", json={"speaker": "agent", "text": text})

    def tool(self, name: str, call_id: str, **args) -> dict:
        return self.client.post(f"/tools/{name}", json={"call_id": call_id, **args}).json()

    def end(self, call_id: str, reason: str = "agent_end") -> None:
        # Calls take a few minutes; the next call sets its own start time.
        self.clock.now += timedelta(minutes=2, seconds=40)
        self.client.post(f"/calls/{call_id}/end", json={"reason": reason})

    def read_back(self, call_id: str, street: str, zip_code: str) -> None:
        address = {"street": street, "unit": "", "city": "Brooklyn", "zip": zip_code}
        # The server acts only on a ZIP the caller said (app/tools.py _zip_known).
        self.caller(call_id, f"It's {street}, {zip_code}.")
        asked = self.tool("read_back", call_id, address=address, phone="caller_id")
        self.agent(call_id, asked["say"])
        self.caller(call_id, "Yes, that's right.")
        self.tool("read_back", call_id, address=address, phone="caller_id", confirmed=True)

    def book(self, call_id: str, job_type: str, slot: dict, issue: str, **details) -> dict:
        body = {**DETAILS, "issue": issue, "name": details.pop("name", "Pat Rivera"), **details}
        booked = self.tool("book_appointment", call_id, job_type=job_type, slot=slot, details=body)
        if booked["refused"] == "account_choice":
            # A demo caller on file booking a new address keeps it on their own account.
            booked = self.tool(
                "book_appointment",
                call_id,
                job_type=job_type,
                slot=slot,
                details=body,
                new_address_account="caller",
            )
        if not booked["ok"]:
            raise SystemExit(f"booking failed on {call_id}: {booked}")
        return booked

    def first_slot(self, call_id: str, job_type: str, **facts) -> dict:
        found = self.tool("find_slots", call_id, job_type=job_type, facts=facts)["data"]
        if "first_urgent" in found:
            first = found["first_urgent"]
            return {"date": first["date"], "window": first["window"]}
        day = found["days"][0]
        return {"date": day["date"], "window": day["windows"][0]}


def tune_up(voice: Voice, call_id: str, caller: str, street: str, zip_code: str) -> dict:
    voice.start(call_id, caller)
    voice.caller(call_id, "Hi, I'd like to book my annual tune-up.")
    voice.read_back(call_id, street, zip_code)
    booked = voice.book(call_id, "tune_up", voice.first_slot(call_id, "tune_up"), "Annual tune-up")
    voice.agent(call_id, "Anything else I can help with?")
    voice.caller(call_id, "No, that's it, thanks.")
    voice.end(call_id)
    return booked


def earlier_week(voice: Voice, clock: Clock, day: date) -> None:
    """Three tune-ups a day for the week before, one of them cancelled later, so booked-and-kept
    reads 30 days of real bookings."""
    for back in range(7, 0, -1):
        d = day - timedelta(days=back)
        for n in range(3):
            clock.at(d, 9 + n * 2)
            call = f"w{back}-{n}"
            phone, street = f"(212) 555-{back}{n}11", f"{back * 10 + n} Prospect Pl"
            booked = tune_up(voice, call, phone, street, "11238")
            if n == 2 and back % 3 == 0:
                clock.at(d, 18)
                again = voice.start(f"{call}-x", phone)
                voice.caller(again, "I need to cancel my tune-up, sorry.")
                voice.tool(
                    "cancel_appointment",
                    again,
                    booking_id=booked["data"]["booking_id"],
                    reason="Going out of town",
                )
                voice.end(again)
        clock.at(d + timedelta(days=1), 7)
        jobs.run_once(voice.app.state.services)


def today(voice: Voice, clock: Clock, day: date, gas_lines: str, co_lines: str) -> None:
    def at(hour: int, minute: int = 0) -> None:
        clock.at(day, hour, minute)

    # 7:42, before the office opens: an older parent with no heat. Flagged urgent and the on-call
    # person is emailed (held here: no mail server).
    at(7, 42)
    c = voice.start("c01", "(718) 555-0142")
    voice.caller(c, "Hi, I'm calling for my parent. Their heat's been out since last night.")
    voice.caller(c, "They're 84 years old and it's 52 degrees in their place.")
    voice.read_back(c, "412 Ovington Ave", "11209")
    slot = voice.first_slot(c, "no_heat", inside_temp_f=52)
    # The on-call technician is alerted before a tonight booking (journey 11, tools._on_call_first).
    voice.tool(
        "flag_person",
        c,
        reason="84-year-old at home, no heat, 52 F inside",
        priority="Urgent",
        callback_in_minutes=20,
    )
    voice.book(
        c, "no_heat", slot, "No heat since last night", inside_temp_f=52, who_home="Parent, 84"
    )
    voice.end(c)
    clock.at(day, 7, 43)
    jobs.run_once(voice.app.state.services)
    office_returned(voice, c)

    at(8, 5)
    tune_up(voice, "c02", "(718) 555-0198", "88 Ocean Pkwy", "11218")

    # 8:20: a gas smell. The lines are said, a person is flagged, the caller goes outside.
    at(8, 20)
    c = voice.start("c03", "(347) 555-0123")
    voice.caller(c, "My furnace is making a noise and honestly it smells a bit like gas in here.")
    voice.agent(c, gas_lines)
    voice.caller(c, "Okay, okay, I'm going outside now.")
    voice.end(c, "caller_hangup")

    at(8, 41)
    c = voice.start("c04", "(212) 555-0177")
    voice.caller(c, "I'm the property manager for an office building on Court Street.")
    voice.tool(
        "record_lead",
        c,
        kind="commercial",
        details={"contact_name": "Lee Park", "company": "Court St Offices", "need": "Rooftop unit"},
    )
    voice.end(c)

    # 9:03: the caller mentions a parent only after hearing the first opening. The raise stands and
    # the call page flags it for the dispatcher (DECISIONS.md 072).
    at(9, 3)
    c = voice.start("c05", "(718) 555-0109")
    voice.caller(c, "The radiators upstairs are barely warm.")
    voice.read_back(c, "61 Wilson Ave", "11237")
    voice.first_slot(c, "no_heat")
    voice.agent(c, "The first opening I have is tomorrow morning, between 8 and 12.")
    voice.caller(c, "Oh, I should say my dad lives with us, he's 79. Is there anything sooner?")
    voice.book(c, "no_heat", voice.first_slot(c, "no_heat"), "Upstairs radiators barely warm")
    voice.end(c)

    at(9, 30)
    c = voice.start("c06", "(646) 555-0150")
    voice.caller(c, "Hi, my boiler stopped. I'm on West 23rd Street in Manhattan, 10011.")
    voice.tool("check_service_area", c, zip="10011")
    voice.end(c)

    at(9, 55)
    tune_up(voice, "c07", "(718) 555-0133", "230 Clinton St", "11201")

    at(10, 2)
    c = voice.start("c08", "(718) 555-0161")
    voice.caller(c, "How does your membership work? I'd like to join.")
    voice.tool(
        "record_lead",
        c,
        kind="membership",
        details={
            "contact_name": "Dana Ruiz",
            "need": "Wants to join the plan",
            "callback_in_minutes": 90,
        },
    )
    voice.end(c)

    at(10, 20)
    c = voice.start("c09", "(347) 555-0188")
    voice.caller(
        c,
        "Your tech was here last week and the same noise is back. I want someone to make it right.",
    )
    voice.tool(
        "flag_person",
        c,
        reason="Complaint: noise back a week after repair, wants a manager",
        priority="High",
        callback_time="2:00 PM",
    )
    voice.end(c)

    # 11:15: an infant at home, booked tomorrow and flagged for an earlier slot.
    at(11, 15)
    c = voice.start("c10", "(718) 555-0124")
    voice.caller(c, "Our heat is out and we have a newborn baby at home.")
    voice.read_back(c, "15 Bay Ridge Pkwy", "11209")
    tomorrow = {"date": (day + timedelta(days=1)).isoformat(), "window": "0800-1000"}
    voice.book(c, "no_heat", tomorrow, "No heat, newborn at home", who_home="Parents and a newborn")
    voice.end(c)

    at(11, 40)
    c = voice.start("c11", "(917) 555-0145")
    voice.caller(c, "We're thinking about replacing our oil boiler with a heat pump.")
    voice.tool(
        "record_lead",
        c,
        kind="install",
        details={
            "contact_name": "Sam Lee",
            "need": "Heat pump to replace an oil boiler",
            "callback_in_minutes": 120,
        },
    )
    voice.end(c)

    at(11, 25)
    office_returned(voice, "c08")

    at(12, 10)
    c = voice.start("c12", "(201) 555-0110")
    voice.caller(c, "Do you come out to Hoboken, 07030?")
    voice.tool("check_service_area", c, zip="07030")
    voice.end(c)

    at(12, 35)
    tune_up(voice, "c13", "(718) 555-0172", "540 Church Ave", "11218")

    at(13, 5)
    c = voice.start("c14", "(212) 555-0101")
    voice.caller(c, "Sorry, I think I have the wrong number.")
    voice.tool("end_call", c, reason="wrong_number")
    voice.end(c)

    at(13, 30)
    office_returned(voice, "c09")

    at(13, 40)
    c = voice.start("c21", "(718) 555-0137")
    voice.caller(c, "My heat stopped. I need to talk to my partner before I book anything.")
    voice.read_back(c, "18 Avenue J", "11230")
    voice.first_slot(c, "no_heat")
    voice.end(c, "caller_hangup")

    # 14:10: the caller asks to be called back; the system places the call.
    at(14, 10)
    c = voice.start("c15", "(718) 555-0115")
    voice.caller(c, "I'm about to go into a meeting, can you call me back in 20 minutes?")
    voice.tool(
        "flag_person",
        c,
        reason="Caller asked for a call back about a tune-up",
        callback_in_minutes=20,
        system_calls_back=True,
    )
    voice.end(c)

    # 14:20: the earlier-slot job offers the newborn household an opening today; they take it.
    at(14, 20)
    jobs.run_once(voice.app.state.services)
    placed_call(voice, accept=True)

    at(14, 31)
    jobs.run_once(voice.app.state.services)
    placed_call(voice, accept=False)

    at(15, 0)
    c = voice.start("c16", "(718) 555-0190")
    voice.caller(c, "No heat since this morning, it's about 60 inside.")
    voice.read_back(c, "77 Kings Hwy", "11223")
    voice.book(c, "no_heat", voice.first_slot(c, "no_heat"), "No heat since this morning")
    voice.end(c)

    at(15, 30)
    c = voice.start("c17", "(646) 555-0166")
    voice.caller(c, "My AC unit is leaking, I'm in Chelsea, 10011.")
    voice.tool("check_service_area", c, zip="10011")
    voice.end(c)

    at(16, 10)
    c = voice.start("c18", "(347) 555-0139")
    voice.caller(c, "I have a question about my last bill.")
    voice.tool("flag_person", c, reason="Billing question on last invoice", callback_in_minutes=30)
    voice.end(c)

    # 16:30: a CO alarm. The lines are said; the caller insists on booking a check.
    at(16, 30)
    c = voice.start("c19", "(718) 555-0156")
    voice.caller(c, "The carbon monoxide alarm went off earlier but it stopped now.")
    voice.agent(c, co_lines)
    voice.caller(
        c, "We're outside and the fire department said it's fine. Please just book a check."
    )
    voice.read_back(c, "9 Marine Ave", "11209")
    voice.tool(
        "book_appointment",
        c,
        job_type="safety_check",
        slot=voice.first_slot(c, "safety_check"),
        details={
            **DETAILS,
            "issue": "CO alarm went off, fire department cleared",
            "name": "Chris Moy",
        },
    )
    voice.end(c)

    at(17, 15)
    tune_up(voice, "c20", "(917) 555-0181", "300 Smith St", "11231")
    jobs.run_once(voice.app.state.services)


def office_returned(voice: Voice, call_id: str) -> None:
    """A person at the office calls the customer back from ServiceTitan's phone system and tags the
    call "Callback resolved". The call lands in the call log, and the callbacks job marks the
    callback done from it (decision #26, docs/decisions/26-callback-done.md)."""
    services = voice.app.state.services
    conn = services.connect()
    callback = conn.execute("SELECT phone FROM callbacks WHERE call_id = ?", (call_id,)).fetchone()
    conn.close()
    tenant = services.tenants.default
    logged = voice.mock.post(
        "/debug/calls",
        json={
            "sid": f"office-{call_id}",
            "direction": "Outbound",
            "fromNumber": tenant.raw["company"]["phone"],
            "toNumber": callback["phone"],
            "receivedOn": voice.clock().isoformat(),
            "durationSeconds": 240,
            "tags": [tenant.fsm["callback_tags"]["resolved"]],
        },
    )
    assert logged.status_code == 200, logged.text
    conn = services.connect()
    try:
        jobs.confirm_callbacks(conn, tenant, services.fsm[tenant.slug], voice.clock())
    finally:
        conn.close()


def placed_call(voice: Voice, accept: bool) -> None:
    """The voice layer claiming the next outbound call and placing it."""
    claimed = voice.client.post("/outbound/claim", json={"worker": "seed"}).json()["data"][
        "outbound"
    ]
    if not claimed:
        return
    call = voice.start(claimed["call_id"], claimed["phone"], direction="outbound")
    voice.agent(call, claimed["opening_line"])
    if claimed["kind"] == "earlier_slot" and accept:
        voice.caller(call, "Yes please, today is much better.")
        voice.tool(
            "reschedule_appointment",
            call,
            booking_id=claimed["booking_id"],
            slot=claimed["offer"],
            reason="Earlier opening",
        )
    else:
        # The callback is about a tune-up: the person is confirmed by the read-back and the
        # booking is the reason handled, so the call ends resolved.
        voice.caller(call, "Thanks for calling back. I'd like to book my tune-up.")
        voice.read_back(call, "61 Wilson Ave", "11237")
        voice.book(call, "tune_up", voice.first_slot(call, "tune_up"), "Annual tune-up")
        voice.agent(call, "Anything else I can help with?")
        voice.caller(call, "No, that's it, thanks.")
    voice.end(call)
    voice.client.post(f"/outbound/{claimed['outbound_id']}/done", json={"answered": True})


def rule_history(app, clock: Clock, day: date) -> None:
    """The Rules page's versions: each draft a gate run tested, saved, sent to the gate and given
    that run's real result, through app/rules.py as the editor does it."""
    tenant = app.state.services.tenants.default
    conn = app.state.services.connect()
    try:
        for name, rerun_name, note, at in GATE_RUNS:
            run = results.load(ROOT / "evals" / "results" / name)
            rerun = results.load(ROOT / "evals" / "results" / rerun_name) if rerun_name else None
            clock.now = datetime.combine(day, at, NEW_YORK)
            # Replays drafts a real gate run already tested, so no model reads them again here.
            _draft, errors = rules.save_draft(
                conn, tenant, run.overlay, "Owner", note, clock(), lambda tenant, items: {}
            )
            assert not errors, errors
            clock.now += timedelta(minutes=2)
            checking = rules.request_publish(conn, tenant.slug, "Owner", clock())
            clock.now += timedelta(minutes=6)
            found = gate.summary(run, run.overlay, rerun)
            # Judged against the must-pass set the saved run covered, not today's: the set grows
            # as scenarios are added, and an old run can't have run a newer one.
            rules.record_gate(conn, checking["id"], found, clock(), len(run.scenarios))
    finally:
        conn.close()


# Development only: the demo database is thrown away on every seed and never deployed.
DEMO_USERS = (
    ("owner", "Pat Moreno", "admin", "dev-owner-password"),
    ("staff", "Sam Ortiz", "staff", "dev-staff-password"),
)


def demo_users(app, clock) -> None:
    """An admin and a staff user to sign in as (make owner-demo)."""
    conn = app.state.services.connect()
    try:
        for username, name, role, password in DEMO_USERS:
            accounts.add(conn, username, name, role, password, "Revin", clock())
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed a day of calls for the owner view.")
    parser.add_argument("--dir", default="data/owner-demo")
    parser.add_argument("--day", help="YYYY-MM-DD, default today in New York")
    args = parser.parse_args(argv)
    folder = Path(args.dir)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    day = date.fromisoformat(args.day) if args.day else datetime.now(NEW_YORK).date()

    world = folder / "world.db"
    build_world(world, TENANT_DIR, day - timedelta(days=8))
    clock = Clock(datetime.combine(day - timedelta(days=8), time(8), NEW_YORK))
    mock = create_mock(world, TENANT_DIR, folder / "world.log", clock=clock)
    settings = Settings(
        db_path=folder / "tool.db",
        tenants_dir=TENANT_DIR.parent,
        default_tenant="summit-air",
        jobs_interval_seconds=0,
    )
    mock_client = TestClient(mock)
    app = create_app(settings, clock=clock, fsm_clients={"summit-air": mock_client})
    tenant = app.state.services.tenants.default
    gas = " ".join(tenant.safety["kinds"]["gas"]["lines"].split())
    co = " ".join(tenant.safety["kinds"]["co"]["lines"].split())

    voice = Voice(app, clock, mock_client)
    earlier_week(voice, clock, day)
    today(voice, clock, day, gas, co)
    rule_history(app, clock, day)
    demo_users(app, clock)
    # make owner-demo serves the page as if it were this moment, so "now" sits on the board.
    evening = datetime.combine(day, time(17, 20), NEW_YORK)
    (folder / "now").write_text(evening.isoformat())
    print(f"seeded {day} into {folder}")


if __name__ == "__main__":
    main()
