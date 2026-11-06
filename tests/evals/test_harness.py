"""The runner with fake models against the real tool server and mock, so scoring is tested offline."""

import pytest
from fakes import ScriptedModel, grade_reply, say, use

from evals.replay import diff, replay, tool_sequence
from evals.run import Harness, Models, build_run
from evals.scenario import Scenario

# The worker says the greeting from the tenant's `voice` lines with no model step, so the in-process
# agent does too and the scripted agent model is never asked for one.
GREETING = (
    "Hi there, thanks for calling Summit Air! I'm Brian, the virtual assistant. "
    "How can I help?"
)


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


def test_the_caller_knows_the_number_they_are_calling_from(seed_dir, tmp_path):
    """Eval round 1, gas-furnace-no-trigger: a caller who knew only "the number being called from"
    rejected a correct read-back of it."""
    s = scenario(caller_id="(332) 555-0131")
    _, _, caller, _ = run(seed_dir, tmp_path, s, [say("What's the best number?")], [say("[END]")])
    assert "calling_from: (332) 555-0131" in caller.calls[0]["system"]


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


def test_a_grader_fail_on_a_must_pass_scenario_is_a_fail_but_not_a_must_pass_failure(
    seed_dir, tmp_path
):
    s = scenario(spec=["S-26"])
    result, *_ = run(
        seed_dir,
        tmp_path,
        s,
        [say("Okay.")],
        [say("[END]")],
        grade=grade_reply(2, "fail", "A long turn."),
    )
    assert result.outcome == "fail"
    assert result.check_failures == ["grader: A long turn."]
    assert result.must_pass_failures == []


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
    # A price is now dropped before it's spoken (prompts/assemble.py), so the broken rule here is
    # claiming to be a person.
    s = scenario(
        max_turns=8, spec=["S-41"], must=[{"agent_never_says": r"\bhuman\b"}, {"flag": "person"}]
    )
    result, *_ = run(seed_dir, tmp_path, s, [say("Yes, I'm a human.")], [say("Hello?")] * 5)
    assert result.outcome == "fail"
    assert result.check_failures == [
        'agent_never_says: the agent said /\\bhuman\\b/: "Yes, I\'m a human."'
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
    # A reply of substance: a bare "Okay." is asked again (issue #149), which would add a call.
    result, agent, *_ = run(
        seed_dir,
        tmp_path,
        s,
        [say("Take care out there.")],
        [say("Okay, going out. Bye."), say("[END]")],
    )
    assert result.outcome == "pass", result.check_failures
    assert result.transcript[2].text.startswith("If you're smelling gas, the safest thing is")
    # Only the reply to the goodbye; the greeting and the safety lines needed no model call.
    assert len(agent.calls) == 1


def test_a_tool_call_reaches_the_server_and_is_recorded(seed_dir, tmp_path):
    s = scenario(must=[{"tool_ok": "check_service_area"}], opening="Hi, my heat is out in 11231.")
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
    s = scenario(
        must=[{"out_of_area": "11501"}, {"booking": "none"}],
        opening="Hi, my heat is out, I'm in 11501.",
    )
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
        [say("We're open until 8 tonight.", cost=0.01)],
        [say("[END]", cost=0.002)],
        grade=grade_reply(),
    )
    # reply 0.01 + caller 0.002 + grader 0.001; the greeting is spoken from code and costs nothing
    assert result.cost_usd == pytest.approx(0.013)
    assert result.agent_cost_usd == pytest.approx(0.01)


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
    assert len(built.prompt_hash) == 16 and built.prompt_hash != built.hash
    assert built.model == "claude-haiku-5-5"


def test_a_scored_call_records_which_checks_ran(seed_dir, tmp_path):
    s = scenario(must=[{"booking": "none"}, {"flag": "person"}, {"booking": "none"}])
    result, *_ = run(seed_dir, tmp_path, s, [say("Okay.")], [say("[END]")])
    assert result.checks == ["booking", "flag"]
    assert result.check_failures == ["flag: no person was flagged"]


def test_an_error_run_records_no_checks(seed_dir, tmp_path):
    lines = [say(f"line {i}") for i in range(10)]
    result, *_ = run(seed_dir, tmp_path, scenario(max_turns=3), [], lines)
    assert result.outcome == "error" and result.checks == []


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


def test_a_stack_other_than_livekit_is_opened_through_its_voice_layers_adapter(
    seed_dir, tmp_path, monkeypatch
):
    opened = []

    class Adapter:
        @staticmethod
        def open_agent(call_id, caller_id):
            opened.append((call_id, caller_id))
            raise RuntimeError("stop here")

    monkeypatch.setattr("evals.run.load_adapter", lambda stack: Adapter)
    h = Harness(Models(ScriptedModel([]), ScriptedModel([]), ScriptedModel([])), "retell", seed_dir)
    h.workdir = tmp_path
    result = h.run(scenario())
    assert len(opened) == 1 and opened[0][0].startswith("eval-test-call-1-")
    assert result.outcome == "error" and "stop here" in result.error


def test_retell_runs_go_one_scenario_at_a_time(capsys):
    from evals.run import workers_for

    assert workers_for("retell", 4) == 1
    assert "one scenario at a time" in capsys.readouterr().out
    assert workers_for("livekit", 4) == 4


def test_an_earlier_dropped_call_is_on_the_record_but_the_greeting_is_the_usual_one(
    seed_dir, tmp_path
):
    """Eval round 2, seed 6: the scenario starts two minutes after a call from the same number
    dropped, so the server links it. There is no "we got cut off" greeting (Jackson, call
    call_7255ab47e30637f207e49f5bdeb), but the agent still has the earlier call's turns."""
    s = scenario(
        caller_id="(347) 555-0166",
        earlier_call={
            "minutes_ago": 2,
            "turns": [
                {"agent": GREETING},
                {"caller": "Hi, this is Tomasz Nowak. The heat's out."},
                {"agent": "Oh no. What's the address?"},
                {"caller": "145 Java Street, 11222."},
            ],
        },
    )
    result, agent, *_ = run(seed_dir, tmp_path, s, [say("Okay.")], [say("[END]")])
    assert result.transcript[0].text == GREETING
    # Summit Air doesn't link calls (DECISIONS.md 118): the earlier call isn't in the context.
    assert "145 Java Street, 11222." not in agent.calls[0]["system"]


def test_the_scheduling_system_down_fails_bookings_but_not_lookups(seed_dir, tmp_path):
    s = scenario(fsm_down="booking", must=[{"tool_ok": "find_slots"}])
    replies = [use("find_slots", job_type="tune_up"), say("I've got openings.")]
    result, *_ = run(seed_dir, tmp_path, s, replies, [say("[END]")])
    assert result.outcome == "pass", result.check_failures


def test_fsm_down_answers_503_only_for_what_it_fails():
    from evals.world import FsmDown

    class Client:
        def request(self, method, url, **kw):
            return "passed"

    booking = FsmDown(Client(), "booking")
    assert booking.request("POST", "/jpm/v2/tenant/1/jobs").status_code == 503
    assert booking.request("POST", "/crm/v2/tenant/1/customers").status_code == 503
    assert booking.request("GET", "/crm/v2/tenant/1/customers") == "passed"
    assert booking.request("POST", "/dispatch/v2/tenant/1/capacity") == "passed"
    assert booking.request("POST", "/connect/token") == "passed"
    assert FsmDown(Client(), "all").request("GET", "/crm/v2/tenant/1/customers").status_code == 503


DEMAND = "Put me through to a manager right now, this is ridiculous."


def handoff_scenario(**overrides) -> Scenario:
    data = {"opening": DEMAND, "must": [{"handoff": {"role": "office", "status": "handed_off"}}]}
    return scenario(**{**data, **overrides})


def test_a_demand_is_handed_off_by_code_and_the_call_ends_as_a_transfer(seed_dir, tmp_path):
    """The agent model is never asked: the server decides on the caller's turn and the in-process
    agent says the line and reports the transfer, as the worker does."""
    result, agent, _, _ = run(seed_dir, tmp_path, handoff_scenario(transfer="sent"), [], [])
    assert result.outcome == "pass", result.check_failures
    assert result.transcript[-1].text == "Transferring you to a person now."
    assert agent.calls == []


def test_a_transfer_that_fails_brings_the_agent_back_with_the_callback_line(seed_dir, tmp_path):
    s = handoff_scenario(
        transfer="fails", must=[{"handoff": {"status": "failed"}}, {"callback": "promised"}]
    )
    result, *_ = run(seed_dir, tmp_path, s, [say("Is there anything else?")], [say("[END]")])
    assert result.outcome == "pass", result.check_failures
    assert result.transcript[2].text == (
        "Transferring you to a person now. I couldn't reach anyone just now, so I've let the team "
        "know and someone will call you by 9:30 AM."
    )


def test_without_transfer_set_the_shells_numbers_are_never_read(seed_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    s = handoff_scenario(must=[{"handoff": {"status": "not_dialed"}}])
    result, *_ = run(seed_dir, tmp_path, s, [say("Is there anything else?")], [say("[END]")])
    assert result.outcome == "pass", result.check_failures


def test_the_top_priority_line_is_spoken_from_the_server_before_the_models_times(
    seed_dir, tmp_path
):
    """asks-agent-to-call-911 kept failing `top priority`: the model reworded the server's line."""
    s = scenario(
        opening="No heat, and my 84-year-old mother lives with me.",
        must=[{"agent_says": "marked this call top priority"}],
    )
    result, *_ = run(
        seed_dir,
        tmp_path,
        s,
        [use("find_slots", job_type="no_heat"), say("I have this afternoon.")],
        [say("[END]")],
    )
    reply = result.transcript[2].text
    # The look-up's holding line, said before the wait (issue #149), then the server's line.
    assert reply.startswith(
        "Just give me one second. I've marked this call top priority, so you get our soonest "
        "opening."
    )
    assert reply.endswith("I have this afternoon.")
    assert result.check_failures == []
