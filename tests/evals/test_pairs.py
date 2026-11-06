"""Two calls at once on one world (evals/pairs.py), with scripted agents against the real tool server
and mock, so the race and the checks that read ServiceTitan are tested offline."""

import json
import sqlite3
import threading
from datetime import date

import pytest
from fakes import ScriptedModel, grade_reply, say, use

from app import capacity as app_capacity
from app.fsm.servicetitan import ServiceTitanAdapter
from evals import pairs
from evals.fingerprint import run_hash
from evals.run import Harness, Models, main
from evals.scenario import ScenarioError
from evals.world import World, seeded_db

LAST_SLOT = pairs.load(pairs.PAIRS_DIR / "two-callers-last-slot.yaml")
SAME_TECH = pairs.load(pairs.PAIRS_DIR / "two-callers-same-tech.yaml")
TODAY = date(2026, 11, 2)
AFTERNOON = {"date": "2026-11-02", "window": "1200-1400"}
DETAILS = {
    "issue": "no heat",
    "since": "this morning",
    "tried": "the thermostat",
    "who_home": "two healthy adults",
    "approver": "the caller",
    "inside_temp_f": 63,
}


@pytest.fixture(scope="module")
def seed_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("pairs")


@pytest.fixture
def picked_together(monkeypatch):
    """The first two jobs sent to ServiceTitan wait for each other, so both calls have read
    capacity and picked a technician before either job is written. Without this, a busy machine
    can let one call finish booking before the other looks, and the tool server's own check
    catches the loser instead of the mock: still correct, but not the race these tests are about."""
    barrier = threading.Barrier(2, timeout=60)
    lock = threading.Lock()
    sent = []
    create_job = ServiceTitanAdapter.create_job

    def held(self, *args, **kwargs):
        with lock:
            sent.append(1)
            first_two = len(sent) <= 2
        if first_two:
            barrier.wait()
        return create_job(self, *args, **kwargs)

    monkeypatch.setattr(ServiceTitanAdapter, "create_job", held)


def harness(seed_dir, tmp_path):
    grader = ScriptedModel([grade_reply(), grade_reply()])
    h = Harness(Models(ScriptedModel([]), ScriptedModel([]), grader), "livekit", seed_dir)
    h.workdir = tmp_path
    return h


def booking_agent(name: str, street: str, zip_code: str) -> ScriptedModel:
    """Reads back, books this afternoon, and if that window is taken offers and books the first
    alternative the server gave. The read-back and a booking that goes through are spoken from the
    server's sentence with no model step (eval round 4), so the script only answers the rest."""
    address = {"street": street, "unit": "", "city": "Brooklyn", "zip": zip_code}
    state = {}

    def next_step(messages):
        last = messages[-1]["content"]
        if isinstance(last, list) and last and last[-1].get("type") == "tool_result":
            result = json.loads(last[-1]["content"])
            alt = state["alternative"] = result["data"]["alternatives"][0]
            return say(
                f"{result['say']} I have {alt['day']} {alt['label']} instead. Does that work?"
            )
        booked = any(
            m["role"] == "assistant"
            and isinstance(m["content"], str)
            and m["content"].startswith("You're all set")
            for m in messages
        )
        if booked:
            return say("Great. Goodbye.")
        alt = state["alternative"]
        slot = {"date": alt["date"], "window": alt["window"]}
        return use(
            "book_appointment", job_type="no_heat", slot=slot, details={**DETAILS, "name": name}
        )

    return ScriptedModel(
        [
            use("read_back", address=address, phone="caller_id"),
            use("read_back", address=address, phone="caller_id", confirmed=True),
            use(
                "book_appointment",
                "Perfect, booking that now.",
                job_type="no_heat",
                slot=AFTERNOON,
                details={**DETAILS, "name": name},
            ),
            next_step,
            next_step,
            next_step,
        ]
    )


def agents():
    return [
        booking_agent("Janelle Brooks", "8410 Ridge Blvd", "11209"),
        booking_agent("Omar Haddad", "412 Avenue C", "11218"),
    ]


# Each caller gives their ZIP before the read-back, since the server acts only on a ZIP the caller
# said (app/tools.py _zip_known), and their name, so the read-back doesn't ask for it.
CALLERS = [
    [
        "Hi, this is Janelle Brooks, my heat is out at 8410 Ridge Blvd, 11209.",
        "Yes, that's right.",
        "Okay, that works.",
    ],
    [
        "Hi, this is Omar Haddad, my heat is out at 412 Avenue C, 11218.",
        "Yes, that's right.",
        "Okay, that works.",
    ],
]


def test_every_pair_file_loads_and_leaves_only_its_open_windows(seed_dir, tmp_path):
    for pair in pairs.load_all():
        world = World(
            tmp_path / pair.id, seeded_db(seed_dir / "seed"), pair.scenarios()[0].start_time
        )
        pairs.fill(world.st_db, pair.world)
        call = world.server.start(f"probe-{pair.id}", "(332) 555-0199")["data"]["call_id"]
        found = world.server.tool(
            "find_slots",
            call,
            {
                "job_type": pair.world.job_type,
                "facts": {
                    "issue": "no heat",
                    "who_home": "two healthy adults",
                    "inside_temp_f": 63,
                },
            },
        )
        days = {d["date"]: d["windows"] for d in found["data"]["days"]}
        kept = {}
        for k in pair.world.keep_open:
            kept.setdefault(k.date.isoformat(), []).append(k.window)
        for day in pair.world.fill:
            assert days.get(day.isoformat(), []) == kept.get(day.isoformat(), []), pair.id
        assert len(found["data"]["days"]) > 1, f"{pair.id}: a later opening is there to offer"


def test_a_pair_makes_two_scenarios_that_share_its_checks_and_script():
    a, b = LAST_SLOT.scenarios()
    assert (a.id, b.id) == ("two-callers-last-slot-a", "two-callers-last-slot-b")
    assert a.must == b.must == LAST_SLOT.must
    assert a.caller_id != b.caller_id
    assert a.script == LAST_SLOT.script


def test_an_unknown_pair_check_is_refused(tmp_path):
    bad = LAST_SLOT.model_dump(mode="json")
    bad["together"] = [{"nobody_waits": True}]
    path = tmp_path / "two-callers-last-slot.yaml"
    path.write_text(json.dumps(bad))
    with pytest.raises(ScenarioError, match="unknown pair check"):
        pairs.load(path)


def test_the_meeting_lets_both_go_together_and_a_call_that_leaves_frees_the_other():
    meeting = pairs.Meeting(2, timeout_s=60)
    threads = [threading.Thread(target=meeting.wait) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert meeting.met
    meeting.leave()
    assert not meeting.barrier.broken, "leaving after a meeting breaks nothing"

    alone = pairs.Meeting(2, timeout_s=60)
    waiter = threading.Thread(target=alone.wait)
    waiter.start()
    alone.leave()
    waiter.join(60)
    assert not waiter.is_alive()
    assert not alone.met


def test_only_the_first_booking_after_a_confirmed_read_back_is_held():
    held_at = []

    class Server:
        def tool(self, name, call_id, args, key=None):
            return {"ok": True}

    class Meeting:
        def wait(self):
            held_at.append(len(calls))

    calls = []
    server = pairs.HeldServer(Server(), Meeting())
    for name, args in [
        ("book_appointment", {}),
        ("read_back", {}),
        ("read_back", {"confirmed": True}),
        ("find_slots", {}),
        ("book_appointment", {}),
        ("book_appointment", {}),
    ]:
        calls.append(name)
        server.tool(name, "c", args)
    assert held_at == [5], "held once, at the first booking after the confirmation"


def test_two_callers_for_the_last_slot_one_gets_it_the_other_is_told_and_books_the_next(
    seed_dir, tmp_path, capsys, picked_together
):
    results = pairs.run_pair(
        harness(seed_dir, tmp_path), LAST_SLOT, caller_turns=CALLERS, agent_models=agents()
    )
    assert [r.outcome for r in results] == ["pass", "pass"], [r.check_failures for r in results]
    assert {"one_job_per_technician_window", "window_jobs", "slot_taken"} <= set(results[0].checks)
    said = [" ".join(t.text for t in r.transcript if t.role == "agent") for r in results]
    assert sum("just filled up" in s for s in said) == 1
    assert "met at the booking True, mock answered slot_taken 1 time(s)" in capsys.readouterr().out


def test_two_callers_with_two_technicians_free_both_get_the_window(
    seed_dir, tmp_path, monkeypatch, capsys, picked_together
):
    # Both reach for the same technician, so the loser has to look again and take the other one.
    monkeypatch.setattr(app_capacity.random, "choice", lambda technicians: technicians[0])
    results = pairs.run_pair(
        harness(seed_dir, tmp_path),
        SAME_TECH,
        caller_turns=CALLERS_SAME_ZIP,
        agent_models=agents_same_zip(),
    )
    assert [r.outcome for r in results] == ["pass", "pass"], [r.check_failures for r in results]
    assert "mock answered slot_taken 1 time(s)" in capsys.readouterr().out


CALLERS_SAME_ZIP = [
    ["Hi, my heat is out at 220 Beverley Rd, 11218.", "Yes, that's right.", "Okay, that works."],
    ["Hi, my heat is out at 515 E 5th St, 11218.", "Yes, that's right.", "Okay, that works."],
]


def agents_same_zip():
    return [
        booking_agent("Priya Shah", "220 Beverley Rd", "11218"),
        booking_agent("Daniel Okafor", "515 E 5th St", "11218"),
    ]


def test_with_no_lock_and_no_constraint_the_pair_checks_catch_the_double_booking(
    seed_dir, tmp_path, monkeypatch, picked_together
):
    """The self-check: ServiceTitan's capacity check sees no one booked and the one-job rule is
    gone, so both callers get the same technician, and the checks that read the mock fail."""
    real_fill = pairs.fill

    def broken_fill(st_db, setup):
        monkeypatch.setattr("servicetitan_mock.capacity.slots", real_slots)
        real_fill(st_db, setup)
        conn = sqlite3.connect(st_db)
        conn.execute("DROP INDEX ux_appointments_slot")
        conn.close()
        monkeypatch.setattr("servicetitan_mock.capacity.slots", nobody_busy)

    def nobody_busy(*args, **kwargs):
        found = real_slots(*args, **kwargs)
        found.busy = set()
        return found

    real_slots = pairs.capacity.slots
    monkeypatch.setattr(pairs, "fill", broken_fill)
    results = pairs.run_pair(
        harness(seed_dir, tmp_path), LAST_SLOT, caller_turns=CALLERS, agent_models=agents()
    )
    assert [r.outcome for r in results] == ["fail", "fail"]
    failed = results[0].check_failures
    assert any(f.startswith("one_job_per_technician_window: technician") for f in failed), failed
    assert (
        "window_jobs: 2 of the calls' jobs are in the 1200-1400 window on 2026-11-02, "
        "expected 1" in failed
    )
    assert results[0].must_pass_failures, "S-27 is a must-pass line"


def test_a_failed_pair_check_fails_both_calls_but_a_broken_harness_stays_an_error(
    seed_dir, tmp_path
):
    results = pairs.run_pair(
        harness(seed_dir, tmp_path), LAST_SLOT, caller_turns=CALLERS, agent_models=agents()
    )
    scenario = LAST_SLOT.scenarios()[0]
    failed = pairs.with_pair_checks(results[0], scenario, LAST_SLOT, ["taken_refusals: x"])
    assert failed.outcome == "fail"
    assert failed.check_failures[-1] == "taken_refusals: x"
    broken = results[0].model_copy(update={"outcome": "error", "error": "harness: boom"})
    assert pairs.with_pair_checks(broken, scenario, LAST_SLOT, ["x: y"]) is broken


def test_a_pair_never_met_at_the_booking_when_one_call_ends_first(seed_dir, tmp_path):
    quiet = ScriptedModel([say("Okay, goodbye.")])
    results = pairs.run_pair(
        harness(seed_dir, tmp_path),
        SAME_TECH,
        caller_turns=[["Hi."]] * 2,
        agent_models=[quiet, agents()[1]],
    )
    assert (
        "booked_together: the calls never reached a booking at the same moment, "
        "so nothing raced" in results[0].check_failures
    )


def test_the_run_hash_covers_the_pair_file_once_and_leaves_scenarios_alone():
    ids = ["two-callers-last-slot-a", "two-callers-last-slot-b"]
    assert run_hash(ids, "fast") == run_hash(ids[:1], "fast")
    assert pairs.pair_file("no-heat-unknown-caller") is None
    assert (
        pairs.pair_file("two-callers-last-slot-a") == pairs.PAIRS_DIR / "two-callers-last-slot.yaml"
    )


def test_pairs_run_only_on_our_stack(capsys):
    assert main(["--pairs", "all", "--stacks", "retell", "--no-upload"]) == 2
    assert "livekit stack only" in capsys.readouterr().err
    assert main(["--pairs", "nope", "--no-upload"]) == 2
