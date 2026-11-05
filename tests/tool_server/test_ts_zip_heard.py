"""A ZIP the server acts on came from the caller or their record (tools._zip_known): the agent
can't turn a caller away as out of area, or book, on a ZIP it worked out itself (eval run
20261104-041212, where "1520 Flatbush Ave" became 11226 and the call ended out of area)."""

import pytest
from toolkit import UNKNOWN, book, count, first_open, in_area_address

from app.text import digit_runs


@pytest.mark.parametrize(
    "line, runs",
    [
        ("It's 11226.", ["11226"]),
        ("one one two two six", ["11226"]),
        ("one one double two six", ["11226"]),
        ("1 1 2 2 6", ["11226"]),
        ("it's eleven two two six", ["226"]),
        ("1520 Flatbush Ave, apartment 4C", ["1520"]),
        ("zip is one oh three oh one, phone 929 555 0112", ["10301", "9295550112"]),
    ],
)
def test_a_line_is_read_for_the_digits_it_says(line, runs):
    assert digit_runs(line) == runs


def test_a_zip_the_caller_never_said_is_refused_and_nothing_is_recorded(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's for my apartment, 1520 Flatbush Avenue, apartment 4C in Brooklyn.")
    checked = agent.tool("check_service_area", call, zip="11226")
    assert checked["refused"] == "zip_not_heard"
    assert checked["say"] == "What's the ZIP code there?"
    assert count(agent, "SELECT COUNT(*) FROM out_of_area WHERE call_id = ?", call) == 0
    agent.end(call, "agent_end")
    conn = agent.db()
    outcome = conn.execute("SELECT outcome FROM calls WHERE id = ?", (call,)).fetchone()[0]
    conn.close()
    assert outcome != "declined_out_of_area"


@pytest.mark.parametrize(
    "line", ["It's 11550.", "Zip's one one five five zero.", "one one double five zero"]
)
def test_a_zip_the_caller_said_in_digits_or_words_is_checked(agent, line):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, line)
    # 11550 (Hempstead, Nassau): every ZIP in the five boroughs is in the area since DECISIONS.md 105.
    assert agent.tool("check_service_area", call, zip="11550")["refused"] == "out_of_area"


def test_a_zip_on_the_callers_record_needs_no_saying(agent, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    call = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(call, "Hi, it's Maria again.")
    assert agent.tool("check_service_area", call, zip="11235")["ok"]


def test_a_zip_said_on_a_linked_call_counts(agent, clock):
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "The heat's out at 101 Union St, 11231.")
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(second, "Sorry, we got cut off.")
    assert agent.tool("check_service_area", second, zip="11231")["ok"]


def test_the_read_back_and_so_the_booking_need_a_zip_the_caller_said(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "The heat's out at 101 Union St in Brooklyn.")
    asked = agent.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert asked["refused"] == "zip_not_heard"
    assert book(agent, call, first_open(agent, call))["refused"] == "gate_read_back"
    agent.turn(call, "The ZIP is 11231.")
    assert agent.tool("read_back", call, address=in_area_address(), phone="caller_id")["ok"]


def test_the_agents_own_line_with_a_zip_does_not_count(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "1520 Flatbush Avenue.")
    agent.say(call, "That's 11226, right?")
    assert agent.tool("check_service_area", call, zip="11226")["refused"] == "zip_not_heard"


def test_punctuation_ends_a_run_of_digits():
    assert digit_runs("11226, and my cell is 718 555 0142.") == ["11226", "7185550142"]


@pytest.mark.parametrize(
    "line", ["My number is 718 112 2600.", "Call me at 9 1 1 2 2 6 4 4 0 1.", "Unit 112260."]
)
def test_a_zip_inside_a_longer_number_does_not_count(agent, line):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, line)
    assert agent.tool("check_service_area", call, zip="11226")["refused"] == "zip_not_heard"


def test_zip_plus_four_counts(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 11231-4402.")
    assert agent.tool("check_service_area", call, zip="11231")["ok"]


@pytest.mark.parametrize(
    "line, tokens",
    [
        ("two forty eight duffield", ["248", "duffield"]),
        ("two hundred forty eight", ["248"]),
        ("two hundred and five main", ["205", "main"]),
        ("forty two forty one bell", ["4241", "bell"]),
        ("ten sixty two park place", ["1062", "park", "place"]),
        ("one oh six two", ["1062"]),
        ("77-10 34th Ave", ["7710", "34", "ave"]),
        ("out for 12 days", ["out", "for", "12", "days"]),
    ],
)
def test_spoken_numbers_are_read_as_new_yorkers_say_addresses(line, tokens):
    from app.text import number_tokens

    assert number_tokens(line) == tokens
