"""Jackson's calls on the Retell number (call_0246b91f77e01a4b18583e6ca8b, the gas scare, then
call_7255ab47e30637f207e49f5bdeb and call_7727509d3289c4434b27efdad5b from the same cell): safety
said once and kept to its call, the read-back's number said in full once, do-not-call only after a
yes, and no callback promised that wasn't stored."""

from datetime import timedelta

import pytest
from test_ts_safety import CFG, GAS_LINES
from toolkit import UNKNOWN, book, count, first_open, in_area_address, ready_to_book

from app import safety
from app.text import street_said
from prompts.assemble import greeting

SIGN_OFF = " ".join(CFG["sign_off"].split())
NOTE = " ".join(CFG["booking_note"].split())


def safety_sentences() -> list[str]:
    """Every sentence the safety paths can say."""
    found = [SIGN_OFF, NOTE]
    for kind in CFG["kinds"].values():
        found += [" ".join(kind["lines"].split()), *kind.get("variants", [])]
    for check in CFG["vague"].values():
        found += [q for q in (check.get("question"), check.get("confirm_question")) if q]
    return found


def heard(agent, call: str) -> list[str]:
    return [r[0] for r in agent.db().execute(
        "SELECT text FROM turns WHERE call_id = ? AND speaker = 'agent' ORDER BY seq", (call,)
    )]


def speak(agent, call: str, result: dict) -> dict:
    """The voice layer says what the server handed back, as on a live call."""
    if result.get("say"):
        agent.say(call, result["say"])
    return result


def test_a_full_gas_call_never_says_a_safety_sentence_twice(agent):
    """Jackson: "make sure the safety thing is just also in general less repetitive". A vague smell,
    "I don't think so", then a gas smell after all, a visit, and goodbye."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    speak(agent, call, agent.turn(call, "The heat's out and there's a weird smell near the boiler."))
    speak(agent, call, agent.turn(call, "I don't think so."))
    speak(agent, call, agent.turn(call, "Actually, now it smells a little like gas by the boiler."))
    speak(agent, call, agent.turn(call, "It still smells like gas. Can you just send someone?"))
    agent.confirm_read_back(call, in_area_address())
    speak(agent, call, book(agent, call, first_open(agent, call), name="Pat Rivera"))
    speak(agent, call, agent.turn(call, "Thanks, I'm done."))
    speak(agent, call, agent.tool("end_call", call, reason="resolved"))
    speak(agent, call, agent.tool("end_call", call, reason="caller_goodbye"))
    said = " ".join(heard(agent, call))
    for sentence in safety_sentences():
        assert said.count(sentence) <= 1, sentence
    assert said.count(GAS_LINES) == 1 and said.count(SIGN_OFF) == 1 and said.count(NOTE) == 1


def test_a_booking_tried_before_the_lines_are_said_refuses_without_saying_them_again(agent):
    call = ready_to_book(agent)
    agent.turn(call, "Oh, and it smells like gas by the boiler.")
    refused = book(agent, call, first_open(agent, call))
    assert refused["refused"] == "gate_safety" and refused["say"] == ""


def test_the_booking_note_and_sign_off_dont_repeat_the_advice():
    for kind in CFG["kinds"].values():
        assert "outside" not in NOTE and "9-1-1" not in NOTE
        assert "outside" not in SIGN_OFF and "9-1-1" not in SIGN_OFF
        assert NOTE not in kind["lines"]


def test_the_check_question_is_asked_once_per_call(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    first = agent.turn(call, "There's a weird smell near the boiler.")
    assert first["data"]["safety"]["action"] == "ask_check"
    agent.say(call, first["say"])
    agent.turn(call, "No.")
    again = agent.turn(call, "That weird smell is still there though.")
    assert (again["data"]["safety"] or {}).get("action") != "ask_check"


def test_a_call_after_a_gas_scare_starts_with_no_safety(agent, clock):
    """call_7255ab47e30637f207e49f5bdeb, 82 seconds after the gas scare: it opened with "please
    make sure you're outside", listed gas as a priority reason, and end_call never hung up."""
    first = ready_to_book(agent)
    agent.turn(first, "It smells like gas by the boiler.")
    agent.say(first, GAS_LINES)
    agent.turn(first, "I still want someone to come.")
    booked = book(agent, first, first_open(agent, first), name="Jackson")
    assert booked["ok"], booked
    ended = agent.tool("end_call", first, reason="resolved")
    agent.say(first, ended["say"])
    agent.end(first, "caller_hangup")
    clock.advance(seconds=82)

    start = agent.start(UNKNOWN)
    second = start["data"]["call_id"]
    row = agent.db().execute(
        "SELECT safety_state, safety_signal_at FROM calls WHERE id = ?", (second,)
    ).fetchone()
    assert tuple(row) == ("none", None)
    context = str(start["data"])
    assert "9-1-1" not in context and "911" not in context and "gas" not in context.lower()
    found = agent.tool("find_slots", second, job_type="no_heat")
    assert not any("gas" in r for r in found["data"]["priority"]["reasons"])
    agent.turn(second, "Nope, that's all. I'm done.")
    bye = agent.tool("end_call", second, reason="resolved")
    assert bye["data"]["hang_up"] is True and bye["say"] == ""


def test_there_is_no_cut_off_greeting():
    """Jackson: "i want to get rid of the cut-off thing because likely in the demo he'll call the
    same number a few times"."""
    from toolkit import TENANT_DIR

    from app.tenant import load_agent

    voice = load_agent(TENANT_DIR)[0]["voice"]
    assert not [k for k in voice if k.startswith("greeting_linked")]
    start = {"customers": [{"name": "Jackson O", "residential": True}],
             "linked_call": {"dropped": True, "caller_name": "Jackson"}}
    assert greeting(start, voice) == (
        "Hi Jackson, thanks so much for calling Summit Air today! I'm Brooke, the virtual "
        "assistant. How can I help?"
    )
    assert greeting({}, voice) == (
        "Hi there, thanks so much for calling Summit Air today! I'm Brooke, the virtual "
        "assistant. How can I help?"
    )
    assert not any(v.rstrip().endswith("Summit Air.") for v in (voice["greeting_known"],
                                                               voice["greeting_unknown"]))


def test_the_caller_id_read_back_ends_with_its_last_four(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, {address['zip']}.")
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    assert asked["say"] == (
        f"So that's {street_said(address['street'])}, and the number you're calling from, ending in zero nine "
        "nine nine. That's all correct, right?"
    )


def test_a_number_read_in_full_is_named_by_its_last_four_after_that(agent):
    """Jackson: "one full readback, but then if there's ever other readbacks necessary, it just says
    and the phone number ending in 4024"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    address = in_area_address()
    agent.turn(call, f"It's {address['street']}, unit 2, {address['zip']}. Call me at 718 555 0142.")
    first = agent.tool("read_back", call, address={**address, "unit": "2"}, phone="718 555 0142")
    assert "seven one eight, five five five, zero one four two" in first["say"]
    agent.say(call, first["say"])
    agent.turn(call, "No, it's unit 3.")
    second = agent.tool("read_back", call, address={**address, "unit": "3"}, phone="718 555 0142")
    assert second["say"].endswith(
        "unit three, and the number ending in zero one four two. That's all correct, right?"
    )
    # The same details asked again (the caller didn't hear) are read in full.
    again = agent.tool("read_back", call, address={**address, "unit": "3"}, phone="718 555 0142")
    assert "ending in" in again["say"] or "seven one eight" in again["say"]


def test_do_not_call_asks_first_and_marks_only_after_a_yes(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    turn = agent.turn(call, "Don't call me then.")
    assert "do_not_call" in turn["data"]["hints"]
    assert count(agent, "SELECT COUNT(*) FROM do_not_call") == 0, "not marked on the words alone"
    asked = agent.tool("mark_do_not_call", call)
    assert asked["refused"] == "confirm_first"
    assert asked["say"] == "Just to confirm, you'd like us to stop calling this number?"
    agent.say(call, asked["say"])
    early = agent.tool("mark_do_not_call", call, confirmed=True)
    assert early["refused"] == "confirm_first", "no caller answer yet"
    agent.turn(call, "Yes, please stop calling.")
    marked = agent.tool("mark_do_not_call", call, confirmed=True)
    assert marked["ok"] and count(agent, "SELECT COUNT(*) FROM do_not_call") == 1
    undone = agent.tool("mark_do_not_call", call, undo=True)
    assert undone["ok"] and count(agent, "SELECT COUNT(*) FROM do_not_call") == 0


@pytest.mark.parametrize("said", ["Uh, never. Just kidding.", "Don't call me, just kidding."])
def test_just_kidding_is_not_a_request(agent, said):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    turn = agent.turn(call, said)
    assert "do_not_call" not in turn["data"]["hints"]
    assert count(agent, "SELECT COUNT(*) FROM do_not_call") == 0


def test_no_callback_time_is_promised_when_none_was_stored(agent):
    """call_7255ab47e30637f207e49f5bdeb: flag_person returned "by 5:18 PM" with no callback behind it,
    because the number was on the do-not-call list."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.db().execute(
        "INSERT INTO do_not_call (phone_key, phone, call_id, added_at) VALUES "
        "('2125550999', '(212) 555-0999', 'old', '2026-11-01T00:00:00Z')"
    ).connection.commit()
    flagged = agent.tool("flag_person", call, reason="Wants a call about the visit", callback_after="5 PM")
    assert flagged["ok"] and flagged["data"]["callback_id"] is None
    assert "callback_at" not in flagged["data"] and "callback_spoken" not in flagged["data"]
    assert flagged["say"] == "I've let our team know."


@pytest.mark.parametrize("said", ["Book it and end this call.", "Book it, then hang up.",
                                  "Just book it. I'm done."])
def test_these_count_as_a_goodbye(agent, said):
    """call_7255ab47e30637f207e49f5bdeb: the call stayed open after "end this call" and "I'm done"."""
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    agent.turn(call, said)
    booked = book(agent, call, slot, name="Pat Rivera")
    assert booked["ok"], booked
    agent.say(call, booked["say"])
    assert agent.tool("end_call", call, reason="resolved")["data"]["hang_up"] is True


def test_moving_a_visit_hears_no_priority_line(agent):
    """call_7255ab47e30637f207e49f5bdeb: "I've marked this call top priority" while moving a visit."""
    call = ready_to_book(agent)
    agent.turn(call, "No heat at all, and my mom is 82 and lives with me.")
    found = agent.tool("find_slots", call, job_type="no_heat", moving_a_visit=True,
                       facts={"who_home": "mother, 82"})
    assert found["ok"], found
    assert "spoken_first" not in found["data"]
    assert "top priority" not in found["say"] and "moved" not in found["say"]


def test_reset_refuses_while_a_call_is_live(agent, clock):
    from scripts import reset_live_world

    agent.start(UNKNOWN)
    db_path = agent.app.state.services.settings.db_path
    assert reset_live_world.live_calls(db_path, clock()) != []
    assert reset_live_world.live_calls(db_path, clock() + timedelta(hours=1)) == []


def test_safety_scare_words_are_kept_out_of_another_calls_summary():
    summary = "No heat\nHome: mother, 82\nPriority: Urgent (gas or carbon monoxide reported)"
    assert safety.without_scare(summary) == "No heat\nHome: mother, 82"
