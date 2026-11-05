"""The live-like audio checks (evals/audio_rig/live_checks.py), on small timelines and on Jackson's
two live calls, which they were written to catch."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from evals.audio_rig import live_checks as lc
from evals.audio_rig.timeline import AgentPart, AgentTurn, CallerLine, Timeline, Word
from evals.models import load_config

CFG = lc.cfg_of(load_config())
LIVE = Path(__file__).resolve().parents[2] / "evals" / "live_calls"
# Both live calls were on a Wednesday evening, New York time.
WEDNESDAY = datetime(2026, 11, 4, 22, 30)


def part(text, start, end=None, sounds=None, words=None, cut=False):
    end = start + 1.0 if end is None else end
    return AgentPart(text, start, end, sounds or [(start, end)], words or [], cut)


def call(*events, started=WEDNESDAY, hung_up_at=None):
    return Timeline(list(events), started=started, hung_up_at=hung_up_at)


def caller(text, start, end):
    return CallerLine(text, start, end)


def agent(after, *parts, lookup=False):
    return AgentTurn(after, list(parts), lookup)


GREETING = agent(0.0, part("Thanks for calling Summit Air. How can I help?", 0.5, 3.0))


# silence

def test_a_reply_within_four_seconds_passes_and_one_after_fails():
    fast = call(GREETING, caller("My heat is out.", 4, 5), agent(5, part("Sorry. Is anyone home?", 6)))
    slow = call(GREETING, caller("My heat is out.", 4, 5), agent(5, part("Sorry. Is anyone home?", 9.5)))
    assert lc.silence(fast, CFG) == []
    (line,) = lc.silence(slow, CFG)
    assert line.startswith("silence: 4.5 s") and '"My heat is out."' in line


def test_no_reply_at_all_is_a_silence():
    t = call(GREETING, caller("Hello?", 4, 5), agent(5))
    assert lc.silence(t, CFG) == ['silence: no reply at all after the caller said "Hello?"']


def test_a_quiet_stretch_in_a_lookup_fails_past_three_seconds():
    sounds = [(6.0, 7.0), (10.5, 12.0)]
    words = [Word("Let", 6.0, 6.2), Word("me", 6.2, 6.4), Word("check.", 6.4, 7.0)]
    t = call(GREETING, caller("Friday?", 4, 5),
             agent(5, part("Let me check. I have Friday.", 6.0, 12.0, sounds, words), lookup=True))
    (line,) = lc.silence(t, CFG)
    assert line == 'silence: 3.5 s with no sound in a lookup after "Let me check."'
    plain = call(GREETING, caller("Friday?", 4, 5),
                 agent(5, part("Let me check. I have Friday.", 6.0, 12.0, sounds, words)))
    assert lc.silence(plain, CFG) == [], "a plain turn gets the four-second limit"


def test_the_greeting_is_not_timed_as_a_reply():
    t = call(agent(0.0, part("Thanks for calling.", 9.0)))
    assert lc.silence(t, CFG) == []


# half replies

def test_a_line_with_no_question_then_silence_is_a_half_reply():
    t = call(GREETING, caller("Just didn't like it.", 4, 5), agent(5, part("Got it.", 6, 6.5)),
             caller("Is that all?", 25.5, 26.5))
    (line,) = lc.half_reply(t, CFG)
    assert line == ('half_reply: "Got it." asked nothing, then 19.0 s of silence before the '
                    "caller spoke")


def test_a_question_or_a_quick_follow_on_is_not_a_half_reply():
    asked = call(GREETING, caller("Friday.", 4, 5), agent(5, part("Got it. Morning or afternoon?", 6, 8)),
                 caller("Afternoon.", 15, 16))
    no_mark = call(GREETING, caller("Friday.", 4, 5), agent(5, part("Got it. Would afternoon work", 6, 8)),
                   caller("Yes.", 15, 16))
    quick = call(GREETING, caller("Friday.", 4, 5),
                 agent(5, part("Let me look.", 6, 7), part("I have Friday at noon. Okay?", 8, 10)),
                 caller("Sure.", 11, 12))
    assert lc.half_reply(asked, CFG) == lc.half_reply(no_mark, CFG) == lc.half_reply(quick, CFG) == []


def test_a_part_the_agent_continues_after_a_long_pause_is_a_half_reply():
    t = call(GREETING, caller("Is that all?", 4, 5),
             agent(5, part("I'll cancel it now.", 6, 7), part("What name is it under?", 11, 13)),
             caller("Sam.", 14, 15))
    (line,) = lc.half_reply(t, CFG)
    assert "then 4.0 s of silence before the agent went on" in line


def test_the_last_words_of_the_call_and_a_cut_line_are_not_half_replies():
    t = call(GREETING, caller("Bye.", 4, 5), agent(5, part("Thanks for calling.", 6)))
    cut = call(GREETING, caller("Friday.", 4, 5), agent(5, part("Someone will call you", 6, 7, cut=True)),
               caller("No, Friday.", 12, 13))
    assert lc.half_reply(t, CFG) == lc.half_reply(cut, CFG) == []


# repeats

def test_the_same_line_twice_in_one_turn_fails():
    line = "I'm not able to make that change from this number. Would that help?"
    t = call(GREETING, caller("Yes.", 4, 5), agent(5, part(line, 6), part(line, 9)))
    (out,) = lc.repeat(t, CFG)
    assert out.startswith('repeat: "I\'m not able to make that change') and "one turn" in out


def test_the_same_turn_again_fails_unless_the_caller_asked_to_hear_it():
    line = "The new address is 75 Hoyt Street, Brooklyn. Is that right?"
    again = call(GREETING, caller("Moved.", 4, 5), agent(5, part(line, 6)),
                 caller("Say that again?", 10, 11), agent(11, part(line, 12)))
    pushed = call(GREETING, caller("Moved.", 4, 5), agent(5, part(line, 6)),
                  caller("Okay, and?", 10, 11), agent(11, part(line, 12)))
    assert lc.repeat(again, CFG) == []
    (out,) = lc.repeat(pushed, CFG)
    assert "said again in the next turn" in out


def test_short_lines_and_different_lines_are_not_repeats():
    t = call(GREETING, caller("Yes.", 4, 5), agent(5, part("Okay. Okay.", 6)),
             caller("And?", 8, 9), agent(9, part("Okay. Okay.", 10)))
    assert lc.repeat(t, CFG) == []


# time to first sound

def test_first_sound_reports_median_and_slowest_against_the_targets():
    events = [GREETING]
    t = 3.0
    for delay in (0.8, 0.9, 1.0, 1.1, 3.0):
        events += [caller("Okay.", t + 1, t + 2), agent(t + 2, part("Right. And then?", t + 2 + delay))]
        t += 5
    (line,) = lc.first_sound(call(*events), CFG)
    assert line == "first_sound: plain p90 3000 ms over 2000 ms (5 turns)"


def test_lookups_are_held_to_their_own_target():
    t = call(GREETING, caller("Friday?", 4, 5), agent(5, part("I have Friday. Okay?", 7.4), lookup=True))
    assert lc.first_sound(t, CFG) == []
    assert lc.reply_delays(t) == {"plain": [], "lookup": [pytest.approx(2400)]}


# read-back speed

def spoken(text: str, start: float, per_word: float) -> list[Word]:
    return [Word(w, start + i * per_word, start + (i + 1) * per_word)
            for i, w in enumerate(text.split())]


def test_a_phone_number_read_too_fast_fails_and_a_slow_one_passes():
    text = "your number is three four seven five five five zero one two four, right?"
    fast = call(GREETING, caller("Yes.", 4, 5), agent(5, part(text, 6, 10, words=spoken(text, 6, 0.25))))
    slow = call(GREETING, caller("Yes.", 4, 5), agent(5, part(text, 6, 14, words=spoken(text, 6, 0.4))))
    (line,) = lc.readback_rate(fast, CFG)
    assert line.startswith('readback_rate: "three four seven') and "at 4.0 digits/s" in line
    assert lc.readback_rate(slow, CFG) == []


def test_digits_in_one_formatted_word_count_and_a_house_number_alone_does_not():
    zip_fast = [Word("Brooklyn,", 6.0, 6.5), Word("11201.", 6.5, 7.5)]
    house = [Word("75", 6.0, 6.2), Word("Hoyt", 6.2, 6.6)]
    t = call(GREETING, caller("Yes.", 4, 5), agent(5, part("Brooklyn, 11201.", 6, 7.5, words=zip_fast)))
    h = call(GREETING, caller("Yes.", 4, 5), agent(5, part("75 Hoyt", 6, 6.6, words=house)))
    assert lc.readback_rate(t, CFG) == ['readback_rate: "11201." at 5.0 digits/s, over 3.0']
    assert lc.readback_rate(h, CFG) == []


# hang-up

def test_a_hang_up_right_after_the_goodbye_fails():
    bye = agent(5, part("Thanks for calling Summit Air.", 6, 8))
    assert lc.hangup_gap(call(GREETING, caller("Bye.", 4, 5), bye, hung_up_at=8.4), CFG) == [
        'hangup_gap: the line dropped 0.4 s after "Thanks for calling Summit Air."']
    assert lc.hangup_gap(call(GREETING, caller("Bye.", 4, 5), bye, hung_up_at=9.5), CFG) == []
    assert lc.hangup_gap(call(GREETING, caller("Bye.", 4, 5), bye), CFG) == [], "caller hung up"


# a requested time said back

def asked_then(asked: str, reply: str) -> list[str]:
    return lc.time_as_asked(call(GREETING, caller(asked, 4, 5), agent(5, part(reply, 6))), CFG)


def test_two_pm_turned_into_a_window_fails():
    (line,) = asked_then("Friday at two PM.",
                         "Someone will call you tomorrow between 1 PM and 3 PM. Anything else?")
    assert line.startswith('time_as_asked: the caller asked "Friday at two PM."')
    assert asked_then("Tomorrow at two PM.",
                      "Someone will call tomorrow between 1 PM and 3 PM, not at 2 PM exactly. "
                      "Anything else?")


def test_the_time_said_back_on_the_day_asked_passes():
    assert asked_then("Friday at two PM.", "Okay, someone will call you Friday at 2 PM.") == []
    assert asked_then("Tomorrow at 2 PM.", "Got it, Thursday at 2:00 PM. Anything else?") == []


def test_the_right_time_on_the_wrong_day_fails():
    assert asked_then("Friday at two PM.", "Someone will call you tomorrow at 2 PM.")


def test_offering_another_time_as_a_question_passes():
    assert asked_then("Tomorrow at 2 PM.", "Tomorrow I have 12 to 4. Should I book that?") == []


def test_a_reply_with_no_time_waits_for_the_next_turn_that_names_one():
    t = call(GREETING, caller("Friday at 2 PM.", 4, 5), agent(5, part("Let me check.", 6)),
             caller("Okay.", 8, 9), agent(9, part("Done, Friday between 1 PM and 3 PM.", 10)))
    assert len(lc.time_as_asked(t, CFG)) == 1


def test_a_line_with_no_clock_time_is_not_checked():
    assert asked_then("Friday afternoon.", "I have Friday 12 to 4.") == []


# the two live calls

def live(n: int) -> list[str]:
    doc = json.loads((LIVE / f"call-{n}.json").read_text())
    return lc.run(lc.timeline_from_live_call(doc, WEDNESDAY), load_config())


def names(failures: list[str]) -> set[str]:
    return {f.split(":", 1)[0] for f in failures}


def test_live_call_1_fails_on_the_half_reply_the_reply_delay_and_the_callback_time():
    failures = live(1)
    assert {"half_reply", "first_sound", "time_as_asked"} <= names(failures)
    assert any('"Got it."' in f and f.startswith("half_reply") for f in failures)
    assert any('"Friday at two PM."' in f for f in failures)


def test_live_call_2_fails_on_the_repeat_the_half_reply_and_the_callback_time():
    failures = live(2)
    assert {"repeat", "half_reply", "time_as_asked"} <= names(failures)
    assert any("set up an AC tune-up" in f for f in failures if f.startswith("half_reply"))
    assert any('"Tomorrow at two PM."' in f for f in failures)


def test_every_check_has_a_threshold_in_config():
    cfg = load_config()["audio_rig"]["live_checks"]
    assert set(lc.CHECKS) == set(lc.NAMES)
    for key in ("silence_after_caller_s", "silence_in_lookup_s", "half_reply_silence_s",
                "repeat_similarity", "first_sound_ms", "readback_digits_per_s",
                "hangup_after_goodbye_s"):
        assert key in cfg


# the live-like suite's scenarios and the Jackson caller style

def test_every_live_scenario_loads_runs_the_audio_checks_and_uses_the_jackson_style():
    from evals.scenario import load_live

    scenarios = load_live()
    assert len(scenarios) == 12
    for s in scenarios:
        assert "live" in s.all_tags() and s.audio_checks and s.caller_style == "jackson", s.id
        assert not s.must_pass, f"{s.id}: a must-pass spec would run three times on the rig"
    assert {"live-replay-1-cancel-wrong-name", "live-replay-2-move-and-tuneup"} <= {
        s.id for s in scenarios}


def test_the_live_suite_selects_the_live_scenarios_and_the_text_suites_do_not():
    from evals import suites
    from evals.scenario import load_all, load_live

    chosen, _ = suites.select(load_all() + load_live(), "live")
    assert {s.id for s in chosen} == {s.id for s in load_live()}
    standard, _ = suites.select(load_all(), "standard")
    assert not any(s.id.startswith("live-") for s in standard)


def test_a_live_scenario_file_is_found_for_the_run_hash():
    from evals.scenario import LIVE_DIR, SCENARIOS_DIR, scenario_path

    assert scenario_path("live-call-06-no-zip-booking") == LIVE_DIR / "live-call-06-no-zip-booking.yaml"
    assert scenario_path("gas-smell-in-passing") == SCENARIOS_DIR / "gas-smell-in-passing.yaml"


def test_the_jackson_style_adds_his_manner_and_lines_to_the_caller_prompt():
    from evals.caller import system_prompt
    from evals.scenario import load_live

    replay = next(s for s in load_live() if s.id == "live-replay-1-cancel-wrong-name")
    prompt = system_prompt(replay)
    assert "How you talk: You talk in short, clipped lines" in prompt
    assert '"Why do you take so long to respond?"' in prompt and "never as a script" in prompt


def test_a_scenario_without_a_style_gets_the_caller_prompt_as_before():
    from evals.caller import system_prompt
    from evals.scenario import load_all

    plain = load_all()[0]
    assert plain.caller_style is None
    assert "How you talk" not in system_prompt(plain)


def test_an_unknown_caller_style_is_refused():
    from evals.scenario import Scenario

    data = {"id": "x", "flow": ["booking"], "difficulty": "easy", "journeys": ["J1"],
            "persona": "A caller.", "opening": "Hi.", "must": [{"booking": "none"}],
            "caller_style": "nobody"}
    with pytest.raises(ValueError, match="no caller style"):
        Scenario.model_validate(data)


def test_the_jackson_style_timing_is_his_measured_patience():
    from evals.caller import load_style

    timing = load_style("jackson")["timing"]
    assert timing["patience_s"] == 20 and timing["cut_in_after_s"] == 10


# the report

def test_the_report_counts_audio_failures_per_scenario_and_quotes_them(scenario_data):
    from evals.audio_rig.live_report import report
    from evals.results import ScenarioResult

    timing = {"kind": "plain", "reply_delay_ms": 1500.0, "model_first_token_ms": 0.0,
              "platform_stages": {"llm_requests": 2.0}}
    s = ScenarioResult.model_validate(scenario_data(
        scenario_id="live-replay-1-cancel-wrong-name", outcome="fail",
        check_failures=['half_reply: "Got it." asked nothing, then 19.0 s of silence',
                        "repeat: \"Same line.\" said twice in one turn",
                        "identity: expected mismatch"],
        turn_timings=[timing, {**timing, "reply_delay_ms": 900.0}]))
    lines = report([s], load_config())
    row = next(line for line in lines if line.startswith("| live-replay-1"))
    assert row.split(" | ")[1:4] == ["fail", "", "1"], row
    assert "talk_over" in lines[0] and "llm_requests" in lines[0]
    assert row.rstrip(" |").endswith("identity: expected mismatch")
    assert "| plain | 2 | 900 | 1200 | 1500 | 2000 |" in lines
    assert "| plain | 2 | 2 | 2 | 1 |" in lines
    assert '  - half_reply: "Got it." asked nothing, then 19.0 s of silence' in lines


# talking over the caller, and model requests

def test_sound_while_the_caller_speaks_or_a_reply_already_playing_is_talking_over():
    over = agent(5, part("Okay. And then?", 5.5))
    over.over_caller = 4.6
    instant = agent(5, part("Okay. And then?", 5.01))
    fine = agent(5, part("Okay. And then?", 5.6))
    assert lc.talk_over(call(GREETING, caller("My heat is out.", 4, 5), over), CFG) == [
        'talk_over: the agent made a sound 400 ms before the caller finished "My heat is out."']
    assert "already playing" in lc.talk_over(call(GREETING, caller("Hi.", 4, 5), instant), CFG)[0]
    assert lc.talk_over(call(GREETING, caller("Hi.", 4, 5), fine), CFG) == []


def test_after_the_caller_cut_in_the_overlap_is_not_the_agents():
    reply = agent(13, part("Sorry. Friday then?", 13.5))
    reply.over_caller = 12.2
    t = call(GREETING, caller("Friday.", 4, 5), agent(5, part("Someone will call", 6, 12, cut=True)),
             caller("No, Friday.", 12, 13), reply)
    assert lc.talk_over(t, CFG) == []


def test_more_model_requests_than_the_turn_needs_fail():
    plain = agent(5, part("Okay. And then?", 6))
    plain.llm_requests = 2
    lookup = agent(5, part("I have Friday. Okay?", 7), lookup=True)
    lookup.llm_requests = 2
    (line,) = lc.llm_requests(call(GREETING, caller("Hi.", 4, 5), plain), CFG)
    assert line == 'llm_requests: 2 model requests on a plain turn (limit 1) after the caller said "Hi."'
    assert lc.llm_requests(call(GREETING, caller("Friday?", 4, 5), lookup), CFG) == []
    assert lc.llm_requests(call(GREETING, caller("Hi.", 4, 5), agent(5, part("Ok?", 6))), CFG) == []


def test_live_call_2_spent_three_model_requests_on_plain_turns():
    assert any(f.startswith("llm_requests: 3 model requests on a plain turn") for f in live(2))


def test_the_rig_sums_model_requests_over_a_turns_lines():
    from evals.audio_rig.rig import turn_agent_timings

    trace = {"events": [
        {"type": "turn", "speaker": "caller", "timings": {}},
        {"type": "turn", "speaker": "agent", "timings": {"llm_requests": 2.0}},
        {"type": "turn", "speaker": "agent", "timings": {"llm_requests": 1.0}},
    ]}
    stages, _ = turn_agent_timings(trace, 0)
    assert stages["llm_requests"] == 3.0
