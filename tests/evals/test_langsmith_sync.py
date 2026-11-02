import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from evals import langsmith_sync as ls
from evals import results


class FakeClient:
    """In-memory stand-in for langsmith.Client: no network, no key."""

    def __init__(self):
        self.datasets = {}
        self.examples = {}
        self.projects = {}
        self.project_ids = {}
        self.runs = []
        self.feedback = []
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
        return SimpleNamespace(id=self.project_ids[name])

    def read_project(self, *, project_name):
        return SimpleNamespace(id=self.project_ids[project_name])

    def list_runs(self, *, project_id, **kwargs):
        names = [n for n, pid in self.project_ids.items() if pid == project_id]
        return [SimpleNamespace(id=r["id"]) for r in self.runs if r["project_name"] in names]

    def create_run(self, **kwargs):
        self.runs.append(kwargs)
        self.created_runs_total += 1

    def flush(self):
        pass

    def create_feedback(self, run_id, key, *, score, session_id):
        assert session_id in self.project_ids.values()
        self.feedback.append((run_id, key, score))


@pytest.fixture
def client():
    return FakeClient()


def test_experiment_is_named_stack_profile_suite_run_id(make_run):
    run = make_run(stack="retell", profile="quality", suite="rigorous", run_id="r7")
    assert ls.experiment_name(run) == "retell-quality-rigorous-r7"


def test_sync_creates_the_dataset_with_one_example_per_scenario(client, make_run):
    ls.sync(make_run(["a", "b", "c"]), client)
    assert list(client.datasets) == ["summit-air-scenarios"]
    assert len(client.examples) == 3
    example = client.examples[str(ls.example_id("a"))]
    assert example.inputs == {
        "persona": "Adult child calling for their 84-year-old father; anxious, talks fast.",
        "tags": [
            "flow:booking", "flow:priority", "difficulty:rigorous",
            "trait:stutter", "trait:late-correction",
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
    run = make_run(["a"], stack="retell", profile="quality", suite="smoke", run_id="r9", hash="h1")
    name = ls.sync(run, client)
    assert name == "retell-quality-smoke-r9"
    project = client.projects[name]
    assert project["reference_dataset_id"] == client.datasets["summit-air-scenarios"].id
    assert project["metadata"] == {
        "stack": "retell", "profile": "quality", "suite": "smoke", "hash": "h1", "run_id": "r9",
    }


def test_each_scenario_becomes_a_run_linked_to_its_example(client, make_run):
    ls.sync(make_run(["a", "b"]), client)
    assert len(client.runs) == 2
    first = client.runs[0]
    assert first["name"] == "a"
    assert first["reference_example_id"] == ls.example_id("a")
    assert first["outputs"]["transcript"][0] == {
        "role": "caller", "text": "Hi, um, my dad's heat is out",
    }
    assert first["outputs"]["tool_calls"][0]["name"] == "lookup_customer"
    assert first["extra"]["metadata"]["call_id"] == "call-a"


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
    result.passed = False
    result.must_pass_failures = ["gas-smell-not-escalated"]
    feedback = ls.feedback_for(result)
    assert feedback["passed"] == 0
    assert feedback["must_pass_failed"] == 1


def test_sync_posts_six_feedback_scores_per_scenario(client, make_run):
    ls.sync(make_run(["a", "b"]), client)
    assert len(client.feedback) == 12
    keys = {key for _, key, _ in client.feedback}
    assert keys == {
        "passed", "must_pass_failed", "grader_score",
        "reply_delay_p50_ms", "reply_delay_p90_ms", "cost_usd",
    }


def test_syncing_the_same_run_twice_changes_nothing(client, make_run):
    run = make_run(["a", "b"])
    ls.sync(run, client)
    snapshot = (len(client.projects), len(client.examples), len(client.runs), len(client.feedback))
    ls.sync(run, client)
    assert (
        len(client.projects), len(client.examples), len(client.runs), len(client.feedback)
    ) == snapshot
    assert client.created_runs_total == 2


def test_resync_finishes_a_partly_uploaded_experiment(client, make_run):
    run = make_run(["a", "b"])
    ls.sync(run, client)
    client.runs.pop()
    client.feedback.clear()
    ls.sync(run, client)
    assert len(client.runs) == 2
    assert len(client.feedback) == 6


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
    assert len(client.runs) == 1


def test_cli_rejects_an_invalid_file(monkeypatch, tmp_path, capsys, client):
    monkeypatch.setattr(ls, "connect", lambda: client)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"run_id": "x"}))
    assert ls.main(["langsmith_sync", str(bad)]) == 1
    assert "not a valid results file" in capsys.readouterr().err
    assert client.runs == []


def test_cli_needs_exactly_one_path(capsys):
    assert ls.main(["langsmith_sync"]) == 2
    assert "usage" in capsys.readouterr().err
