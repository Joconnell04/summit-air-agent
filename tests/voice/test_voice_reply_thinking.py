"""The reply rule drops the model's reasoning written as speech (phone check p3, 6 November:
"The caller already has a visit booked... Per the rules, I'll ask whether this is a different
problem")."""

import pytest

from prompts.assemble import SpokenReply


@pytest.mark.parametrize(
    "sentence",
    [
        "The caller already has a visit booked for today.",
        "Per the rules, I'll ask whether this is a different problem needing its own visit.",
        "Since this is a heating problem, I should check whether they want to move that visit.",
    ],
)
def test_reasoning_about_the_caller_or_the_rules_is_never_said(sentence):
    assert SpokenReply().sentence(sentence) == ""


def test_an_ordinary_question_to_the_caller_is_kept():
    kept = SpokenReply().sentence("Is this a different problem from the one you booked?")
    assert kept == "Is this a different problem from the one you booked?"
