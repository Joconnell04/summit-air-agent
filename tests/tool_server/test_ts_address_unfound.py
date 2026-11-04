"""An address the lookup can't find, or a lookup that's down, never stalls the call: a ZIP the
caller said is taken and the read-back confirms it; with none said, the agent asks once."""

import pytest
from test_ts_address_lookup import Book, down, lookup
from toolkit import UNKNOWN, Agent, make_server

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
    assert not first["ok"]
    asks = first["say"]
    if first["refused"] == "address_not_found":  # a miss asks to spell the street first
        unfound.turn(call, "One nine seven, 197th Street.")
        asks = unfound.tool("find_address", call, street="118-24 197th Street")["say"]
    assert asks == "What's the ZIP code there?"
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
    assert asked["ok"] and "one one four one two" in asked["say"]
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


def test_the_zip_is_asked_once_and_the_lookup_isnt_tried_again(unfound):
    """PR #139 review: with no ZIP said, find_address asked for it up to four times."""
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, "It's 118-24 197th Street in St. Albans.")
    asked = []
    for _ in range(4):
        result = unfound.tool("find_address", call, street="118-24 197th Street")
        asked.append(result["say"] == "What's the ZIP code there?")
    assert asked.count(True) == 1
    assert result["refused"] == "zip_asked" and result["say"] == ""


def test_a_made_up_street_is_refused_even_with_a_zip_said(unfound):
    """PR #139 review: "99 Fake Street" was accepted once any ZIP was said."""
    call = unfound.start(UNKNOWN)["data"]["call_id"]
    unfound.turn(call, "I'm at 118-24 197th Street, 11412.")
    refused = unfound.tool("find_address", call, street="99 Fake Street")
    assert refused["refused"] == "address_not_heard"
