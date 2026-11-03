"""The runner with fake models against the real tool server and mock, so scoring is tested offline."""

import pytest
from fakes import ScriptedModel, grade_reply, say, use

from evals.replay import diff, replay, tool_sequence
from evals.run import Harness, Models, build_run
from evals.scenario import Scenario

# The worker says the greeting from the tenant's `voice` lines with no model step, so the in-process
# agent does too and the scripted agent model is never asked for one.
GREETING = "Thanks for calling Summit Air, this is the virtual assistant. How can I help?"


def scenario(**overrides) -> Scenario:
    data = {
        "id": "test-call",
        "flow": ["booking"],
        "difficulty": "easy",
        "journeys": ["J1"],
        "persona": "A test caller.",
        "opening": "Hi, my heat is out.",
        "must": [{"booking": "none"}],
        "max_turns": 4,
    }
    return Scenario.model_validate({**data, **overrides})


@pytest.fixture(scope="module")
def seed_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("harness")


def harness(seed_dir, tmp_path, agent, caller, grader, **kw):
    h = Harness(Models(agent, caller, grader), "livekit", seed_dir, **kw)
    h.workdir = tmp_path
    return h


def run(seed_dir, tmp_path, s, agent_replies, caller_replies, grade=None, **kw):
    agent = ScriptedModel(agent_replies)
    caller = ScriptedModel(caller_replies)
    grader = ScriptedModel([grade or grade_reply()])
    return harness(seed_dir, tmp_path, agent, caller, grader, **kw).run(s), agent, caller, grader


def test_a_call_that_meets_its_checks_passes(seed_dir, tmp_path):
    result, *_ = run(
        seed_dir, tmp_path, scenario(), [say("Sorry to hear that. Bye for now.")], [say("[END]")]
    )
    assert result.outcome == "pass"
    assert result.grader_score == 4
    assert [t.role for t in result.transcript] == ["agent", "caller", "agent"]
    assert result.transcript[0].text == GREETING
    assert result.call_id.startswith("eval-test-call-1-")


def test_a_failed_check_fails_the_call_and_the_grader_cannot_rescue_it(seed_dir, tmp_path):
    result, *_ = run(
        seed_dir,
        tmp_path,
        scenario(must=[{"flag": "person"}]),
        [say("Okay.")],
        [say("[END]")],
        grade=grade_reply(5),
    )
    assert result.outcome == "fail"
    assert result.check_failures == ["flag: no person was flagged"]
    assert result.grader_score == 5


def test_the_grader_can_fail_a_call_the_code_passed(seed_dir, tmp_path):
    result, *_ = run(
        seed_dir,
        tmp_path,
        scenario(),
        [say("Okay.")],
        [say("[END]")],
        grade=grade_reply(2, "fail", "Quoted a price."),
    )
    assert result.outcome == "fail"
    assert result.check_failures == ["grader: Quoted a price."]


def test_must_pass_failures_are_listed_only_for_must_pass_scenarios(seed_dir, tmp_path):
    must = scenario(spec=["S-26"], must=[{"booking": "one"}])
    result, *_ = run(seed_dir, tmp_path, must, [say("Okay.")], [say("[END]")])
    assert result.must_pass_failures == ["booking: 0 booking(s) made, expected 1"]
    plain = scenario(spec=["S-29"], must=[{"booking": "one"}])
    result, *_ = run(seed_dir, tmp_path, plain, [say("Okay.")], [say("[END]")])
    assert result.outcome == "fail" and result.must_pass_failures == []


def test_a_looping_caller_is_an_error_not_a_fail(seed_dir, tmp_path):
    s = scenario(max_turns=8, must=[{"flag": "person"}])
    result, _, _, grader = run(seed_dir, tmp_path, s, [], [say("Hello?")] * 5)
    assert result.outcome == "error"
    assert "repeated the same line" in result.error
    assert result.grader_score is None
    # The miss is kept for the record, but a call that never finished has no verdict.
    assert result.check_failures == ["flag: no person was flagged"]
    assert grader.calls == []


def test_a_rule_broken_before_the_caller_looped_is_still_a_fail(seed_dir, tmp_path):
    s = scenario(
        max_turns=8, spec=["S-41"], must=[{"agent_never_says": r"\$\s?\d"}, {"flag": "person"}]
    )
    result, *_ = run(seed_dir, tmp_path, s, [say("That will be $89.")], [say("Hello?")] * 5)
    assert result.outcome == "fail"
    assert result.check_failures == [
        "agent_never_says: the agent said /\\$\\s?\\d/: 'That will be $89.'"
    ]
    assert "repeated the same line" in result.error


def test_a_call_that_never_finishes_is_an_error(seed_dir, tmp_path):
    lines = [say(f"line {i}") for i in range(10)]
    result, *_ = run(seed_dir, tmp_path, scenario(max_turns=3), [], lines)
    assert result.outcome == "error"
    assert "did not finish within 3" in result.error


def test_a_broken_harness_is_an_error(seed_dir, tmp_path):
    def broken(s, call_id):
        raise RuntimeError("tool server down")

    result, *_ = run(seed_dir, tmp_path, scenario(), [], [], open_agent=broken)
    assert result.outcome == "error"
    assert result.error == "harness: RuntimeError: tool server down"


def test_a_broken_grader_never_hides_a_failed_check(seed_dir, tmp_path):
    s = scenario(spec=["S-26"], must=[{"booking": "one"}])
    result, *_ = run(seed_dir, tmp_path, s, [say("Okay.")], [say("[END]")], grade=say("junk"))
    assert result.outcome == "fail"
    assert result.must_pass_failures == ["booking: 0 booking(s) made, expected 1"]
    assert result.grader_score is None
    assert result.grader_notes.startswith("grader unavailable")


def test_unreadable_grader_output_is_an_error(seed_dir, tmp_path):
    result, *_ = run(
        seed_dir, tmp_path, scenario(), [say("Okay.")], [say("[END]")], grade=say("five stars")
    )
    assert result.outcome == "error"
    assert result.error.startswith("grader:")


def test_safety_lines_come_from_the_server_not_the_model(seed_dir, tmp_path):
    s = scenario(
        opening="I smell gas in the kitchen.", must=[{"safety_lines": "said_first"}], spec=["S-17"]
    )
    result, agent, *_ = run(seed_dir, tmp_path, s, [], [say("Okay, going out. Bye."), say("[END]")])
    assert result.outcome == "pass", result.check_failures
    assert result.transcript[2].text.startswith("Please get everyone out of the house now.")
    # Only the reply to the goodbye; the greeting and the safety lines needed no model call.
    assert len(agent.calls) == 1


def test_a_tool_call_reaches_the_server_and_is_recorded(seed_dir, tmp_path):
    s = scenario(must=[{"tool_ok": "check_service_area"}])
    replies = [
        use("check_service_area", "Let me check.", zip="11231"),
        say("Good news, we cover your area."),
    ]
    result, *_ = run(seed_dir, tmp_path, s, replies, [say("[END]")])
    assert result.outcome == "pass", result.check_failures
    assert [c.name for c in result.tool_calls] == ["check_service_area"]
    assert result.turn_timings[0].kind == "lookup"
    assert result.turn_timings[0].second_hop_ms is not None
    assert result.transcript[-1].text == "Let me check. Good news, we cover your area."


def test_an_out_of_area_refusal_is_recorded_by_zip(seed_dir, tmp_path):
    s = scenario(must=[{"out_of_area": "11501"}, {"booking": "none"}])
    replies = [use("check_service_area", zip="11501"), say("Sorry, that's outside our area.")]
    result, agent, *_ = run(seed_dir, tmp_path, s, replies, [say("[END]")])
    assert result.outcome == "pass", result.check_failures
    assert '"refused": "out_of_area"' in agent.calls[1]["messages"][-1]["content"][0]["content"]


def test_server_hints_reach_the_model(seed_dir, tmp_path):
    s = scenario(opening="Hi, I manage a restaurant in Bushwick and the heat is out.")
    _, agent, *_ = run(
        seed_dir, tmp_path, s, [say("Sorry, we only work on homes.")], [say("[END]")]
    )
    assert "can't book" in agent.calls[0]["messages"][-1]["content"]
    assert "[server]" in agent.calls[0]["messages"][-1]["content"]


def test_caller_lines_are_roughened_for_harder_scenarios(seed_dir, tmp_path):
    s = scenario(difficulty="standard", opening="My ZIP is 11231.")
    result, *_ = run(seed_dir, tmp_path, s, [say("Thanks.")], [say("[END]")])
    assert result.transcript[1].text == "my zip is one one two three one"


def test_replayed_turns_are_used_word_for_word(seed_dir, tmp_path):
    agent = ScriptedModel([say("Okay."), say("Bye.")])
    h = harness(seed_dir, tmp_path, agent, ScriptedModel([]), ScriptedModel([grade_reply()]))
    result = h.run(scenario(difficulty="rigorous"), caller_turns=["Line ONE.", "line two"])
    assert [t.text for t in result.transcript if t.role == "caller"] == ["Line ONE.", "line two"]


def test_cost_adds_the_agent_the_caller_and_the_grader(seed_dir, tmp_path):
    result, *_ = run(
        seed_dir,
        tmp_path,
        scenario(),
        [say("Okay.", cost=0.01)],
        [say("[END]", cost=0.002)],
        grade=grade_reply(),
    )
    # reply 0.01 + caller 0.002 + grader 0.001; the greeting is spoken from code and costs nothing
    assert result.cost_usd == pytest.approx(0.013)


def test_each_scenario_gets_its_own_call_and_database(seed_dir, tmp_path):
    first, *_ = run(seed_dir, tmp_path, scenario(), [say("Okay.")], [say("[END]")])
    second, *_ = run(seed_dir, tmp_path, scenario(), [say("Okay.")], [say("[END]")])
    assert first.call_id != second.call_id
    assert (tmp_path / first.call_id / "tool_server.db").exists()


def test_build_run_hashes_and_names_the_run(seed_dir, tmp_path):
    result, *_ = run(
        seed_dir, tmp_path, scenario(id="tune-up-known-caller"), [say("Okay.")], [say("[END]")]
    )
    built = build_run("smoke", [], "livekit", "fast", [result])
    assert built.run_id.endswith("-livekit-fast-smoke")
    assert len(built.hash) == 16


def test_replay_reruns_the_recorded_caller_lines_and_finds_a_changed_tool_sequence(
    seed_dir, tmp_path
):
    s = scenario(id="tune-up-known-caller", must=[{"tool_ok": "check_service_area"}])
    first, *_ = run(
        seed_dir, tmp_path, s, [use("check_service_area", zip="11231"), say("Yes.")], [say("[END]")]
    )
    same = harness(
        seed_dir,
        tmp_path,
        ScriptedModel([use("check_service_area", zip="11231"), say("Yes.")]),
        ScriptedModel([]),
        ScriptedModel([grade_reply()]),
    )
    assert diff(tool_sequence(first), tool_sequence(replay(first, same))) == []
    changed = harness(
        seed_dir,
        tmp_path,
        ScriptedModel([say("Yes.")]),
        ScriptedModel([]),
        ScriptedModel([grade_reply()]),
    )
    assert diff(tool_sequence(first), tool_sequence(replay(first, changed))) == [
        "  step 1: was check_service_area, now -"
    ]
