import json
from pathlib import Path
from typing import get_args

import pytest

from evals import results
from evals.results import Flow, ResultsError, Run, Trait


def test_run_round_trips_through_json(tmp_path, run_data):
    run = Run.model_validate(run_data())
    path = results.save(run, tmp_path)
    assert path == tmp_path / "20261102-1.json"
    assert results.load(path) == run


def test_save_creates_the_results_directory(tmp_path, make_run):
    path = results.save(make_run(), tmp_path / "nested" / "results")
    assert path.exists()


def test_load_reports_a_missing_file(tmp_path):
    with pytest.raises(ResultsError, match="cannot read"):
        results.load(tmp_path / "nope.json")


def test_load_reports_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ResultsError, match="not a valid results file"):
        results.load(bad)


@pytest.mark.parametrize(
    "scenario_change, message",
    [
        ({"grader_score": 6}, "grader_score"),
        ({"grader_score": 0}, "grader_score"),
        ({"difficulty": "hard"}, "difficulty"),
        ({"cost_usd": -0.01}, "cost_usd"),
        ({"flow": []}, "flow"),
        ({"call_id": ""}, "call_id"),
        ({"reply_delay_p50_ms": 2000.0, "reply_delay_p90_ms": 1000.0}, "p90"),
        ({"outcome": "pass", "must_pass_failures": ["gas-smell"]}, "must_pass_failures"),
        ({"outcome": "passed"}, "outcome"),
        ({"duration_ms": -1}, "duration_ms"),
        ({"flow": ["booking", "plumbing"]}, "flow"),
        ({"traits": ["stutter", "mumbles"]}, "traits"),
        ({"transcript": [{"role": "narrator", "text": "x"}]}, "role"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_load_rejects_a_bad_scenario(tmp_path, run_data, scenario_data, scenario_change, message):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run_data(scenarios=[scenario_data(**scenario_change)])))
    with pytest.raises(ResultsError, match=message):
        results.load(path)


@pytest.mark.parametrize(
    "run_change, message",
    [
        ({"scenarios": []}, "scenarios"),
        ({"run_id": "../escape"}, "run_id"),
        ({"stack": ""}, "stack"),
        ({"created": "2026-11-02T15:30:00"}, "created"),
        ({"hash": ""}, "hash"),
    ],
)
def test_load_rejects_a_bad_run(tmp_path, run_data, run_change, message):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run_data(**run_change)))
    with pytest.raises(ResultsError, match=message):
        results.load(path)


def test_load_rejects_duplicate_scenario_ids(tmp_path, run_data, scenario_data):
    path = tmp_path / "run.json"
    twins = [scenario_data(), scenario_data()]
    path.write_text(json.dumps(run_data(scenarios=twins)))
    with pytest.raises(ResultsError, match="duplicate scenario ids"):
        results.load(path)


def test_a_failed_scenario_with_must_pass_failures_is_valid(make_run, scenario_data):
    failed = scenario_data(outcome="fail", must_pass_failures=["no-booking-before-read-back"])
    run = Run.model_validate({**make_run().model_dump(mode="json"), "scenarios": [failed]})
    assert run.scenarios[0].must_pass_failures == ["no-booking-before-read-back"]


def readme_labels(name: str) -> set[str]:
    readme = (Path(results.__file__).parent / "README.md").read_text()
    line = next(l for l in readme.splitlines() if l.startswith(f"- **{name}:**"))
    return {label.strip() for label in line.split(":**", 1)[1].rstrip(".").split(",")}


def test_flow_and_trait_types_match_the_labels_in_the_readme():
    assert set(get_args(Flow)) == readme_labels("flow")
    assert set(get_args(Trait)) == readme_labels("traits")


def test_a_simulator_loop_is_recorded_as_error_not_fail(tmp_path, make_run, scenario_data):
    errored = scenario_data(outcome="error", grader_score=1)
    run = Run.model_validate({**make_run().model_dump(mode="json"), "scenarios": [errored]})
    assert run.scenarios[0].outcome == "error"
    assert results.load(results.save(run, tmp_path)) == run


def test_only_an_error_run_may_have_no_grader_score(tmp_path, run_data, scenario_data):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(run_data(scenarios=[scenario_data(grader_score=None)])))
    with pytest.raises(ResultsError, match="grader_score is missing"):
        results.load(path)
    error = scenario_data(outcome="error", grader_score=None, error="caller looped")
    path.write_text(json.dumps(run_data(scenarios=[error])))
    assert results.load(path).scenarios[0].grader_score is None


def test_samples_of_one_scenario_are_distinct_and_keyed(run_data, scenario_data):
    run = Run.model_validate(run_data(scenarios=[scenario_data(), scenario_data(sample=2)]))
    assert [s.key for s in run.scenarios] == [
        "no-heat-elderly-wrong-zip",
        "no-heat-elderly-wrong-zip#2",
    ]


def test_pass_rate_leaves_errors_out(run_data, scenario_data):
    rows = [
        scenario_data(scenario_id="a"),
        scenario_data(scenario_id="b", outcome="fail"),
        scenario_data(scenario_id="c", outcome="error", grader_score=None),
    ]
    assert Run.model_validate(run_data(scenarios=rows)).pass_rate() == 0.5
