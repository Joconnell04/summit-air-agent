"""The bake-off table and the decision 026 verdict, from synthetic audio-rig runs."""
import pytest

from evals.audio_rig.report import (
    Stack,
    contains,
    gates_held,
    heard_share,
    lines_by_kind,
    table,
    verdict,
)
from evals.results import Run

GAS = lines_by_kind()["gas"]


def call(scenario_id, delays, outcome="pass", failures=(), heard=None, sample=1, flow=("booking",)):
    agent = heard if heard is not None else "Sure, I can help."
    return {
        "scenario_id": scenario_id, "sample": sample, "persona": "A caller.", "flow": list(flow),
        "difficulty": "easy", "outcome": outcome, "check_failures": list(failures),
        "grader_score": 4 if outcome == "pass" else None, "reply_delay_p50_ms": 0,
        "reply_delay_p90_ms": 0, "cost_usd": 0.1, "call_id": f"c-{scenario_id}-{sample}",
        "cost_parts": {"platform": 0.05},
        "transcript": [{"role": "caller", "text": "hi"}, {"role": "agent", "text": agent,
                                                          "said": agent}],
        "turn_timings": [{"kind": "plain", "reply_delay_ms": d, "model_first_token_ms": 0}
                         for d in delays],
    }


def stack(name, delays, gas_heard=GAS, gas_failures=()):
    gas = [call("gas-smell-in-passing", delays, heard=gas_heard, sample=n, flow=("safety",),
                outcome="fail" if gas_failures else "pass", failures=gas_failures)
           for n in (1, 2, 3)]
    scenarios = [call("tune-up-known-caller", delays), *gas]
    run = Run.model_validate({"run_id": f"r-{name}", "created": "2026-11-03T20:00:00Z",
                              "suite": "smoke", "stack": name, "profile": "fast", "hash": "h",
                              "transport": "audio", "scenarios": scenarios})
    return Stack(name, [run])


def test_heard_share_allows_for_a_misheard_word_but_not_a_paraphrase():
    assert heard_share(GAS, GAS) == 1.0
    misheard = GAS.replace("switches", "witches")
    assert heard_share(GAS, misheard) > 0.9
    assert heard_share(GAS, "Please get outside and call 911 from there.") < 0.5
    assert heard_share(GAS, GAS.replace("911", "nine one one")) == 1.0


def test_exact_text_must_contain_the_lines_in_full():
    assert contains(GAS, "Okay. " + GAS)
    assert not contains(GAS, GAS[:-20])


def test_grader_notes_do_not_count_against_the_gates(scenario_data):
    from evals.results import ScenarioResult

    graded = ScenarioResult.model_validate(scenario_data(outcome="fail", grader_score=2,
                                                         check_failures=["grader: tone"]))
    gated = ScenarioResult.model_validate(scenario_data(outcome="fail",
                                                        check_failures=["booking: none made"]))
    assert gates_held(graded) and not gates_held(gated)


def test_a_newcomer_is_chosen_only_when_faster_by_the_margin_and_clean():
    ours = stack("livekit-rig", [1500, 1500, 1500])
    faster = stack("fast", [1100, 1100, 1100])
    close = stack("close", [1300, 1300, 1300])
    paraphrase = stack("para", [1000, 1000, 1000], gas_heard="Please get out and call 911.",
                       gas_failures=["safety_lines: the first reply did not open with the lines"])
    lines = verdict([ours, faster, close, paraphrase], "livekit-rig")
    assert lines[0].startswith("- fast: chosen")
    assert lines[1].startswith("- close: tie, keep ours")
    assert lines[2].startswith("- para: tie, keep ours") and "not clean" in lines[2]


def test_the_table_has_a_row_per_stack_with_its_medians():
    rows = table([stack("livekit-rig", [1000, 2000, 3000])])
    assert len(rows) == 3
    assert "| livekit-rig | 4 | 2.00 s (12) | 3.00 s |" in rows[2]
    assert "| 0 / 0 |" in rows[2]
    assert "3 of 3" in rows[2]  # safety lines word for word


def test_a_reply_over_the_caller_is_a_talk_over_not_a_fast_reply():
    st = stack("x", [0, 1200, 1400])
    assert st.delays("plain") == [1200, 1400] * 4
    assert st.talk_overs() == 4


@pytest.mark.parametrize("missing", ["nobody"])
def test_no_verdict_without_our_run(missing):
    assert verdict([stack("x", [1000])], missing) == [f"no run for {missing}; no verdict"]
