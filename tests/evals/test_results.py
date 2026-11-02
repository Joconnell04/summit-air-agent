import json

import pytest

from evals import results
from evals.results import ResultsError, Run


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
        ({"passed": True, "must_pass_failures": ["gas-smell"]}, "must_pass_failures"),
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
    failed = scenario_data(passed=False, must_pass_failures=["no-booking-before-read-back"])
    run = Run.model_validate({**make_run().model_dump(mode="json"), "scenarios": [failed]})
    assert run.scenarios[0].must_pass_failures == ["no-booking-before-read-back"]
