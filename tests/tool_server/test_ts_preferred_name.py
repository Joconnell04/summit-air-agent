"""What the caller asked to be called (app/address_as.py): read from their words, used for the
rest of the call and on a linked call, refused when mocking or abusive, and saved on the customer
record for the next call."""

import pytest
import yaml
from toolkit import TENANT_DIR, UNKNOWN, book, count, first_open, ready_to_book

from app import address_as
from prompts import assemble

PLACES = address_as.place_words(
    yaml.safe_load((TENANT_DIR / "config.yaml").read_text())["address_lookup"]
)


@pytest.mark.parametrize(
    "line, name",
    [
        ("Please call me Dr. Patel.", "Dr. Patel"),
        ("you can call me doctor patel", "Dr. Patel"),
        ("It's Ms. Rivera, by the way.", "Ms. Rivera"),
        ("This is Reverend Okafor calling about the boiler", "Reverend Okafor"),
        ("I go by Sam.", "Sam"),
        ("Just call me Bea, everyone does.", "Bea"),
        ("I'd prefer to be called Mr. McKenzie.", "Mr. McKenzie"),
        ("Call me Jo Ann, please.", "Jo Ann"),
        # As phone speech-to-text writes it: no punctuation, no capitals, the name after an intro.
        ("hi this is anjali patel please call me dr patel id like to book my boiler tune up",
         "Dr. Patel"),
    ],
)
def test_a_form_of_address_is_read_from_the_callers_words(line, name):
    assert address_as.read(line) == ("ok", name)


@pytest.mark.parametrize(
    "line",
    [
        "Can you call me back after 5?",
        "Call me at 718 555 0142.",
        "Call me tomorrow instead.",
        "This is Maria, my heat's out.",
        "It's about my furnace.",
        "Call me crazy, but it smells like dust.",
        "I'm calling about the heat.",
        # Review of #125: days, times and figures of speech are not names.
        "call me Tuesday",
        "Call me Monday.",
        "call me right back",
        "call me noon",
        "call me up",
        "call me sometime",
        "call me whatever",
        "I go by bus",
        "Call me Tuesday afternoon, please.",
        # A given name needs a capital, as speech-to-text writes names; lowercase is ambiguous.
        "just call me bea everyone does",
        # A possessive is someone else's name.
        "I'm Dr. Patel's wife.",
        "it's dr patels wife calling",
        # Re-check of #125: rides, places, urgency and fillers.
        "Call me an Uber.",
        "Can you call me a Lyft?",
        "I go by Amtrak.",
        "I go by Greyhound.",
        "I go by Metro.",
        "Call me Outside.",
        "Call me Immediately.",
        "Call me Urgent.",
        "Call me Hello.",
        "Call me Him.",
        "Call me Brooklyn.",
    ],
)
def test_lines_that_ask_for_no_name_are_ignored(line):
    assert address_as.read(line, PLACES) is None


@pytest.mark.parametrize(
    "line",
    [
        "Call me Your Majesty.",
        "Call me master.",
        "You can call me Lord Vader.",
        "call me daddy",
        # Review of #125: abuse inside a word.
        "Call me Mr. Fuckface.",
        "Call me Shithead.",
        "Just call me Asshat.",
        "Call me Dumbass.",
        "Call me Retard.",
        "Call me Jackass.",
        "Call me Dr. Bitchface.",
        "Call me Poopface.",
        "Call me Mr. Butthead.",
        "Call me Scumbag.",
        "Call me Pissbaby.",
        "Call me Jerk.",
        "Call me Numbnuts.",
        "Call me Dingleberry.",
        "Call me Boobs.",
        "Call me Ms. Dyke.",
    ],
)
def test_a_mocking_form_of_address_is_refused(line):
    assert address_as.read(line)[0] == "refused"


def test_every_listed_slur_is_refused_inside_a_name():
    for stem in address_as.REFUSED_STEMS:
        assert address_as.read(f"Call me Mr. {stem.capitalize()}face.")[0] == "refused", stem
    for word in address_as.REFUSED:
        assert address_as.read(f"Call me {word.capitalize()}.")[0] == "refused", word


@pytest.mark.parametrize(
    "line, name",
    [
        ("Call me Ms. Glass.", "Ms. Glass"),
        ("I go by Cass.", "Cass"),
        ("Call me Mr. Scunthorpe.", "Mr. Scunthorpe"),
        ("Call me Dr. Shittu.", "Dr. Shittu"),
        ("Call me Mr. Wankel.", "Mr. Wankel"),
        ("Call me Ms. Mass.", "Ms. Mass"),
        ("Call me Mr. Pass.", "Mr. Pass"),
        ("Call me Ms. Tass.", "Ms. Tass"),
        ("Call me Mr. Assange.", "Mr. Assange"),
        ("Call me Mr. Butts.", "Mr. Butts"),
        ("Call me Ms. Buttons.", "Ms. Buttons"),
    ],
)
def test_real_names_that_contain_a_refused_stem_are_kept(line, name):
    assert address_as.read(line) == ("ok", name)


def test_the_name_is_kept_for_the_call_and_told_to_the_agent(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    result = agent.turn(call, "Hi, it's Dr. Patel. My AC isn't cooling.")
    assert "preferred_name" in result["data"]["hints"]
    assert result["data"]["known"]["call them"] == "Dr. Patel"
    later = agent.turn(call, "It's the upstairs unit.")
    assert later["data"]["known"]["call them"] == "Dr. Patel"
    assert "preferred_name" not in later["data"]["hints"]
    notes = assemble.server_notes(result["data"]["hints"], result["data"]["known"])
    assert any("call them" in n for n in notes)


def test_a_refused_name_is_not_kept(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    result = agent.turn(call, "Call me Your Majesty.")
    assert "preferred_name_refused" in result["data"]["hints"]
    assert "call them" not in result["data"]["known"]
    assert count(agent, "SELECT COUNT(*) FROM call_names") == 0


def test_a_later_name_replaces_the_first(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Call me Sam.")
    result = agent.turn(call, "Actually, call me Dr. Okafor.")
    assert result["data"]["known"]["call them"] == "Dr. Okafor"


def test_a_linked_call_keeps_the_name_and_greets_with_it(agent, clock):
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "This is Dr. Patel, my heat's out.")
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)["data"]
    assert second["preferred_name"] == "Dr. Patel"
    assert second["linked_call"]["preferred_name"] == "Dr. Patel"
    voice = {"greeting_linked": "Sorry, we got cut off. Is this {first_name}?",
             "greeting_linked_unknown": "Sorry, we got cut off.", "greeting_known": "Is this {first_name}?",
             "greeting_unknown": "Hi."}
    assert assemble.greeting(second, voice) == "Sorry, we got cut off. Is this Dr. Patel?"
    turn = agent.turn(second["call_id"], "Yes, it's me.")
    assert turn["data"]["known"]["call them"] == "Dr. Patel"


def test_the_name_goes_on_the_booked_customer_and_greets_the_next_call(agent, clock):
    call = ready_to_book(agent)
    agent.turn(call, "Oh, and please call me Dr. Patel.")
    booked = book(agent, call, first_open(agent, call))
    assert booked["ok"], booked
    agent.end(call)
    saved = count(agent, "SELECT COUNT(*) FROM call_names WHERE saved_at IS NOT NULL")
    assert saved == 1
    clock.advance(days=1)
    nxt = agent.start(UNKNOWN)["data"]
    assert nxt["preferred_name"] == "Dr. Patel"
    voice = {"greeting_known": "Hi, is this {first_name}?", "greeting_unknown": "Hi."}
    assert assemble.greeting(nxt, voice) == "Hi, is this Dr. Patel?"


def test_a_known_caller_gets_the_note_once(agent, scenarios, clock):
    linda = scenarios["activeMemberWithAdultChild"]
    for _ in range(2):
        call = agent.start(linda["phone"])["data"]["call_id"]
        agent.turn(call, "Please call me Mrs. Park.")
        agent.end(call)
        clock.advance(days=1)
    fsm = agent.app.state.services.fsm["summit-air"]
    customer = fsm.customers_by_phone(linda["phone"])[0]
    notes = [n["text"] for n in fsm.notes(customer["id"])]
    assert notes.count("Prefers to be called Mrs. Park.") == 1


def test_no_customer_no_note(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Call me Sam.")
    agent.end(call)
    assert count(agent, "SELECT COUNT(*) FROM call_names WHERE saved_at IS NOT NULL") == 0
