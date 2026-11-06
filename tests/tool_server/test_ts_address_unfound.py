"""An address the lookup can't find, or a lookup that's down, never stalls the call: a ZIP the
caller said is taken and the read-back confirms it; with none said, the agent asks once."""

import pytest
from test_ts_address_lookup import Book, down, lookup
from toolkit import UNKNOWN, Agent, make_server

from app.geocode import Place

ST_ALBANS = "one one eight 24 one nine seven th street st albans one one four one two"


@pytest.fixture(params=["none", "down"])
def unfound(request, tmp_path, clock, mock):
    backend = ("book", Book({})) if request.param == "none" else ("down", down)
    return Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([backend])), clock)


def test_the_callers_zip_is_taken_when_the_lookup_finds_nothing(unfound):
    """Eval runs 20261104-201607 and later, caller-declines-safety-advice: the agent looped."""
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, ST_ALBANS)
    found = unfound.tool("find_address", call, street="118-24 197th Street", borough="St. Albans")
    assert found["ok"], found
    assert found["data"]["zip"] == "11412"


def test_with_no_zip_said_the_agent_asks_once_then_goes_on(unfound):
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, "It's 118-24 197th Street in St. Albans.")
    first = unfound.tool("find_address", call, street="118-24 197th Street", borough="St. Albans")
    # A numbered street is never spelled. St. Albans names no borough, so the borough is asked;
    # this caller gives the ZIP instead.
    assert not first["ok"] and first["say"] == "And which borough is that in?"
    unfound.turn(call, "It's 11412.")
    again = unfound.tool("find_address", call, street="118-24 197th Street")
    assert again["ok"] and again["data"]["zip"] == "11412", again


def test_a_street_the_caller_never_said_is_refused_without_a_zip(unfound):
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, "My heat is out.")
    refused = unfound.tool("find_address", call, street="118-24 197th Street")
    assert refused["refused"] == "address_not_heard"


def test_the_callers_zip_then_reads_back_and_confirms(unfound):
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, ST_ALBANS)
    assert unfound.tool("find_address", call, street="118-24 197th Street")["ok"]
    assert unfound.tool("check_service_area", call, zip="11412")["ok"]
    address = {"street": "118-24 197th Street", "unit": "", "city": "St. Albans", "zip": "11412"}
    asked = unfound.tool("read_back", call, address=address, phone="caller_id")
    assert asked["ok"] and asked["data"]["sentence"].startswith("So that's one eighteen twenty-four One Hundred Ninety-Seventh Street, and ")
    unfound.say(call, asked["say"])
    unfound.turn(call, "Yes.")
    confirmed = unfound.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    assert confirmed["ok"], confirmed


def test_a_house_and_numbered_street_said_as_one_run_is_split():
    from app.tools import _split_run_on

    said = "one one eight 24 one nine seven th street"
    assert _split_run_on(["11824197", "th", "st"], "11824", ["197"], said) == [
        "11824", "197", "th", "st"
    ]
    assert _split_run_on(["248", "duffield"], "248", ["duffield"], "248 Duffield") == [
        "248", "duffield"
    ]


def test_a_run_written_as_digits_is_not_split():
    """PR #139 review: "112 2nd Avenue" is house 112, never house 11 on 2nd Avenue."""
    from app.tools import _split_run_on

    assert _split_run_on(["112", "ave"], "11", ["2"], "112 2nd Avenue") == ["112", "ave"]


def test_the_zip_is_never_asked_and_the_borough_only_once(unfound):
    """PR #139 review: with no ZIP said, find_address asked for it up to four times. Jackson: never
    ask for the ZIP; the visit books unverified and the office confirms the address."""
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, "It's 118-24 197th Street in St. Albans.")
    says = [unfound.tool("find_address", call, street="118-24 197th Street")["say"]
            for _ in range(4)]
    assert "What's the ZIP code there?" not in says
    assert says.count("And which borough is that in?") <= 1


def test_a_made_up_street_is_refused_even_with_a_zip_said(unfound):
    """PR #139 review: "99 Fake Street" was accepted once any ZIP was said."""
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, "I'm at 118-24 197th Street, 11412.")
    refused = unfound.tool("find_address", call, street="99 Fake Street")
    assert refused["refused"] == "address_not_heard"


def test_a_corrected_address_gets_its_own_lookup_after_the_zip_was_asked(tmp_path, clock, mock):
    """PR #139 re-check: after the ZIP was asked for one street, a corrected street was stuck on
    zip_asked for the rest of the call, with the lookup up or down."""
    from test_ts_address_lookup import DUFFIELD

    book = Book({("248", "duffield st"): [DUFFIELD]})
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("book", book)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 118-24 197th Street in St. Albans.")
    first = agent.tool("find_address", call, street="118-24 197th Street")
    # A numbered street goes straight to the borough, never to spelling.
    assert first["say"] == "And which borough is that in?"
    agent.turn(call, "Sorry, I'm actually at 248 Duffield Street, Brooklyn.")
    corrected = agent.tool("find_address", call, street="248 Duffield Street")
    assert corrected["ok"] and corrected["data"]["zip"] == "11201", corrected


def test_a_corrected_address_with_the_lookup_down_books_unverified_in_its_borough(
    tmp_path, clock, mock
):
    agent = Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("down", down)])), clock)
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 118-24 197th Street in St. Albans.")
    assert agent.tool("find_address", call, street="118-24 197th Street")["refused"] == "address_lookup_down"
    agent.turn(call, "Sorry, I'm actually at 248 Duffield Street, Brooklyn.")
    corrected = agent.tool("find_address", call, street="248 Duffield Street")
    assert corrected["ok"] and corrected["data"]["verified"] is False
    assert corrected["data"]["zip"] == "11201", corrected


def test_a_run_said_as_number_words_is_not_split():
    """PR #139 re-check: "one twelve 2nd avenue" is house 112, not house 11 on 2nd Avenue."""
    from app.tools import _split_run_on

    assert _split_run_on(["112", "ave"], "11", ["2"], "one twelve 2nd avenue") == ["112", "ave"]


# ---- a numbered street with no East or West (Jackson's call call_046f37112cac691a50019893717) ----

EAST_4TH = Place("199", "EAST 4 STREET", "10009", "Manhattan", "East Village")
WEST_4TH = Place("199", "WEST 4 STREET", "10014", "Manhattan", "West Village")


def numbered(tmp_path, clock, mock, book):
    return Agent(make_server(tmp_path, clock, mock[0], geocoder=lookup([("book", Book(book))])),
                 clock)


def test_east_or_west_is_asked_never_spelled_never_the_zip(tmp_path, clock, mock):
    agent = numbered(tmp_path, clock, mock, {("199", "e 4 st"): [EAST_4TH],
                                            ("199", "w 4 st"): [WEST_4TH]})
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "$199.")
    agent.turn(call, "Um, 4th Street.")
    asked = agent.tool("find_address", call, street="199 4th Street")
    assert asked["refused"] == "address_direction"
    assert asked["say"] == "Is that East 4th Street or West 4th Street?"
    agent.say(call, asked["say"])
    agent.turn(call, "East.")
    found = agent.tool("find_address", call, street="199 East 4th Street")
    assert found["ok"] and found["data"]["zip"] == "10009"
    # The read-back reads the direction back.
    assert found["data"]["street"] == "199 East 4th Street"


def test_one_direction_on_the_books_is_taken_without_asking(tmp_path, clock, mock):
    agent = numbered(tmp_path, clock, mock, {("199", "e 4 st"): [EAST_4TH]})
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 199 4th Street.")
    found = agent.tool("find_address", call, street="199 4th Street")
    assert found["ok"] and found["data"]["street"] == "199 East 4th Street"


def test_no_borough_even_after_asking_books_unverified_never_asks_the_zip(tmp_path, clock, mock):
    agent = numbered(tmp_path, clock, mock, {})
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "It's 199 4th Street.")
    asked = agent.tool("find_address", call, street="199 4th Street")
    assert asked["say"] == "And which borough is that in?"
    agent.say(call, asked["say"])
    agent.turn(call, "E-A-S. See you. And number 4.")
    booked = agent.tool("find_address", call, street="199 4th Street")
    assert booked["ok"] and booked["data"]["verified"] is False
    assert "ZIP" not in booked["say"]
