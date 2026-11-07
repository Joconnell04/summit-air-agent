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
        "I'll answer it directly.",  # phone check t3 leak replay, 6 November
    ],
)
def test_reasoning_about_the_caller_or_the_rules_is_never_said(sentence):
    assert SpokenReply().sentence(sentence) == ""


def test_an_ordinary_question_to_the_caller_is_kept():
    kept = SpokenReply().sentence("Is this a different problem from the one you booked?")
    assert kept == "Is this a different problem from the one you booked?"


def test_saying_the_address_back_is_kept_when_the_caller_asked_to_hear_it_again():
    """Phone check t1 last-one: "Wait, what? Say it again?" got only "Is that all correct?"."""
    reply = SpokenReply()
    reply.heard(None, "It's 210 Clinton Street, Brooklyn.")
    assert reply.sentence("That's 210 Clinton Street.") == ""
    reply.heard(None, "Wait, what? Say it again?")
    assert reply.sentence("That's 210 Clinton Street.") == "That's 210 Clinton Street."


def test_a_phone_ending_is_said_digit_by_digit():
    """Leak replay t5, 6 November: "ending in 6730" was heard as "six thousand seven hundred and
    thirty"."""
    said = SpokenReply().sentence("We'll call the number ending in 6730 if anything changes.")
    assert said == "We'll call the number ending in 6 7 3 0 if anything changes."
