"""The lookup a caller line asks for, run by the server before the model answers (app/prefetch.py,
issue #142). Off by default. On, a ZIP or a street in a caller line is looked up with the same tool
the model would call, logged as that tool, and handed back with the turn; every gate still refuses
as before."""

import json

import pytest
from test_ts_address_lookup import DUFFIELD, Book, lookup
from toolkit import UNKNOWN, Agent, book, count, first_open, in_area_address, make_server

from app import settings as app_settings
from prompts.assemble import server_notes

IN_AREA = "11231"
OUT_OF_AREA = "11226"
GAS = "I smell gas in the kitchen."


@pytest.fixture
def on(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0], prefetch_lookups=True), clock)


@pytest.fixture
def off(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0], prefetch_lookups=False), clock)


@pytest.fixture
def located_on(tmp_path, clock, mock):
    geocoder = lookup([("book", Book({("248", "duffield st"): [DUFFIELD]}))])
    server = make_server(tmp_path, clock, mock[0], geocoder=geocoder, prefetch_lookups=True)
    return Agent(server, clock)


def opened(agent: Agent) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, the heat's out in my house.")
    agent.say(call, "Sorry to hear that. What's the address there?")
    return call


def prefetched(agent: Agent, call: str) -> list[dict]:
    conn = agent.db()
    try:
        rows = conn.execute(
            "SELECT tool, args_json, ok, refused FROM tool_calls WHERE call_id = ? "
            "AND request_id LIKE 'prefetch-%' ORDER BY id",
            (call,),
        ).fetchall()
    finally:
        conn.close()
    return [{"tool": r["tool"], "args": json.loads(r["args_json"]), "ok": r["ok"],
             "refused": r["refused"]} for r in rows]


# ---- the trigger ------------------------------------------------------------------------------


def test_a_zip_said_is_checked_and_handed_back_with_the_turn(on):
    call = opened(on)
    data = on.turn(call, f"It's 101 Union Street, {IN_AREA}.")["data"]
    assert data["lookup"]["tool"] == "check_service_area"
    assert data["lookup"]["args"] == {"zip": IN_AREA}
    assert data["lookup"]["result"]["ok"] is True
    assert data["lookup"]["result"]["data"]["in_area"] is True
    rows = prefetched(on, call)
    assert [(r["tool"], r["args"]["zip"], r["args"]["by"]) for r in rows] == [
        ("check_service_area", IN_AREA, "server")
    ]
    # The model reads it as the tool's result, with the call it stands for.
    note = server_notes(data["hints"], data["known"], data["lookup"])[-1]
    assert note.startswith(f'[server] Already called for this line: check_service_area(zip="{IN_AREA}")')
    assert "don't call it again" in note


def test_a_zip_said_as_words_is_checked(on):
    call = opened(on)
    data = on.turn(call, "zip is one one two three one")["data"]
    assert data["lookup"]["args"] == {"zip": IN_AREA}


def test_a_street_said_without_a_zip_is_looked_up(located_on):
    call = opened(located_on)
    data = located_on.turn(call, "It's 248 Duffield Street.")["data"]
    assert data["lookup"]["tool"] == "find_address"
    assert data["lookup"]["args"]["street"] == "248 Duffield Street"
    assert data["lookup"]["result"]["data"]["zip"] == "11201"
    # The ZIP it found passes the ZIP gate, as when the model calls find_address itself.
    assert located_on.tool("check_service_area", call, zip="11201")["ok"]


def test_a_street_with_no_geocoder_hands_back_the_ask_for_the_zip(on):
    call = opened(on)
    data = on.turn(call, "It's 248 Duffield Street.")["data"]
    assert data["lookup"]["tool"] == "find_address"
    assert data["lookup"]["result"]["refused"] == "address_lookup_down"


def test_an_out_of_area_zip_hands_back_the_decline_and_records_it(on):
    call = opened(on)
    data = on.turn(call, f"The zip is {OUT_OF_AREA}.")["data"]
    result = data["lookup"]["result"]
    assert result["ok"] is False and result["refused"] == "out_of_area"
    assert result["say"]
    assert count(on, "SELECT COUNT(*) FROM out_of_area WHERE call_id = ?", call) == 1


def test_a_house_number_is_never_taken_for_a_zip(on):
    call = opened(on)
    data = on.turn(call, "It's 11231 Flatlands Avenue.")["data"]
    assert data["lookup"]["tool"] == "find_address"


def test_two_zips_in_one_line_are_not_looked_up(on):
    call = opened(on)
    data = on.turn(call, "It's 11231, no sorry, 11215.")["data"]
    assert "lookup" not in data
    assert prefetched(on, call) == []


def test_a_correction_with_the_new_zip_replaces_the_lookup(on):
    call = opened(on)
    on.turn(call, f"It's {IN_AREA}.")
    on.say(call, "Thanks. How cold is it inside?")
    data = on.turn(call, "Sorry, I mean 11215.")["data"]
    assert data["lookup"]["args"] == {"zip": "11215"}
    assert [r["args"]["zip"] for r in prefetched(on, call)] == [IN_AREA, "11215"]


def test_a_correction_with_no_new_address_says_the_lookup_no_longer_holds(on):
    call = opened(on)
    on.turn(call, f"It's {IN_AREA}.")
    on.say(call, "Thanks. How cold is it inside?")
    data = on.turn(call, "Wait, sorry, that's the wrong zip.")["data"]
    assert "lookup" not in data
    assert "lookup_taken_back" in data["hints"]
    assert any("Don't rely on that lookup" in n for n in server_notes(data["hints"]))
    # Only on the line right after the lookup; later talk about the address carries no note.
    on.say(call, "No problem. What's the right one?")
    later = on.turn(call, "Actually the street is fine, sorry.")["data"]
    assert "lookup_taken_back" not in later["hints"]


def test_no_job_type_yet_runs_no_slot_lookup(on):
    """find_slots is never run by the server: the read-back comes first, and the wait it records
    belongs to the moment the caller hears times."""
    call = opened(on)
    on.turn(call, f"It's 101 Union Street, {IN_AREA}.")
    assert [r["tool"] for r in prefetched(on, call)] == ["check_service_area"]
    assert count(on, "SELECT COUNT(*) FROM waits WHERE call_id = ?", call) == 0


def test_a_zip_already_checked_is_not_checked_again(on):
    call = opened(on)
    on.turn(call, f"It's {IN_AREA}.")
    on.say(call, "Thanks. How cold is it inside?")
    data = on.turn(call, f"About 55. And it's {IN_AREA}, like I said.")["data"]
    assert "lookup" not in data
    assert len(prefetched(on, call)) == 1


def test_a_zip_the_model_already_checked_is_not_checked_again(on):
    call = opened(on)
    on.turn(call, f"Union Street, {IN_AREA}.")
    on.tool("check_service_area", call, zip=IN_AREA)
    on.say(call, "Thanks. What's the house number?")
    assert "lookup" not in on.turn(call, f"101, in {IN_AREA}.")["data"]


def test_no_street_lookup_once_a_zip_was_checked(located_on):
    call = opened(located_on)
    located_on.turn(call, "It's in 11201.")
    located_on.say(call, "And the street address?")
    assert "lookup" not in located_on.turn(call, "248 Duffield Street.")["data"]


# ---- the switch off -----------------------------------------------------------------------------


def test_the_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PREFETCH_LOOKUPS", raising=False)
    assert app_settings.prefetch_lookups() is False
    assert app_settings.Settings().prefetch_lookups is False
    monkeypatch.setenv("PREFETCH_LOOKUPS", "1")
    assert app_settings.Settings().prefetch_lookups is True


def test_switch_off_gives_todays_turns_exactly(off, on, tmp_path, clock, mock, monkeypatch):
    """Off, the turn result and the tool log are what a server without the switch gives."""
    monkeypatch.delenv("PREFETCH_LOOKUPS", raising=False)
    default = Agent(make_server(tmp_path / "default", clock, mock[0]), clock)
    lines = ["Hi, the heat's out in my house.", f"It's 101 Union Street, {IN_AREA}.",
             "Sorry, I mean 11215.", f"The zip is {OUT_OF_AREA}."]
    for agent in (off, default):
        call = agent.start(UNKNOWN, call_id="same")["data"]["call_id"]
        agent.bodies = [agent.turn(call, line) for line in lines]
        assert count(agent, "SELECT COUNT(*) FROM tool_calls WHERE call_id = ? AND tool != 'turn'"
                     " AND tool NOT LIKE 'calls.%'", call) == 0
    assert off.bodies == default.bodies
    assert all("lookup" not in b["data"] for b in off.bodies)
    assert all("lookup_taken_back" not in b["data"]["hints"] for b in off.bodies)


def test_server_notes_without_a_lookup_are_unchanged():
    hints, known = ["commercial"], {"ZIP said": IN_AREA}
    assert server_notes(hints, known) == server_notes(hints, known, None)


# ---- every gate still refuses with the switch on -------------------------------------------------


def test_booking_still_needs_the_read_back(on):
    call = opened(on)
    on.turn(call, f"It's 101 Union Street, {IN_AREA}.")
    slot = first_open(on, call)
    assert book(on, call, slot)["refused"] == "gate_read_back"


def test_the_read_back_still_needs_a_zip_the_caller_said(on):
    call = opened(on)
    on.turn(call, "It's 101 Union Street.")
    asked = on.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert asked["refused"] == "zip_not_heard"


def test_the_read_back_still_needs_the_callers_yes(on):
    call = opened(on)
    on.turn(call, f"It's 101 Union Street, {IN_AREA}.")
    asked = on.tool("read_back", call, address=in_area_address(), phone="caller_id")
    assert asked["ok"]
    confirmed = on.tool("read_back", call, address=in_area_address(), phone="caller_id",
                        confirmed=True)
    assert confirmed["refused"] == "gate_read_back"


def test_an_out_of_area_zip_still_cant_book(on):
    call = opened(on)
    address = {"street": "1520 Flatbush Ave", "unit": "", "city": "Brooklyn", "zip": OUT_OF_AREA}
    on.turn(call, f"It's 1520 Flatbush Avenue, {OUT_OF_AREA}.")
    on.tool("read_back", call, address=address, phone="caller_id")
    on.turn(call, "Yes, that's right.")
    on.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    slot = first_open(on, call)
    assert book(on, call, slot)["refused"] == "out_of_area"


def test_a_safety_call_runs_no_lookup_and_still_cant_book(on):
    call = on.start(UNKNOWN)["data"]["call_id"]
    data = on.turn(call, f"{GAS} I'm at 101 Union Street, {IN_AREA}.")["data"]
    assert data["safety"]["action"] == "speak_lines"
    assert "lookup" not in data
    assert prefetched(on, call) == []
    slot = first_open(on, call)
    assert book(on, call, slot)["refused"] == "gate_safety"


def test_a_commercial_call_runs_no_lookup_and_still_cant_book(on):
    call = on.start(UNKNOWN)["data"]["call_id"]
    data = on.turn(call, f"We run a restaurant at 101 Union Street, {IN_AREA}, the heat's out.")
    assert "commercial" in data["data"]["hints"]
    assert "lookup" not in data["data"]
    on.confirm_read_back(call, in_area_address())
    slot = first_open(on, call)
    assert book(on, call, slot)["refused"] == "customer_type"


def test_a_booking_with_the_switch_on_goes_through_the_same_gates_and_books(on):
    call = opened(on)
    on.confirm_read_back(call, in_area_address())
    slot = first_open(on, call)
    assert book(on, call, slot)["ok"]
