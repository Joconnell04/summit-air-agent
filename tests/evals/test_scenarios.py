import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.text import phone_key
from evals.scenario import ROOT, Scenario, ScenarioError, load, load_all, must_pass_spec_ids
from evals.suites import select
from evals.world import SEED_START, TENANT_DIR, World, resolve_caller, seeded_db
from servicetitan_mock.seed import build_world

SCENARIOS = load_all()
SPEC = (ROOT / "docs" / "spec.md").read_text()


def test_every_scenario_file_loads_and_ids_are_unique():
    assert len(SCENARIOS) >= 40
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_every_journey_has_a_scenario():
    covered = {j for s in SCENARIOS for j in s.journeys}
    assert {f"J{i}" for i in range(1, 15)} <= covered


def test_every_must_pass_line_tested_by_eval_has_a_scenario():
    eval_lines = set(re.findall(r"^\| (S-\d+[a-z]?) \*\*M\*\* \|[^|]*\| [^|]*[Ee]val", SPEC,
                                flags=re.MULTILINE))
    covered = {s for sc in SCENARIOS for s in sc.spec}
    assert eval_lines and eval_lines <= covered, eval_lines - covered


def test_spec_ids_in_scenarios_exist():
    known = set(re.findall(r"^\| (S-\d+[a-z]?)", SPEC, flags=re.MULTILINE))
    for s in SCENARIOS:
        assert set(s.spec) <= known, s.id


def test_every_difficulty_is_represented():
    assert {s.difficulty for s in SCENARIOS} == {"easy", "standard", "rigorous", "adversarial"}


def test_smoke_is_small_and_covers_each_must_pass_rule():
    smoke, samples = select(SCENARIOS, "smoke")
    assert 6 <= len(smoke) <= 10 and samples == 1
    flows = {f for s in smoke for f in s.flow}
    assert {"booking", "safety", "commercial", "injection", "priority", "membership"} <= flows
    specs = {x for s in smoke for x in s.spec}
    assert {"S-17", "S-05", "S-28", "S-39", "S-26"} <= specs


def test_must_pass_ids_come_from_the_spec():
    assert {"S-05", "S-17", "S-18", "S-19", "S-26", "S-27", "S-28", "S-39"} <= must_pass_spec_ids()
    assert "S-29" not in must_pass_spec_ids()


def test_literal_caller_numbers_are_not_seeded_customers(tmp_path):
    path = tmp_path / "w.db"
    build_world(path, TENANT_DIR, SEED_START)
    conn = sqlite3.connect(path)
    seeded = {r[0] for r in conn.execute("SELECT phone_key FROM contacts WHERE phone_key != ''")}
    for s in SCENARIOS:
        if s.caller_id != "unknown" and not s.caller_id.startswith("seed:"):
            assert phone_key(s.caller_id) not in seeded, f"{s.id}: use seed:<key> for a known caller"


def test_seed_references_resolve(tmp_path):
    _, scenarios = seeded_db(tmp_path)
    for s in SCENARIOS:
        assert len(phone_key(resolve_caller(s.caller_id, scenarios))) == 10, s.id


def test_two_people_mind_change_asks_for_windows_the_seed_has_open(tmp_path):
    # The couple's call is High priority (62 F inside). Wednesday has no non-urgent window in the
    # seed, and a caller who insists on a closed day can only end in a flag, never the booking the
    # scenario checks for.
    scenario = next(s for s in SCENARIOS if s.id == "two-people-mind-change")
    world = World(tmp_path / "w", seeded_db(tmp_path / "seed"),
                  datetime(2026, 11, 2, 9, 0, tzinfo=ZoneInfo("America/New_York")))
    world.server.start("c1", scenario.caller_id)
    facts = {"issue": "furnace runs, house won't warm", "who_home": "Carla, healthy",
             "inside_temp_f": 62.0, "temp_falling_fast": False}

    def windows(day: str) -> list[str]:
        found = world.server.tool("find_slots", "c1", {"job_type": "no_heat", "day": day,
                                                       "facts": facts})
        assert found["data"]["priority"]["level"] == "High"
        return [w["window"] for w in found["data"]["windows"]]

    assert "Tuesday morning" in scenario.hidden_facts["final_choice"]
    assert "morning" in windows("2026-11-03")
    assert "afternoon" in windows("2026-11-05")


def test_no_em_dashes_in_scenarios():
    for path in (ROOT / "evals" / "scenarios").glob("*.yaml"):
        assert "\u2014" not in path.read_text(), path.name


def write(tmp_path, name, text):
    path = tmp_path / f"{name}.yaml"
    path.write_text(text)
    return path


BASE = """id: {id}
flow: [booking]
difficulty: easy
journeys: [J1]
persona: A caller.
opening: Hi.
must:
  - {check}
"""


def test_a_file_whose_id_differs_from_its_name_is_rejected(tmp_path):
    path = write(tmp_path, "other", BASE.format(id="one", check="booking: none"))
    with pytest.raises(ScenarioError, match="does not match"):
        load(path)


@pytest.mark.parametrize("check", ["bookings: none", "booking: maybe", "safety_lines: said"])
def test_an_unknown_check_or_argument_is_rejected(tmp_path, check):
    path = write(tmp_path, "one", BASE.format(id="one", check=check))
    with pytest.raises(ScenarioError):
        load(path)


def test_tags_include_flow_difficulty_trait_journey_spec_and_must_pass():
    s = Scenario.model_validate({
        "id": "x", "flow": ["safety"], "difficulty": "rigorous", "traits": ["stutter"],
        "journeys": ["J3"], "spec": ["S-17"], "tags": ["smoke"], "persona": "p", "opening": "o",
        "must": [{"booking": "none"}],
    })
    assert s.all_tags() >= {"flow:safety", "difficulty:rigorous", "trait:stutter", "journey:J3",
                            "spec:S-17", "smoke", "must-pass", "id:x"}
    assert s.start_time.isoformat() == "2026-11-02T09:00:00-05:00"
