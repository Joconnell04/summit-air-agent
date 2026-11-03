"""The eval gate for owner edits (evals/gate.py): what blocks a draft and how the result is kept."""
from datetime import UTC, datetime

import pytest
from fakes import ScriptedModel, grade_reply, say
from test_harness import scenario

from app import db, rules
from app.tenant import Tenant
from evals import gate
from evals.results import ScenarioResult, load
from evals.run import Harness, Models, build_run
from evals.scenario import load_all
from evals.stacks import TENANTS_DIR

TENANT = Tenant.load(TENANTS_DIR / "summit-air")
DRAFT = {"guidance": {"whole_call": ["Ask about pets."]}, "lines": {}}
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)



def passes(tenant, items):
    """The guidance check, passing: these tests are about the gate."""
    return {}

def result(sid, outcome, failures=(), error=""):
    return ScenarioResult(
        scenario_id=sid, persona="A caller.", flow=["booking"], difficulty="easy", outcome=outcome,
        check_failures=list(failures), error=error, grader_score=None if error else 3,
        reply_delay_p50_ms=1, reply_delay_p90_ms=1, cost_usd=0.02, call_id=f"c-{sid}",
    )


def run_of(*scenarios, suite="gate"):
    return build_run(suite, ["must-pass"], "livekit", "fast", list(scenarios), NOW, overlay=DRAFT)


def test_a_code_check_failure_or_an_error_blocks_and_a_grader_note_does_not():
    a, b, c, d = "co-alarm-clear", "commercial-bakery", "gas-smell-in-passing", "safety-override-insists"
    found = gate.summary(run_of(
        result(a, "pass"),
        result(b, "fail", ["grader: too chatty"]),
        result(c, "fail", ["booking: 1 booking(s) made, expected none"]),
        result(d, "error", error="harness: broke"),
    ), DRAFT)
    assert found["failed"] == [c] and found["errors"] == [d]
    assert found["grader_flags"] == [b] and found["passed_count"] == 2
    assert found["content_hash"] == rules.content_hash(DRAFT)
    assert not rules.gate_passed(found, found["scenarios"])
    clean = gate.summary(run_of(result(a, "pass"), result(b, "fail", ["grader: x"])), DRAFT)
    assert rules.gate_passed(clean, clean["scenarios"])


def test_the_gate_covers_every_must_pass_scenario():
    chosen = gate.scenarios()
    assert chosen and all(s.must_pass for s in chosen)
    assert {s.id for s in chosen} == {s.id for s in load_all() if s.must_pass}


def test_check_waiting_records_the_result_on_the_version_it_tested(tmp_path):
    path = tmp_path / "tool.db"
    db.init(path)
    conn = db.connect(path)
    rules.save_draft(conn, TENANT, DRAFT, "Dana", "pets", NOW, passes)
    rules.request_publish(conn, TENANT.slug, "Dana", NOW)
    conn.close()
    tested = []

    def fake_run(content, profile, results_dir):
        tested.append(content)
        return None, gate.summary(run_of(*(result(x.id, "pass") for x in gate.scenarios())), content)

    done = gate.check_waiting(path, "fast", tmp_path, run=fake_run)
    assert tested == [rules.validate(TENANT, DRAFT)[0]]
    assert [v["status"] for v in done] == ["published"]
    assert gate.check_waiting(path, "fast", tmp_path, run=fake_run) == [], "nothing left waiting"


def test_a_gate_run_serves_the_draft_through_the_real_call_path(tmp_path):
    """The harness's world makes the draft live, so the model's prompt carries the guidance."""
    agent = ScriptedModel([say("Thanks, goodbye.")])
    models = Models(agent, ScriptedModel([say("Bye.")]), ScriptedModel([grade_reply()]))
    harness = Harness(models, "livekit", tmp_path / "seed", overlay=DRAFT)
    harness.workdir = tmp_path
    harness.run(scenario())
    assert "## Whole call\n\n- Ask about pets." in agent.calls[0]["system"]


def test_a_gate_run_is_saved_with_its_draft(tmp_path):
    run, found = gate.run_gate(DRAFT, "fast", tmp_path, harness=_FakeHarness())
    saved = load(tmp_path / f"{run.run_id}.json")
    assert saved.overlay == DRAFT and saved.suite == "gate" and found["scenarios"] == len(
        gate.scenarios()
    )


def test_a_miss_is_rerun_and_counts_only_if_it_misses_again():
    a, b, c = "co-alarm-clear", "commercial-bakery", "gas-smell-in-passing"
    first = run_of(result(a, "pass"), result(b, "fail", ["booking: none made"]),
                   result(c, "error", error="harness: broke"))
    rerun = run_of(result(b, "pass"), result(c, "fail", ["lead: none"]), suite="gate-rerun")
    found = gate.summary(first, DRAFT, rerun)
    assert found["first_missed"] == [b, c]
    assert found["passed_on_rerun"] == [b] and found["failed"] == [c] and found["errors"] == []
    assert found["reruns"] == 2 and found["rerun_cost_usd"] == 0.04
    assert found["cost_usd"] == 0.1 and found["passed_count"] == 2
    assert not rules.gate_passed(found, found["scenarios"])
    rerun_clean = gate.summary(first, DRAFT, run_of(
        result(b, "pass"), result(c, "pass"), suite="gate-rerun"))
    assert rules.gate_passed(rerun_clean, 3)


def test_the_gate_reruns_only_the_scenarios_that_missed(tmp_path):
    flaky = _FakeHarness(fail_first={"commercial-bakery"})
    _run, found = gate.run_gate(DRAFT, "fast", tmp_path, harness=flaky)
    assert flaky.calls.count("commercial-bakery") == 2
    assert len(flaky.calls) == len(gate.scenarios()) + 1
    assert found["passed_on_rerun"] == ["commercial-bakery"]
    assert rules.gate_passed(found, len(gate.scenarios()))
    assert load(tmp_path / f"{found['rerun_id']}.json").suite == "gate-rerun"

    broken = _FakeHarness(fail_always={"co-alarm-clear"})
    _run, found = gate.run_gate(DRAFT, "fast", tmp_path, harness=broken)
    assert found["failed"] == ["co-alarm-clear"]
    assert not rules.gate_passed(found, len(gate.scenarios()))


def test_a_partial_or_malformed_result_never_passes():
    clean = {"scenarios": 18, "failed": [], "errors": []}
    assert rules.gate_passed(clean, 18)
    assert not rules.gate_passed({**clean, "scenarios": 17}, 18), "fewer than the must-pass set"
    assert not rules.gate_passed({"scenarios": 18, "errors": []}, 18), "no failed list"
    assert not rules.gate_passed({"scenarios": 18, "failed": [], "errors": None}, 18)
    assert not rules.gate_passed({"scenarios": 0, "failed": [], "errors": []}, 0)


def test_only_one_gate_process_runs_at_a_time(tmp_path):
    path = tmp_path / "tool.db"
    db.init(path)
    with gate.single_runner(path):
        try:
            gate.check_waiting(path, "fast", tmp_path, run=lambda *a: pytest.fail("ran twice"))
        except gate.Busy:
            pass
        else:
            pytest.fail("the second runner was let in")
    assert gate.check_waiting(path, "fast", tmp_path) == [], "free again once the first is done"


def test_a_late_result_for_a_cancelled_check_is_not_recorded(tmp_path):
    path = tmp_path / "tool.db"
    db.init(path)
    conn = db.connect(path)
    rules.save_draft(conn, TENANT, DRAFT, "Dana", "pets", NOW, passes)
    rules.request_publish(conn, TENANT.slug, "Dana", NOW)

    def cancelled_meanwhile(content, profile, results_dir):
        rules.cancel_check(conn, TENANT.slug, NOW)
        return None, gate.summary(run_of(*(result(x.id, "pass") for x in gate.scenarios())), content)

    assert gate.check_waiting(path, "fast", tmp_path, run=cancelled_meanwhile) == []
    assert rules.open_draft(conn, TENANT.slug)["status"] == "draft"
    assert rules.live(conn, TENANT.slug) is None
    conn.close()


def test_a_clean_run_needs_no_rerun(tmp_path):
    clean = _FakeHarness()
    _run, found = gate.run_gate(DRAFT, "fast", tmp_path, harness=clean)
    assert found["reruns"] == 0 and found["rerun_id"] is None
    assert len(clean.calls) == len(gate.scenarios())


class _FakeHarness:
    def __init__(self, fail_first=(), fail_always=()):
        self.fail_first, self.fail_always = set(fail_first), set(fail_always)
        self.calls: list[str] = []

    def run(self, scenario, sample=1):
        first = scenario.id not in self.calls
        self.calls.append(scenario.id)
        if scenario.id in self.fail_always or (first and scenario.id in self.fail_first):
            return result(scenario.id, "fail", ["booking: 0 booking(s) made, expected 1"])
        return result(scenario.id, "pass")
