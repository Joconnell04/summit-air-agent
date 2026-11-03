import pytest
from fakes import ScriptedModel, grade_reply, say

from evals.grader import grade
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
