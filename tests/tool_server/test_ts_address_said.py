"""Addresses as people say them (Jackson): with the voice's own number reading off, "199" would be
"one hundred ninety-nine" and "77-10" a subtraction. Speech only; stored addresses keep numerals."""

import pytest
from toolkit import UNKNOWN, in_area_address

from app.text import address_said, house_said, ordinal_said, street_said, unit_clean, unit_said


@pytest.mark.parametrize("number,said", [
    ("199", "one ninety-nine"),
    ("305", "three oh five"),
    ("12", "twelve"),
    ("100", "one hundred"),
    ("1500", "fifteen hundred"),
    ("1010", "ten ten"),
    ("2050", "twenty fifty"),
    ("7", "seven"),
    ("77-10", "seventy-seven ten"),
    ("34-05", "thirty-four oh five"),
])
def test_house_numbers(number, said):
    assert house_said(number) == said


@pytest.mark.parametrize("n,said", [(4, "Fourth"), (34, "Thirty-Fourth"), (101, "One Hundred First")])
def test_numbered_streets(n, said):
    assert ordinal_said(n) == said


@pytest.mark.parametrize("unit,said", [("2", "two"), ("4B", "four B"), ("12C", "one two C")])
def test_units(unit, said):
    assert unit_said(unit) == said


def test_a_whole_address():
    assert address_said("199 East 4th Street", "2") == "one ninety-nine East Fourth Street, unit two"
    assert street_said("77-10 34th Avenue") == "seventy-seven ten Thirty-Fourth Avenue"
    assert street_said("Avenue N") == "Avenue N"


def test_the_read_back_says_the_address_in_words_and_confirms_on_the_stored_one(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    address = {**in_area_address(), "unit": "4B"}
    agent.turn(call, f"It's {address['street']}, unit 4B, {address['zip']}.")
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert asked["say"].startswith("So that's one oh one Union St, unit four B, and ")
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    confirmed = agent.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    assert confirmed["ok"], confirmed
    assert confirmed["data"]["address"]["street"] == "101 Union St"
    assert confirmed["data"]["address"]["unit"] == "4B"


@pytest.mark.parametrize("unit", ["Unit 2", "unit 2", "Apt. 2", "#2", "Apartment 2", "2"])
def test_a_unit_word_is_dropped_from_the_unit(unit):
    """The model passed "Unit 2", read back as "unit U N I T two" (Retell call
    call_926427e1d1f2a80cb78dc6a4e04)."""
    assert unit_clean(unit) == "2"
    assert address_said("199 East 4th Street", unit) == "one ninety-nine East Fourth Street, unit two"


def test_unit_2_and_2_read_back_and_confirm_as_one_address(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, unit 2, {address['zip']}.")
    asked = agent.tool("read_back", call, address={**address, "unit": "Unit 2"}, phone="caller_id")
    assert "unit two, and" in asked["say"] and "U N I T" not in asked["say"]
    agent.say(call, asked["say"])
    agent.turn(call, "Yes.")
    confirmed = agent.tool(
        "read_back", call, address={**address, "unit": "2"}, phone="caller_id", confirmed=True
    )
    assert confirmed["ok"], confirmed
    assert confirmed["data"]["address"]["unit"] == "2"
