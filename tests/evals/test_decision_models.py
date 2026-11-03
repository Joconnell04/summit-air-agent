"""The side-model quick run (evals/decision-models): offline, no model is called."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dm_run", ROOT / "evals/decision-models/run.py")
dm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dm)
TAGS = {"explicit", "soft", "hard_negative", "other_negative", "stt_noise", "borderline"}


def test_the_line_file_is_valid():
    lines = dm.load_lines("vulnerable_person")
    assert len({x["id"] for x in lines}) == len(lines)
    for line in lines:
        assert isinstance(line["label"], bool) and line["text"].strip()
        assert set(line["tags"]) <= TAGS, line["id"]
        assert ("explicit" in line["tags"] or "soft" in line["tags"]) == line["label"], line["id"]


def test_the_rules_baseline_reads_the_tenant_phrase_rules():
    rules = dm.rules_system()
    assert rules("my 85 year old mother lives with me") == 1.0
    assert rules("the condenser outside is frozen over") == 0.0


def test_scores_join_the_side_model_to_the_rules_with_or():
    lines = [
        {"id": "a", "label": True, "tags": ["soft"]},
        {"id": "b", "label": True, "tags": ["explicit"]},
        {"id": "c", "label": False, "tags": ["hard_negative"]},
        {"id": "d", "label": False, "tags": ["borderline"]},
    ]
    answers = [
        {"id": "a", "p": 0.9, "rules": 0.0, "status": "ok", "ms": 100},
        {"id": "b", "p": None, "rules": 1.0, "status": "error: ConnectError", "ms": 5},
        {"id": "c", "p": 0.7, "rules": 0.0, "status": "ok", "ms": 300},
        {"id": "d", "p": 0.9, "rules": 0.0, "status": "ok", "ms": 200},
    ]
    s = dm.scores(answers, lines, 0.5)
    assert s["alone"]["recall"] == 0.5 and s["with_rules"]["recall"] == 1.0
    assert s["fixes"] == ["a"] and s["new_alarms"] == ["c"], "the borderline line is not scored"
    assert s["failed"] == 1 and s["ms_max"] == 300


def test_mcnemar_is_exact():
    assert dm.mcnemar(0, 0) == 1.0
    assert dm.mcnemar(13, 0) == pytest.approx(2 / 2**13)


def test_a_saved_run_has_the_shape_the_summary_reads(tmp_path):
    runs = sorted((ROOT / "evals/decision-models/results").glob("*/results.json"))
    for path in runs:
        run = json.loads(path.read_text())
        assert {"run_id", "question", "threshold", "systems", "cost_usd"} <= set(run)
