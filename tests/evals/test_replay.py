from evals import results
from evals.replay import diff, find_call, regressed, tool_sequence
from evals.results import ToolCall


def test_diff_names_each_step_that_changed():
    assert diff(["a", "b"], ["a", "b"]) == []
    assert diff(["a", "b"], ["a", "c", "d"]) == ["  step 2: was b, now c", "  step 3: was -, now d"]


def test_find_call_returns_the_result_and_its_profile(tmp_path, make_run):
    run = make_run(("s1", "s2"), profile="quality")
    run.scenarios[1].tool_calls = [ToolCall(name="read_back"), ToolCall(name="book_appointment")]
    results.save(run, tmp_path)
    found, profile = find_call("call-s2", tmp_path)
    assert found.scenario_id == "s2" and profile == "quality"
    assert tool_sequence(found) == ["read_back", "book_appointment"]
    assert find_call("call-nope", tmp_path) is None


def test_replay_cli_reports_an_unknown_call(tmp_path, capsys):
    from evals.replay import main

    assert main(["call-nope", "--results-dir", str(tmp_path)]) == 2
    assert "no call call-nope" in capsys.readouterr().err


def test_a_pass_that_became_a_fail_is_a_regression(make_run):
    passed = make_run().scenarios[0]
    failed = passed.model_copy(update={"outcome": "fail", "check_failures": ["x"]})
    assert regressed(passed, failed)
    assert not regressed(failed, passed)
