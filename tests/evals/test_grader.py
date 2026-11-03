import pytest
from fakes import ScriptedModel, grade_reply, say

from evals.grader import grade, set_up
from evals.models import ModelError
from evals.results import Turn

TURNS = [Turn(role="agent", text="Hi."), Turn(role="caller", text="Hello.")]


def test_a_pass_with_a_score_above_one_does_not_fail():
    g = grade(ScriptedModel([grade_reply(4)]), TURNS, ["One question at a time."])
    assert (g.score, g.failed, g.notes) == (4, False, "Fine.")


def test_a_fail_verdict_or_a_score_of_one_fails():
    assert grade(ScriptedModel([grade_reply(3, "fail")]), TURNS, []).failed
    assert grade(ScriptedModel([grade_reply(1, "pass")]), TURNS, []).failed


def test_an_out_of_range_score_is_an_error():
    with pytest.raises(ModelError):
        grade(ScriptedModel([grade_reply(7)]), TURNS, [])
    with pytest.raises(ModelError):
        grade(ScriptedModel([say("not json")]), TURNS, [])


def test_the_grader_sees_the_transcript_and_what_to_look_for():
    model = ScriptedModel([grade_reply()])
    grade(model, TURNS, ["Offers the shape of availability first."])
    call = model.calls[0]
    assert "Offers the shape of availability first." in call["system"]
    assert call["messages"][0]["content"] == "AGENT: Hi.\nCALLER: Hello."


SAFETY = {"kinds": {"gas": {"lines": "Please get everyone out."}}, "sign_off": "We'll follow up once\n you're safe."}


def trace(flags=(), events=(), signal=None, state="none"):
    call = {"safety_signal_at": signal, "safety_state": state, "safety_kind": "gas" if signal else None}
    return {"call": call, "flags": list(flags), "events": list(events)}


def tool(name, say, ok=True):
    return {"type": "tool", "tool": name, "ok": ok, "say": say}


def test_the_grader_is_told_what_the_server_set_up():
    found = set_up(trace(
        flags=[{"kind": "person"}],
        events=[tool("flag_person", "Someone will call you by 9:15 AM."),
                tool("find_slots", "I've got openings today."),
                tool("book_appointment", "Before I book it, let me read back.", ok=False)],
    ), SAFETY)
    assert found == ["A person was flagged (person).",
                     "flag_person returned: Someone will call you by 9:15 AM."]
    model = ScriptedModel([grade_reply()])
    grade(model, TURNS, [], found)
    assert "flag_person returned: Someone will call you by 9:15 AM." in model.calls[0]["system"]


def test_a_safety_call_shows_the_grader_the_signed_off_wording():
    found = set_up(trace(signal="2026-11-02T14:00:00Z", state="lines_said"), SAFETY)
    assert "Safety sign-off (fixed wording): We'll follow up once you're safe." in found
    assert set_up(trace(signal="2026-11-02T14:00:00Z", state="cleared"), SAFETY) == []


def test_with_nothing_set_up_the_grader_is_told_so():
    model = ScriptedModel([grade_reply()])
    grade(model, TURNS, [])
    assert "- Nothing." in model.calls[0]["system"]
