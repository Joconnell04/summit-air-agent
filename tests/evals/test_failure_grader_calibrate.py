"""The failure grader's calibration scoring (evals/failure_grader/calibrate.py): a call counts as
flagged by the majority of its runs, borderline labels are scored apart, and the label file holds
only known calls with a reason each."""

import yaml

from evals.failure_grader import calibrate

CASES = [
    {"id": "a", "label": "fail"},
    {"id": "b", "label": "fail", "borderline": True},
    {"id": "c", "label": "ok"},
]


def results(flags: dict[str, list[bool]]) -> list[dict]:
    return [
        {"id": cid, "run": run, "flagged": flagged}
        for cid, runs in flags.items()
        for run, flagged in enumerate(runs)
    ]


def test_majority_of_runs_decides_and_borderline_is_scored_apart():
    got = calibrate.score(results({"a": [True, True, False], "b": [False] * 3, "c": [True, False, False]}), CASES)
    assert got["all"] == {"tp": 1, "fp": 0, "fn": 1, "precision": 1.0, "recall": 0.5}
    assert got["clear"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}


def test_one_run_is_scored_on_its_own():
    got = calibrate.score(results({"a": [True, False], "b": [True, True], "c": [False, True]}), CASES, run=1)
    assert got["all"]["fp"] == 1 and got["all"]["tp"] == 1


def test_every_label_is_known_and_explained():
    calls = yaml.safe_load((calibrate.HERE / "labels.yaml").read_text())["calls"]
    assert len({c["id"] for c in calls}) == len(calls)
    for c in calls:
        assert c["label"] in ("fail", "ok"), c["id"]
        assert c["why"].strip(), c["id"]
        assert c["id"].startswith("call_")
        if c["label"] == "fail":
            assert c["kind"] in calibrate.grader.KINDS, c["id"]
