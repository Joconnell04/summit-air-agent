"""The server-led arm (issue #171, app/flow.py): each result names the next step, and the booking
order is held by the server from its own records."""

import pytest
from toolkit import TENANT_DIR, Agent, book, in_area_address, make_server, ready_to_book

from app import flow
from app.tenant import Tenant, load_agent

LINES = load_agent(TENANT_DIR)[0]["lines"]
APPROVAL, HOME = LINES["ask_approval"], LINES["ask_someone_home"]
# Summit Air asks no pets question (agent.yaml visit_questions.pets: false).
ACCESS = "Is there a gate code or buzzer the technician should know about?"


@pytest.fixture
def led(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0], server_led=True), clock)


def offer(agent, call, job_type="tune_up") -> dict:
    found = agent.tool("find_slots", call, job_type=job_type)
    assert found["ok"], found
    agent.say(call, found["say"])
    return found


def slot_of(found: dict) -> dict:
    return {"date": found["data"]["offer"]["date"], "window": found["data"]["offer"]["window"]}


def answer_visit_questions(agent, call) -> None:
    for question, reply in ((APPROVAL, "Yes, I own it."), (HOME, "Yep."), (ACCESS, "No.")):
        agent.say(call, question)
        agent.turn(call, reply)


# ---- switched off ----------------------------------------------------------------------------


def test_off_the_results_carry_no_next_and_openings_need_no_read_back(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "Hi, I need a tune-up.")
    found = agent.tool("find_slots", call, job_type="tune_up")
    assert found["ok"] and "next" not in found


# ---- next ------------------------------------------------------------------------------------


def test_every_result_names_the_next_step(led):
    call = led.start()["data"]["call_id"]
    led.turn(call, "Hi, the heat's out. It's 101 Union Street, 11231.")
    address = in_area_address()
    asked = led.tool("read_back", call, address=address, phone="caller_id")
    assert asked["ok"] and "confirmed true" in asked["next"]
    led.say(call, asked["say"])
    led.turn(call, "Yes.")
    confirmed = led.tool("read_back", call, address=address, phone="caller_id", confirmed=True)
    assert "find_slots" in confirmed["next"]
    found = offer(led, call)
    # The server asks the visit questions itself, from book_appointment's refusals.
    assert "call book_appointment with it" in found["next"]
    assert "book_appointment" in found["next"]


def test_a_replayed_result_keeps_its_next(led):
    call = ready_to_book(led)
    first = led.tool("find_slots", call, key="k1", job_type="tune_up")
    again = led.tool("find_slots", call, key="k1", job_type="tune_up")
    assert again["next"] == first["next"] != ""


# ---- openings after the read-back ------------------------------------------------------------


def test_openings_wait_for_a_confirmed_read_back(led):
    call = led.start()["data"]["call_id"]
    led.turn(call, "Hi, I need a tune-up.")
    refused = led.tool("find_slots", call, job_type="tune_up")
    assert refused["refused"] == "read_back_first" and refused["say"] == ""
    assert "read_back" in refused["next"]
    led.confirm_read_back(call, in_area_address())
    assert led.tool("find_slots", call, job_type="tune_up")["ok"]


def test_moving_a_visit_needs_no_read_back(led):
    call = led.start()["data"]["call_id"]
    led.turn(call, "I need to move my visit.")
    found = led.tool("find_slots", call, job_type="tune_up", moving_a_visit=True)
    assert found["ok"] and "reschedule_appointment" in found["next"]


def test_the_first_offer_on_a_no_heat_call_ends_with_the_open_door_line_once(led):
    call = ready_to_book(led)
    first = offer(led, call, "no_heat")
    # Before the closing question, which the model keeps.
    assert f"{LINES['open_door']} Does that work?" in first["say"]
    led.turn(call, "Anything later?")
    second = led.tool("find_slots", call, job_type="no_heat", time_of_day="afternoon")
    assert LINES["open_door"] not in second["say"]


def test_a_tune_up_offer_has_no_open_door_line(led):
    call = ready_to_book(led)
    assert LINES["open_door"] not in offer(led, call)["say"]


# ---- the visit questions before a booking ----------------------------------------------------


def test_a_booking_waits_for_the_visit_questions_one_at_a_time(led):
    call = ready_to_book(led)
    slot = slot_of(offer(led, call))
    led.turn(call, "Sure, that works.")
    refused = book(led, call, slot)
    assert refused["refused"] == "visit_questions_first"
    # Each with a short acknowledgment that changes from one question to the next.
    assert refused["say"] == f"Got it. {APPROVAL}"
    assert "call book_appointment again" in refused["next"]
    led.say(call, refused["say"])
    led.turn(call, "Yes.")
    refused = book(led, call, slot)
    assert refused["say"] == f"Okay. {HOME}"
    led.say(call, refused["say"])
    led.turn(call, "Yes, I'll be here.")
    refused = book(led, call, slot)
    assert refused["say"] == f"Got it. {ACCESS}"
    assert "pet" not in refused["say"]
    led.say(call, refused["say"])
    led.turn(call, "There's a buzzer.")
    assert book(led, call, slot)["ok"]


def test_an_estimate_has_nothing_to_approve(led):
    call = ready_to_book(led)
    slot = slot_of(offer(led, call, job_type="estimate"))
    led.turn(call, "Sure, that works.")
    refused = book(led, call, slot, job_type="estimate")
    assert refused["say"] == f"Got it. {HOME}"


def test_the_unit_is_asked_only_for_a_building(led):
    ctx = type("Ctx", (), {"tenant": Tenant.load(TENANT_DIR)})()

    def ctx_next(needs_unit):
        return flow._after_address(ctx, needs_unit)

    assert "apartment or unit number" in ctx_next(True)
    assert "apartment or unit number" not in ctx_next(False)
    # The system type is asked once, and 'completely out' only where it means something.
    assert "boiler, a furnace, central air or a mini-split" in ctx_next(False)
    assert "only on a no-heat or no-cooling call" in ctx_next(False)


def test_a_question_asked_without_an_answer_is_not_settled(led):
    call = ready_to_book(led)
    slot = slot_of(offer(led, call))
    led.turn(call, "That works.")
    led.say(call, f"{APPROVAL} {HOME} {ACCESS}")
    assert book(led, call, slot)["say"] == f"Got it. {APPROVAL}"


def test_questions_asked_before_the_offer_do_not_count(led):
    call = ready_to_book(led)
    answer_visit_questions(led, call)
    slot = slot_of(offer(led, call))
    led.turn(call, "That works.")
    assert book(led, call, slot)["refused"] == "visit_questions_first"


@pytest.mark.parametrize("line", ["Someone is home all day.", "We're home.", "I'm home all day."])
def test_someone_home_said_unasked_counts(led, line):
    call = ready_to_book(led)
    slot = slot_of(offer(led, call))
    led.turn(call, f"That works. {line}")
    for question in (APPROVAL, ACCESS):
        led.say(call, question)
        led.turn(call, "Yes.")
    assert book(led, call, slot)["ok"]


def test_answers_the_caller_gave_unasked_count(led):
    call = ready_to_book(led)
    slot = slot_of(offer(led, call))
    led.turn(call, "That works. I'm the owner, I'll be home all day, and there's a buzzer.")
    assert book(led, call, slot)["ok"]


def test_the_questions_in_other_words_count(led):
    call = ready_to_book(led)
    slot = slot_of(offer(led, call))
    led.turn(call, "That works.")
    for question in ("Can you approve repairs?", "Will anyone be home then?",
                     "Any gate code or pets?"):
        led.say(call, question)
        led.turn(call, "Yes.")
    assert book(led, call, slot)["ok"]


# ---- nothing left unsaid before end_call -----------------------------------------------------


def booked_call(led) -> tuple[str, dict]:
    call = led.start()["data"]["call_id"]
    led.turn(call, "Hi, I need a tune-up.")
    led.say(call, led.confirm_read_back(call, in_area_address())["say"])
    led.turn(call, "Pat.")
    slot = slot_of(offer(led, call))
    led.turn(call, "That works.")
    answer_visit_questions(led, call)
    booked = book(led, call, slot)
    assert booked["ok"], booked
    return call, booked


def test_end_call_waits_until_the_booking_sentence_was_said(led):
    call, booked = booked_call(led)
    refused = led.tool("end_call", call, reason="resolved")
    assert refused["refused"] == "say_first" and refused["say"] == booked["say"]
    led.say(call, booked["say"])
    led.turn(call, "No, that's everything.")
    ended = led.tool("end_call", call, reason="resolved")
    assert ended["ok"] and ended["data"]["hang_up"], ended
    assert "Take care" in ended["next"]


def test_end_call_holds_back_once_per_sentence(led):
    call, _ = booked_call(led)
    led.turn(call, "Okay, bye.")
    assert led.tool("end_call", call, reason="caller_goodbye")["refused"] == "say_first"
    assert led.tool("end_call", call, reason="caller_goodbye")["ok"]


def test_a_sentence_said_with_a_word_changed_counts_as_said():
    sentence = "You're all set for Tuesday between 9 and 9:30 AM. Anything else?"
    assert flow.said(sentence, ["Great, you're all set for Tuesday between 9 and 9:30. Anything else?"])
    assert not flow.said(sentence, ["Thanks so much for calling Summit Air today."])


def test_the_booking_sentence_in_haikus_words_counts_as_said():
    """Eval run 20261106-011527: held back at 0.62 of the words, so the call ended on a second
    end_call after an extra turn."""
    assert flow.said(
        "Okay, I've booked it: the technician will arrive tomorrow between 8 and 8:30. Anything else?",
        [
            (
                "You're booked for tomorrow, Friday, November 6, between 8 and 8:30 in the "
                "morning. Anything else I can help with?"
            )
        ],
    )



def test_end_call_never_waits_on_an_offer_the_caller_already_took(led):
    """Eval run 20261106-010112: the offer was said in other words, and holding the goodbye for it
    re-offered the window the caller had just booked."""
    call = led.start()["data"]["call_id"]
    led.turn(call, "Hi, I need a tune-up.")
    led.say(call, led.confirm_read_back(call, in_area_address())["say"])
    led.turn(call, "Pat.")
    found = led.tool("find_slots", call, job_type="tune_up")
    led.say(call, "The soonest opening is Tuesday, November 3, in the morning. Does that work?")
    led.turn(call, "Yes.")
    answer_visit_questions(led, call)
    booked = book(led, call, slot_of(found))
    led.say(call, booked["say"])
    led.turn(call, "No, bye.")
    assert led.tool("end_call", call, reason="caller_goodbye")["ok"]


def test_next_comes_right_after_say():
    from app.envelope import Result

    assert list(Result(True, "Hi.", {}, next="Wait.").body()) == ["ok", "say", "next", "data",
                                                                 "refused"]
    assert "next" not in Result(True, "Hi.").body()
