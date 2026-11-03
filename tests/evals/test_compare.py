from datetime import timedelta

from evals import results
from evals.compare import delays, flagged, percentile, previous, report
from evals.results import TurnTiming


def timed(make_run, delays_ms, kind="plain", **overrides):
    run = make_run(("s1",), **overrides)
    run.scenarios[0].turn_timings = [
        TurnTiming(kind=kind, reply_delay_ms=d, model_first_token_ms=d) for d in delays_ms
    ]
    return run


def test_percentile_is_nearest_rank():
    assert percentile([], 0.5) == 0
    assert percentile([300, 100, 200], 0.5) == 200
    assert percentile(list(range(1, 11)), 0.9) == 9


def test_delay_flag_is_over_100_ms_or_over_10_percent():
    assert not flagged(1000, 1090)
    assert flagged(1000, 1101)
    assert flagged(500, 560)
    assert not flagged(500, 540)


def test_errors_are_left_out_of_delays(make_run):
    run = timed(make_run, [100, 200])
    assert delays(run) == {"plain": (100, 200)}
    run.scenarios[0].outcome, run.scenarios[0].grader_score = "error", None
    assert delays(run) == {}


def test_previous_is_the_latest_earlier_run_of_the_same_suite_stack_and_profile(tmp_path, make_run):
    now = make_run().created
    old = make_run(run_id="old", created=now - timedelta(days=2))
    newer = make_run(run_id="newer", created=now - timedelta(days=1))
    other = make_run(run_id="other", created=now - timedelta(hours=1), stack="retell")
    for r in (old, newer, other):
        results.save(r, tmp_path)
    current = make_run(run_id="current", created=now)
    assert previous(current, tmp_path).run_id == "newer"
    assert previous(make_run(run_id="first", created=now - timedelta(days=3)), tmp_path) is None


def test_report_flags_a_delay_change_and_lists_what_did_not_pass(make_run):
    prev = timed(make_run, [800, 900])
    run = timed(make_run, [1100, 1300], run_id="later")
    run.scenarios[0].outcome = "fail"
    run.scenarios[0].check_failures = ["booking: 0 booking(s) made, expected 1"]
    lines = report(run, prev)
    assert any("FLAG: delay changed" in line for line in lines)
    assert any(line.strip().startswith("FAIL") and "booking" in line for line in lines)
    assert "pass rate 0% (previous 100%)" in lines


def test_report_without_a_previous_run(make_run):
    lines = report(timed(make_run, [500]))
    assert lines[1] == "pass rate 100%"
    assert not any("FLAG" in line for line in lines)
