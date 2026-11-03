import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4, uuid5

import langsmith
import pytest
from langsmith.utils import LangSmithAuthError

from evals import langsmith_sync as ls
from evals import results


class FakeClient:
    """In-memory stand-in for langsmith.Client: no network, no key."""

    def __init__(self):
        self.datasets = {}
        self.examples = {}
        self.projects = {}
        self.project_ids = {}
        self.created_runs = []
        self.feedback = {}
        self.comments = {}
        self.query_calls = []
        self.runs = SimpleNamespace(query_v2=self.query_v2)
        self.created_runs_total = 0

    def has_dataset(self, *, dataset_name):
        return dataset_name in self.datasets

    def read_dataset(self, *, dataset_name):
        return self.datasets[dataset_name]

    def create_dataset(self, dataset_name, **kwargs):
        self.datasets[dataset_name] = SimpleNamespace(id=uuid4(), name=dataset_name, **kwargs)
        return self.datasets[dataset_name]

    def list_examples(self, dataset_id):
        return [e for e in self.examples.values() if e.dataset_id == dataset_id]

    def create_example(self, inputs, dataset_id, example_id, metadata):
        self.examples[str(example_id)] = SimpleNamespace(
            id=example_id, inputs=inputs, dataset_id=dataset_id, metadata=metadata
        )

    def update_example(self, example_id, *, inputs):
        self.examples[str(example_id)].inputs = inputs

    def has_project(self, name):
        return name in self.projects

    def create_project(self, name, **kwargs):
        self.projects[name] = kwargs
        self.project_ids[name] = uuid4()
        return SimpleNamespace(id=self.project_ids[name], url=f"https://smith.example/p/{name}")

    def read_project(self, *, project_name):
        return SimpleNamespace(
            id=self.project_ids[project_name], url=f"https://smith.example/p/{project_name}"
        )

    def list_runs(self, **kwargs):
        raise AssertionError("list_runs is deprecated; use runs.query_v2")

    async def query_v2(self, **kwargs):
        self.query_calls.append(kwargs)
        # The real endpoint returns only runs that start inside the window, one day by default.
        assert "min_start_time" in kwargs, "the default one-day window would hide older runs"
        window = datetime.now(UTC) - kwargs["min_start_time"]
        assert window <= timedelta(days=401), "the API rejects a window longer than 401 days"
        names = [n for n, pid in self.project_ids.items() if str(pid) in kwargs["project_ids"]]
        for r in self.created_runs:
            if r["project_name"] in names:
                yield SimpleNamespace(id=r["id"])

    def create_run(self, **kwargs):
        self.created_runs.append(kwargs)
        self.created_runs_total += 1

    def flush(self):
        pass

    def create_feedback(self, run_id, key, *, score, comment=None, session_id, feedback_id):
        assert session_id in self.project_ids.values()
        # Like LangSmith, a repeated feedback id replaces the earlier score.
        self.feedback[feedback_id] = (run_id, key, score)
        self.comments[feedback_id] = comment


@pytest.fixture
def client():
    return FakeClient()


def test_experiment_is_named_by_the_run_id(make_run):
    run = make_run(run_id="20261103-015651-retell-quality-rigorous")
    assert ls.experiment_name(run) == "20261103-015651-retell-quality-rigorous"


def test_sync_creates_the_dataset_with_one_example_per_scenario(client, make_run):
    ls.sync(make_run(["a", "b", "c"]), client)
    assert list(client.datasets) == ["summit-air-scenarios"]
    assert len(client.examples) == 3
    example = client.examples[str(ls.example_id("a"))]
    assert example.inputs == {
        "persona": "Adult child calling for their 84-year-old father; anxious, talks fast.",
        "tags": [
            "flow:booking",
            "flow:priority",
            "difficulty:rigorous",
            "trait:stutter",
            "trait:late-correction",
        ],
        "difficulty": "rigorous",
    }
    assert example.metadata == {"scenario_id": "a"}


def test_second_run_reuses_the_dataset_and_adds_only_new_scenarios(client, make_run):
    ls.sync(make_run(["a", "b"], run_id="r1"), client)
    ls.sync(make_run(["b", "c"], run_id="r2"), client)
    assert len(client.datasets) == 1
    assert len(client.examples) == 3


def test_a_changed_persona_updates_the_example_in_place(client, make_run):
    ls.sync(make_run(["a"], run_id="r1"), client)
    run = make_run(["a"], run_id="r2")
    run.scenarios[0].persona = "A rewritten persona"
    ls.sync(run, client)
    assert len(client.examples) == 1
    assert client.examples[str(ls.example_id("a"))].inputs["persona"] == "A rewritten persona"


def test_experiment_is_a_project_on_the_dataset_with_run_metadata(client, make_run):
    run = make_run(
        ["a"], stack="retell", profile="quality", suite="smoke", run_id="r9", hash="h1",
        prompt_hash="p1", model="claude-sonnet-5-5",
    )
    name = ls.sync(run, client)
    assert name == "r9"
    project = client.projects[name]
    assert project["reference_dataset_id"] == client.datasets["summit-air-scenarios"].id
    assert project["metadata"] == {
        "stack": "retell",
        "profile": "quality",
        "suite": "smoke",
        "hash": "h1",
        "prompt_hash": "p1",
        "model": "claude-sonnet-5-5",
        "run_id": "r9",
    }
    assert "smoke suite, 1 scenarios" in project["description"]


def test_an_old_results_file_without_prompt_hash_or_model_still_syncs(client, make_run):
    name = ls.sync(make_run(["a"], run_id="old"), client)
    assert set(client.projects[name]["metadata"]) == {"stack", "profile", "suite", "hash", "run_id"}


def test_sync_prints_the_experiment_link(client, make_run, capsys):
    ls.sync(make_run(["a"], run_id="r1"), client)
    assert "https://smith.example/p/r1" in capsys.readouterr().out


def test_each_scenario_becomes_a_run_linked_to_its_example(client, make_run):
    ls.sync(make_run(["a", "b"]), client)
    assert len(client.created_runs) == 2
    first = client.created_runs[0]
    assert first["name"] == "a"
    assert first["reference_example_id"] == ls.example_id("a")
    assert first["outputs"]["transcript"][0] == {
        "role": "caller",
        "text": "Hi, um, my dad's heat is out",
    }
    assert first["outputs"]["tool_calls"][0]["name"] == "lookup_customer"
    assert first["extra"]["metadata"]["call_id"] == "call-a"


def with_checks(make_run, outcome="pass", failures=(), checks=("booking", "read_back")):
    result = make_run().scenarios[0]
    result.outcome, result.check_failures, result.checks = outcome, list(failures), list(checks)
    return result


def test_every_code_check_that_ran_gets_its_own_score(make_run):
    result = with_checks(make_run)
    feedback = ls.feedback_for(result)
    assert feedback["check:booking"] == 1 and feedback["check:read_back"] == 1


def test_a_failed_check_scores_zero_with_its_reason_and_the_others_stay_one(make_run):
    result = with_checks(
        make_run, "fail", ["read_back: booked before the read-back", "read_back: again"]
    )
    feedback = ls.feedback_for(result)
    assert (feedback["check:booking"], feedback["check:read_back"]) == (1, 0)
    comments = ls.feedback_comments(result)
    assert comments["check:read_back"] == "read_back: booked before the read-back; read_back: again"
    assert "check:booking" not in comments
    assert comments["passed"].startswith("read_back: booked")


def test_an_error_run_has_no_check_scores(make_run):
    result = with_checks(make_run, "error")
    assert not [k for k in ls.feedback_for(result) if k.startswith("check:")]


def test_a_check_name_that_starts_like_another_is_not_mistaken_for_it(make_run):
    result = with_checks(make_run, "fail", ["safety_lines_said: nope"], ("safety_lines", "safety_lines_said"))
    feedback = ls.feedback_for(result)
    assert (feedback["check:safety_lines"], feedback["check:safety_lines_said"]) == (1, 0)


def test_comments_carry_the_error_the_grader_notes_and_must_pass_reasons(make_run):
    result = make_run().scenarios[0]
    result.grader_notes = "Warm and clear."
    assert ls.feedback_comments(result) == {"grader_score": "Warm and clear."}
    result.outcome, result.error = "error", "the call did not finish"
    result.must_pass_failures = ["gas not escalated"]
    comments = ls.feedback_comments(result)
    assert comments["errored"] == "the call did not finish"
    assert comments["must_pass_failed"] == "gas not escalated"


def test_sync_sends_the_comments_with_the_scores(client, make_run):
    run = make_run(["a"])
    run.scenarios[0].outcome = "fail"
    run.scenarios[0].check_failures = ["booking: none made"]
    run.scenarios[0].checks = ["booking"]
    ls.sync(run, client)
    by_key = {key: client.comments[fid] for fid, (_, key, _) in client.feedback.items()}
    assert by_key["check:booking"] == "booking: none made"
    assert by_key["cost_usd"] is None


def test_feedback_maps_every_measure(make_run):
    result = make_run().scenarios[0]
    assert ls.feedback_for(result) == {
        "passed": 1,
        "must_pass_failed": 0,
        "grader_score": 4,
        "reply_delay_p50_ms": 820.0,
        "reply_delay_p90_ms": 1450.5,
        "cost_usd": 0.12,
    }


def test_feedback_flags_a_failed_must_pass_case(make_run):
    result = make_run().scenarios[0]
    result.outcome = "fail"
    result.must_pass_failures = ["gas-smell-not-escalated"]
    feedback = ls.feedback_for(result)
    assert feedback["passed"] == 0
    assert feedback["must_pass_failed"] == 1


def test_feedback_for_an_error_has_errored_and_no_passed_score(make_run):
    result = make_run().scenarios[0]
    result.outcome = "error"
    feedback = ls.feedback_for(result)
    assert feedback["errored"] == 1
    assert "passed" not in feedback
    assert len(feedback) == 6


def test_scenario_runs_are_tagged_for_filtering_and_errors_are_marked(client, make_run):
    run = make_run(["a", "b"])
    run.scenarios[1].outcome, run.scenarios[1].error = "error", "harness: boom"
    ls.sync(run, client)
    ok, bad = client.created_runs
    assert {"stack:livekit", "suite:smoke", "outcome:pass", "flow:booking", "difficulty:rigorous",
            "trait:stutter"} <= set(ok["tags"])
    assert "outcome:error" in bad["tags"]
    assert ok["error"] is None and bad["error"] == "harness: boom"


def test_run_outputs_carry_the_outcome(client, make_run):
    run = make_run(["a"])
    run.scenarios[0].outcome = "error"
    ls.sync(run, client)
    assert client.created_runs[0]["outputs"]["outcome"] == "error"


def test_sync_posts_six_feedback_scores_per_scenario(client, make_run):
    ls.sync(make_run(["a", "b"]), client)
    assert len(client.feedback) == 12
    keys = {key for _, key, _ in client.feedback.values()}
    assert keys == {
        "passed",
        "must_pass_failed",
        "grader_score",
        "reply_delay_p50_ms",
        "reply_delay_p90_ms",
        "cost_usd",
    }


def test_syncing_the_same_run_twice_changes_nothing(client, make_run):
    run = make_run(["a", "b"])
    ls.sync(run, client)
    snapshot = (
        len(client.projects),
        len(client.examples),
        len(client.created_runs),
        len(client.feedback),
    )
    ls.sync(run, client)
    assert (
        len(client.projects),
        len(client.examples),
        len(client.created_runs),
        len(client.feedback),
    ) == snapshot
    assert client.created_runs_total == 2


def test_resync_finishes_a_partly_uploaded_experiment(client, make_run):
    run = make_run(["a", "b"])
    ls.sync(run, client)
    client.created_runs.pop()
    client.feedback.clear()
    ls.sync(run, client)
    assert len(client.created_runs) == 2
    assert len(client.feedback) == 12


def test_resync_restores_scores_for_runs_that_already_exist(client, make_run):
    run = make_run(["a", "b"])
    ls.sync(run, client)
    client.feedback.clear()
    ls.sync(run, client)
    assert len(client.created_runs) == 2
    assert len(client.feedback) == 12
    per_run = {}
    for rid, key, _ in client.feedback.values():
        per_run.setdefault(rid, []).append(key)
    assert all(len(keys) == 6 and len(set(keys)) == 6 for keys in per_run.values())


def test_feedback_ids_are_the_same_on_every_sync(client, make_run):
    run = make_run(["a"])
    ls.sync(run, client)
    first = set(client.feedback)
    ls.sync(run, client)
    assert set(client.feedback) == first


def test_existing_runs_are_found_with_the_supported_query_over_the_widest_window(client, make_run):
    ls.sync(make_run(["a"]), client)
    project_id = next(iter(client.project_ids.values()))
    call = client.query_calls[0]
    assert call["project_ids"] == [str(project_id)]
    assert call["is_root"] is True
    assert call["selects"] == ["ID"]
    assert timedelta(days=300) < datetime.now(UTC) - call["min_start_time"] <= timedelta(days=401)


def test_a_scenario_with_a_duration_gets_a_matching_start_and_end(client, make_run):
    run = make_run(["a", "b"])
    run.scenarios[0].duration_ms = 90_000
    ls.sync(run, client)
    timed, untimed = client.created_runs
    assert timed["start_time"] == run.created
    assert (timed["end_time"] - timed["start_time"]).total_seconds() == 90
    assert "start_time" not in untimed
    assert "end_time" not in untimed


def test_no_key_does_nothing_and_prints_one_line(monkeypatch, capsys, make_run):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert ls.sync(make_run()) is None
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert "LANGSMITH_API_KEY is not set" in out


def test_cli_without_a_key_exits_cleanly_even_for_a_missing_file(monkeypatch, capsys):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert ls.main(["langsmith_sync", "no/such/file.json"]) == 0
    assert capsys.readouterr().out.count("\n") == 1


def test_cli_with_a_key_uploads_the_file(monkeypatch, tmp_path, make_run, client):
    monkeypatch.setattr(ls, "connect", lambda: client)
    path = results.save(make_run(["a"]), tmp_path)
    assert ls.main(["langsmith_sync", str(path)]) == 0
    assert len(client.created_runs) == 1


def test_cli_rejects_an_invalid_file(monkeypatch, tmp_path, capsys, client):
    monkeypatch.setattr(ls, "connect", lambda: client)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"run_id": "x"}))
    assert ls.main(["langsmith_sync", str(bad)]) == 1
    assert "not a valid results file" in capsys.readouterr().err
    assert client.created_runs == []


def test_cli_needs_exactly_one_path(capsys):
    assert ls.main(["langsmith_sync", "a.json", "b.json"]) == 2
    assert "usage" in capsys.readouterr().err


def test_cli_without_a_path_uploads_the_newest_results_file(
    monkeypatch, tmp_path, make_run, client
):
    monkeypatch.setattr(ls, "connect", lambda: client)
    monkeypatch.setattr(ls, "RESULTS_DIR", tmp_path)
    older = make_run(["a"], run_id="r1", created="2026-11-02T15:00:00Z")
    newer = make_run(["a"], run_id="r2", created="2026-11-02T16:00:00Z")
    results.save(newer, tmp_path)
    results.save(older, tmp_path)
    (tmp_path / "notes.json").write_text("{}")
    assert ls.main(["langsmith_sync"]) == 0
    assert list(client.projects) == ["r2"]


def test_cli_without_a_path_and_no_results_says_so(monkeypatch, tmp_path, capsys, client):
    monkeypatch.setattr(ls, "connect", lambda: client)
    monkeypatch.setattr(ls, "RESULTS_DIR", tmp_path)
    assert ls.main(["langsmith_sync"]) == 1
    assert "run make eval first" in capsys.readouterr().err


@pytest.mark.parametrize("error", [LangSmithAuthError, langsmith.LangsmithError])
def test_cli_reports_an_api_failure_in_one_line(
    error, monkeypatch, tmp_path, capsys, make_run, client
):
    def refuse(**kwargs):
        raise error("401 Unauthorized\nrequest id abc\nmore detail")

    monkeypatch.setattr(client, "has_dataset", refuse)
    monkeypatch.setattr(ls, "connect", lambda: client)
    path = results.save(make_run(["a"]), tmp_path)
    assert ls.main(["langsmith_sync", str(path)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert f"LangSmith sync failed ({error.__name__}: 401 Unauthorized)" in err
    assert "Traceback" not in err


def test_an_error_run_sends_no_grader_score(make_run):
    run = make_run()
    run.scenarios[0].outcome, run.scenarios[0].grader_score = "error", None
    assert "grader_score" not in ls.feedback_for(run.scenarios[0])


def test_each_sample_is_its_own_run_and_the_first_keeps_its_old_id(make_run):
    run = make_run()
    first = run.scenarios[0]
    second = first.model_copy(update={"sample": 2})
    assert ls.run_id_for(run, first) != ls.run_id_for(run, second)
    expected = uuid5(ls.NAMESPACE,
                          f"run/{ls.experiment_name(run)}/{first.scenario_id}")
    assert ls.run_id_for(run, first) == expected


def test_the_sync_calls_no_langsmith_method_the_sdk_marks_deprecated():
    import inspect
    import re
    from pathlib import Path

    source = Path(inspect.getfile(langsmith.Client)).read_text()
    deprecated = set(re.findall(r'@_deprecated\(\s*"(\w+)\(\)', source))
    assert "list_runs" in deprecated  # the pattern still finds the SDK's markers
    used = set(re.findall(r"client\.(\w+)\(", Path(ls.__file__).read_text()))
    assert used, "no client calls found"
    assert used <= set(dir(langsmith.Client)), "a call names a method the SDK no longer has"
    assert not used & deprecated
    assert "runs.query_v2" in Path(ls.__file__).read_text()


def test_a_full_sync_raises_no_deprecation_warning(client, make_run):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        ls.sync(make_run(["a", "b"]), client)
