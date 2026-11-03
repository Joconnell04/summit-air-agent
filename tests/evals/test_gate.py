"""The eval gate for owner edits (evals/gate.py): what blocks a draft and how the result is kept."""
from datetime import UTC, datetime

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
DRAFT = {"guidance": {"booking": ["Ask about pets."]}, "lines": {}}
NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


def result(sid, outcome, failures=(), error=""):
    return ScenarioResult(
        scenario_id=sid, persona="A caller.", flow=["booking"], difficulty="easy", outcome=outcome,
        check_failures=list(failures), error=error, grader_score=None if error else 3,
        reply_delay_p50_ms=1, reply_delay_p90_ms=1, cost_usd=0.02, call_id=f"c-{sid}",
    )


def run_of(*scenarios):
    return build_run("gate", ["must-pass"], "livekit", "fast", list(scenarios), NOW, overlay=DRAFT)


def test_a_code_check_failure_or_an_error_blocks_and_a_grader_note_does_not():
    a, b, c, d = "co-alarm-clear", "commercial-bakery", "gas-smell-in-passing", "landlord-in-disguise"
    found = gate.summary(run_of(
        result(a, "pass"),
        result(b, "fail", ["grader: too chatty"]),
        result(c, "fail", ["booking: 1 booking(s) made, expected none"]),
        result(d, "error", error="harness: broke"),
    ), DRAFT)
    assert found["failed"] == [c] and found["errors"] == [d]
    assert found["grader_flags"] == [b] and found["passed_count"] == 2
    assert found["content_hash"] == rules.content_hash(DRAFT)
    assert not rules.gate_passed(found)
    clean = gate.summary(run_of(result(a, "pass"), result(b, "fail", ["grader: x"])), DRAFT)
    assert rules.gate_passed(clean)


def test_the_gate_covers_every_must_pass_scenario():
    chosen = gate.scenarios()
    assert chosen and all(s.must_pass for s in chosen)
    assert {s.id for s in chosen} == {s.id for s in load_all() if s.must_pass}


def test_check_waiting_records_the_result_on_the_version_it_tested(tmp_path):
    path = tmp_path / "tool.db"
    db.init(path)
    conn = db.connect(path)
    rules.save_draft(conn, TENANT, DRAFT, "Dana", "pets", NOW)
    rules.request_publish(conn, TENANT.slug, "Dana", NOW)
    conn.close()
    tested = []

    def fake_run(content, profile, results_dir):
        tested.append(content)
        return None, gate.summary(run_of(result("co-alarm-clear", "pass")), content)

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
    assert "## Booking\n\n- Ask about pets." in agent.calls[0]["system"]


def test_a_gate_run_is_saved_with_its_draft(tmp_path):
    run, found = gate.run_gate(DRAFT, "fast", tmp_path, harness=_FakeHarness())
    saved = load(tmp_path / f"{run.run_id}.json")
    assert saved.overlay == DRAFT and saved.suite == "gate" and found["scenarios"] == len(
        gate.scenarios()
    )


class _FakeHarness:
    def run(self, scenario, sample=1):
        return result(scenario.id, "pass")
