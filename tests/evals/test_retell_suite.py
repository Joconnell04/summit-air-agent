"""The Retell simulation suite (evals/retell/): case files, porting, and the guards that keep its
writes off the live agent and the live world."""
import collections

import pytest

from evals.checks import safety_config
from evals.retell import cases as case_files
from evals.retell import suite
from evals.scenario import SCENARIOS_DIR
from evals.scenario import load as load_scenario
from evals.world import seeded_db


def test_every_case_file_loads_with_known_tags_and_checks():
    cases = case_files.load_all()
    assert 40 <= len(cases) <= 60
    for case in cases:
        case.checks()  # the scenario's checks, or the case's own, all known to evals/checks.py


def test_cases_cover_the_must_pass_and_safety_scenarios_first():
    ported = {c.scenario for c in case_files.load_all() if c.scenario}
    must = case_files.must_pass_spec_ids()
    for path in SCENARIOS_DIR.glob("*.yaml"):
        scenario = load_scenario(path)
        # Two must-pass scenarios depend on a clock the batch can't set; the rest are all here.
        if set(scenario.spec) & must and scenario.id not in ("open-visit-during-window",):
            assert scenario.id in ported, f"must-pass scenario {scenario.id} has no Retell case"


def test_cases_call_from_numbers_no_other_case_makes_up(tmp_path):
    # Every case runs at once in one world: a shared made-up number would find another case's
    # new customer record. A seeded caller (Maria Delgado) may call in more than one case.
    seeded = suite.seed_numbers(seeded_db(tmp_path)[1])
    counts = collections.Counter(c.caller_id for c in case_files.load_all())
    shared = {n for n, k in counts.items() if k > 1 and suite.digits(n) not in seeded}
    assert not shared


def test_the_copy_points_every_function_at_the_eval_bridge():
    tools = [{"name": "book_appointment", "type": "custom", "url": suite.LIVE_TOOLS + "book_appointment"},
             {"name": "hang_up", "type": "end_call"}]
    out = suite.eval_tools(tools)
    assert out[0]["url"] == suite.EVAL_TOOLS + "book_appointment"
    assert out[1] == tools[1]
    assert tools[0]["url"].startswith(suite.LIVE_TOOLS)  # the live agent's list is not changed


def test_a_function_that_reaches_anywhere_else_stops_the_copy():
    with pytest.raises(suite.SuiteError):
        suite.eval_tools([{"name": "x", "type": "custom", "url": "https://example.com/hook"}])


class FakeRetell:
    def __init__(self, live: dict, agent: dict):
        self.live, self.agent, self.sent = live, agent, []

    def request(self, method, path, json=None, params=None):
        assert suite.allowed(method, path), (method, path)
        self.sent.append((method, path, json))
        if path.startswith("/get-agent/"):
            return self.agent
        if path.startswith("/get-retell-llm/"):
            return self.live
        if path == "/create-retell-llm":
            return {"llm_id": "llm_copy"}
        return {}


def live_llm():
    return {"llm_id": suite.LIVE_LLM, "version": 3, "general_prompt": "Be kind.",
            "begin_message": "Hi", "model": "claude-5.5-haiku",
            "general_tools": [{"name": "find_slots", "type": "custom",
                               "url": suite.LIVE_TOOLS + "find_slots"}]}


def test_copying_reads_the_live_llm_and_writes_only_the_copy():
    agent = {"response_engine": {"type": "retell-llm", "llm_id": suite.LIVE_LLM, "version": 3}}
    api = FakeRetell(live_llm(), agent)
    state = {"copy_llm": "llm_copy", "cases": {}}
    assert suite.copy_live(api, state) == "llm_copy"
    writes = [(m, p) for m, p, _ in api.sent if m != "GET"]
    assert writes == [("PATCH", "/update-retell-llm/llm_copy")]
    body = api.sent[-1][2]
    assert body["general_prompt"] == "Be kind."
    assert body["general_tools"][0]["url"] == suite.EVAL_TOOLS + "find_slots"
    assert ("GET", f"/get-retell-llm/{suite.LIVE_LLM}", None) in api.sent


def test_the_live_llm_is_never_taken_as_the_copy():
    agent = {"response_engine": {"type": "retell-llm", "llm_id": suite.LIVE_LLM, "version": 3}}
    with pytest.raises(suite.SuiteError):
        suite.copy_live(FakeRetell(live_llm(), agent), {"copy_llm": suite.LIVE_LLM, "cases": {}})


def test_list_endpoints_are_the_versioned_ones():
    assert suite.allowed("GET", "/v2/list-test-case-definitions")
    assert suite.allowed("GET", "/v2/list-test-runs/test_batch_1")
    assert not suite.allowed("GET", "/list-test-case-definitions")
    assert not suite.allowed("GET", "/list-test-runs/test_batch_1")
    assert not suite.allowed("DELETE", "/delete-retell-llm/" + suite.LIVE_LLM)


def test_safety_criteria_carry_the_lines_word_for_word():
    scenario = load_scenario(SCENARIOS_DIR / "gas-smell-in-passing.yaml")
    case = case_files.port(scenario, "(332) 555-0110")
    gas = safety_config()["kinds"]["gas"]["lines"]
    assert any(f'"{gas}"' in m and "first thing" in m for m in case["metrics"])


def test_a_should_line_reads_as_a_sentence_about_the_agent():
    assert case_files.as_criterion("Says the window plainly.") == "The agent says the window plainly."
    assert case_files.as_criterion("Never asks twice.") == "The agent never asks twice."
    assert case_files.as_criterion("The agent stops.") == "The agent stops."


def test_the_calling_number_replaces_the_scenarios_placeholder():
    scenario = load_scenario(SCENARIOS_DIR / "arrival-before-ten.yaml")
    prompt = case_files.user_prompt(scenario, "(917) 555-0171")
    assert "(917) 555-0171 (the number you're calling from)" in prompt
    assert "number being called from" not in prompt


def test_who_ended_the_call_is_not_scored_on_a_simulation():
    case = case_files.Case(id="x", title="x", tags=["booking"], user_prompt="p", metrics=["m"],
                           must=[{"ended_by": "caller"}, {"booking": "none"}])
    trace = {"call": {}, "events": [], "bookings": []}
    failures, skipped = suite.code_checks(case, trace, safety_config())
    assert failures == [] and skipped == ["ended_by"]


def test_a_number_is_found_that_no_case_uses():
    taken = {"(678) 555-0200", "(678) 555-0201"}
    assert suite.free_number(taken) == "(678) 555-0202"
