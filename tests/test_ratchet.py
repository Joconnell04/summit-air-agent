import importlib.util
import json
from pathlib import Path

import yaml

from evals import results as results_file
from evals.results import Run
from evals.scenario import Scenario

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts" / "ratchet.py")
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)


def test_ratchet_passes_at_baseline():
    ok, _ = ratchet.check(4, {"min_tests": 4})
    assert ok


def test_ratchet_fails_below_baseline():
    ok, msg = ratchet.check(3, {"min_tests": 4})
    assert not ok
    assert "below" in msg


def test_model_profiles_name_a_default():
    profiles = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    assert profiles["default"] in profiles["profiles"]


def test_every_profile_has_provider_and_model():
    profiles = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["profiles"]
    for name, p in profiles.items():
        assert p.get("provider") and p.get("model"), name


# The eval floor

SCENARIO = {
    "scenario_id": "s1", "persona": "p", "flow": ["booking"], "difficulty": "easy",
    "outcome": "pass", "grader_score": 4, "reply_delay_p50_ms": 1, "reply_delay_p90_ms": 2,
    "cost_usd": 0.1, "call_id": "c1",
}


def saved_run(tmp_path, scenarios=None, hash="h1"):
    run = Run.model_validate({
        "run_id": "r1", "created": "2026-11-02T15:00:00Z", "suite": "smoke", "stack": "livekit",
        "profile": "fast", "hash": hash, "scenarios": scenarios or [SCENARIO],
    })
    results_file.save(run, tmp_path / "evals" / "results")
    return "evals/results/r1.json"


def scenario(id, spec=(), tags=("smoke",)):
    return Scenario.model_validate({
        "id": id, "flow": ["safety"], "difficulty": "easy", "journeys": ["J3"], "spec": list(spec),
        "tags": list(tags), "persona": "p", "opening": "o", "must": [{"booking": "none"}],
    })


# s1 is an ordinary smoke case; gas is a must-pass smoke case; deep is must-pass but not smoke.
POOL = [scenario("s1"), scenario("gas", ["S-17"]), scenario("deep", ["S-17"], tags=())]
GAS = {**SCENARIO, "scenario_id": "gas", "call_id": "c2"}


def eval_check(tmp_path, baseline, current="h1", scenarios=POOL, allow_stale=False):
    return ratchet.check_eval(baseline, tmp_path, current_hash=lambda run: current,
                              scenarios=scenarios, allow_stale=allow_stale)


def test_no_reference_run_passes(tmp_path):
    ok, msg = ratchet.check_eval({"min_tests": 1, "eval_results": None}, tmp_path)
    assert ok and "no reference run" in msg


def test_a_current_reference_run_passes(tmp_path):
    path = saved_run(tmp_path, [SCENARIO, GAS])
    ok, msg = eval_check(tmp_path, {"eval_results": path, "min_eval_scenarios": 2})
    assert ok, msg


def test_a_must_pass_case_missing_from_the_suite_fails(tmp_path):
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path)})
    assert not ok and "must-pass case missing: gas" in msg


def test_an_errored_must_pass_case_fails(tmp_path):
    errored = {**GAS, "outcome": "error", "grader_score": None}
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path, [SCENARIO, errored])})
    assert not ok and "must-pass case error: gas" in msg


def test_a_stale_hash_fails(tmp_path):
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path)}, current="h2")
    assert not ok and "stale" in msg


def test_a_stale_hash_alone_is_a_warning_when_allowed(tmp_path):
    path = saved_run(tmp_path, [SCENARIO, GAS])
    ok, msg = eval_check(tmp_path, {"eval_results": path}, current="h2", allow_stale=True)
    assert ok and "warning" in msg and "stale" in msg


def test_a_must_pass_case_added_after_a_stale_reference_warns(tmp_path):
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path)}, current="h2",
                         allow_stale=True)
    assert ok and "stale" in msg


def test_a_missing_must_pass_case_still_fails_on_a_current_reference(tmp_path):
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path)}, allow_stale=True)
    assert not ok and "must-pass case missing: gas" in msg


def test_a_stale_reference_still_fails_on_a_failed_must_pass_case(tmp_path):
    failed = {**GAS, "outcome": "fail", "must_pass_failures": ["safety_lines: x"],
              "check_failures": ["safety_lines: x"]}
    path = saved_run(tmp_path, [SCENARIO, failed])
    ok, msg = eval_check(tmp_path, {"eval_results": path}, current="h2", allow_stale=True)
    assert not ok and "must-pass case fail: gas" in msg


def test_a_stale_reference_still_fails_under_the_pass_rate_floor(tmp_path):
    low = {**SCENARIO, "outcome": "fail"}
    path = saved_run(tmp_path, [low, GAS])
    baseline = {"eval_results": path, "min_eval_pass_rate": 0.9}
    ok, msg = eval_check(tmp_path, baseline, current="h2", allow_stale=True)
    assert not ok and "under the floor" in msg


def test_fewer_scenarios_than_the_floor_fails(tmp_path):
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path), "min_eval_scenarios": 2})
    assert not ok and "fewer than the floor" in msg


def test_a_failed_must_pass_case_fails(tmp_path):
    failed = {**GAS, "outcome": "fail", "must_pass_failures": ["safety_lines: x"],
              "check_failures": ["safety_lines: x"]}
    ok, msg = eval_check(tmp_path, {"eval_results": saved_run(tmp_path, [SCENARIO, failed])})
    assert not ok and "must-pass case fail: gas" in msg


def test_a_grader_only_fail_on_a_must_pass_case_counts_in_the_pass_rate_not_as_must_pass(tmp_path):
    graded = {**GAS, "outcome": "fail", "check_failures": ["grader: a long turn"]}
    path = saved_run(tmp_path, [SCENARIO, graded])
    ok, msg = eval_check(tmp_path, {"eval_results": path})
    assert ok, msg
    ok, msg = eval_check(tmp_path, {"eval_results": path, "min_eval_pass_rate": 0.6})
    assert not ok and "pass rate 50%" in msg
    assert ratchet.adopt(path, {}, tmp_path, POOL)[0]


def test_a_pass_rate_under_the_floor_fails_and_errors_are_left_out(tmp_path):
    rows = [SCENARIO, GAS, {**SCENARIO, "scenario_id": "s2", "outcome": "fail"},
            {**SCENARIO, "scenario_id": "s3", "outcome": "fail"},
            {**SCENARIO, "scenario_id": "s4", "outcome": "error", "grader_score": None}]
    path = saved_run(tmp_path, rows)
    ok, msg = eval_check(tmp_path, {"eval_results": path, "min_eval_pass_rate": 0.6})
    assert not ok and "pass rate 50%" in msg, msg
    assert eval_check(tmp_path, {"eval_results": path, "min_eval_pass_rate": 0.5})[0]


def test_a_missing_reference_file_fails(tmp_path):
    ok, msg = eval_check(tmp_path, {"eval_results": "evals/results/nope.json"})
    assert not ok and "cannot read" in msg


def test_adopt_sets_the_reference_and_never_lowers_the_floor(tmp_path):
    baseline = {"min_tests": 1, "min_eval_scenarios": 0}
    path = saved_run(tmp_path, [SCENARIO, GAS])
    ok, msg = ratchet.adopt(path, baseline, tmp_path, POOL)
    assert ok, msg
    assert baseline["eval_results"] == "evals/results/r1.json"
    assert baseline["min_eval_scenarios"] == 2
    ok, msg = ratchet.adopt(path, {"min_eval_scenarios": 5}, tmp_path, POOL)
    assert not ok and "fewer scenarios" in msg


def test_adopt_sets_the_pass_rate_floor_rounded_down_to_five_percent(tmp_path):
    plain = [{**SCENARIO, "scenario_id": f"s{i}", "call_id": f"p{i}"} for i in range(6)]
    plain[0] = {**plain[0], "outcome": "fail"}
    error = {**SCENARIO, "scenario_id": "e", "call_id": "e1", "outcome": "error"}
    # 6 of 7 judged pass (the error is left out): 85.7% becomes an 85% floor.
    runs = [*plain, error, GAS]
    baseline = {"min_tests": 1, "min_eval_scenarios": 0}
    ok, msg = ratchet.adopt(saved_run(tmp_path, runs), baseline, tmp_path, POOL)
    assert ok, msg
    assert baseline["min_eval_pass_rate"] == 0.85
    baseline["min_eval_pass_rate"] = 0.9
    ratchet.adopt(saved_run(tmp_path, runs), baseline, tmp_path, POOL)
    assert baseline["min_eval_pass_rate"] == 0.9  # never lowered


def test_adopt_refuses_a_run_with_a_missing_or_failed_must_pass_case(tmp_path):
    ok, msg = ratchet.adopt(saved_run(tmp_path), {}, tmp_path, POOL)
    assert not ok and "missing: gas" in msg
    failed = {**GAS, "outcome": "fail", "must_pass_failures": ["x: y"], "check_failures": ["x: y"]}
    ok, msg = ratchet.adopt(saved_run(tmp_path, [SCENARIO, failed]), {}, tmp_path, POOL)
    assert not ok and "fail: gas" in msg


def test_the_committed_baseline_names_no_reference_or_a_valid_one():
    # A stale reference is a warning between batches (DECISIONS.md 086); a failed one is not.
    baseline = json.loads((ROOT / "evals" / "baseline.json").read_text())
    ok, msg = ratchet.check_eval(baseline, allow_stale=True)
    assert ok, msg
