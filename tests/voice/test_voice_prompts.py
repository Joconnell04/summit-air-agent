"""The prompt files and tool list both voice layers load (prompts/)."""

import json
import re
from datetime import UTC, datetime

import pytest

from app.tools import TOOLS
from prompts import assemble
from tests.tool_server.toolkit import TENANT_DIR


def test_instructions_name_the_company_from_the_call_context(agent):
    start = agent.start()["data"]
    text = assemble.instructions(start, datetime(2026, 11, 2, 14, 0, tzinfo=UTC))
    assert "{company}" not in text
    assert start["tenant"]["name"] in text
    assert "Monday 2 November 2026, 9:00 AM" in text
    # The tenant's knowledge comes in through the call context, not the prompt files.
    assert "Have a great rest of your day" in text


def test_no_company_fact_is_written_into_the_prompt_files():
    for path in assemble.PROMPTS.glob("*.md"):
        if path.name == "README.md":
            continue
        assert "Summit" not in path.read_text(), path.name
        assert not re.search(r"\d{3}\W{1,3}\d{3}\W{1,3}\d{4}", path.read_text()), path.name
        assert not re.search(r"\b\d{5}\b", path.read_text()), path.name  # a ZIP


def test_every_server_tool_has_a_description_and_its_arguments():
    specs = {s["name"]: s for s in assemble.tool_specs(assemble.job_types(TENANT_DIR))}
    assert set(specs) == set(TOOLS)
    for name, spec in specs.items():
        assert spec["description"], name
        assert "call_id" not in spec["parameters"]["properties"], name
        assert "$ref" not in json.dumps(spec["parameters"]), name
        assert spec["write"] == TOOLS[name][2], name


def test_job_types_are_limited_to_the_tenants_own():
    specs = {s["name"]: s for s in assemble.tool_specs(assemble.job_types(TENANT_DIR))}
    allowed = specs["book_appointment"]["parameters"]["properties"]["job_type"]["enum"]
    assert set(allowed) == {"no_heat", "no_cooling", "tune_up", "safety_check", "estimate"}


def test_server_hints_become_notes_and_unknown_hints_are_dropped():
    notes = assemble.server_notes(["commercial", "journey:safety"])
    assert len(notes) == 1 and notes[0].startswith("[server]")


def test_first_name():
    assert assemble.first_name("Maria Lopez") == "Maria"
    assert assemble.first_name("  ") == ""


def test_booking_tools_say_the_window_is_the_code_not_the_spoken_label():
    specs = {s["name"]: s for s in assemble.tool_specs(assemble.job_types(TENANT_DIR))}
    for name in ("book_appointment", "reschedule_appointment"):
        window = specs[name]["parameters"]["properties"]["slot"]["properties"]["window"]
        assert "code" in window["description"] and "label" in window["description"], name
        assert "label" in specs[name]["description"], name


def test_instructions_keep_the_fixes_from_the_must_pass_reliability_round(agent):
    # Each line here fixed a must-pass miss in evals/rounds/reliability-01.md; losing one brings
    # the miss back.
    text = assemble.instructions(agent.start()["data"], datetime(2026, 11, 2, 14, 0, tzinfo=UTC))
    # Eval round 2 (PR #74) replaced "if the caller asks for a visit anyway" with an offer; the fix
    # it carried, booking the normal way without arguing, is kept.
    assert "book it the normal way" in text and "without arguing" in text
    assert "offer to stay on the line" in text
    assert "call `end_call` without saying anything first" in text or "Then call `end_call`" in text
    assert "the technician prices the work in person" in text
    assert "Never say whether that person is a customer" in text
    assert "to a renter only, never to an owner or landlord" in text
    assert "A yes still counts when the caller says more in the same turn" in text
    assert 'Give no medical advice, not even "keep them upright"' in text
    assert "if they ask what to do for the person, the 9-1-1 operator will tell them" in text
    assert "never an owner or landlord: one neutral line" in text
    assert "Once someone is calling 9-1-1, carry on to booking the visit as usual." in text
    assert "Add no other reason, referral, advice or follow-up" in assemble.server_notes(["commercial"])[0]
    assert "it's the soonest there is: say so plainly rather than offering to move it up" in text
    assert "A caller who threatens to hang up hasn't" in text
    assert "without asking the caller's permission or mentioning it" in text
    assert "No follow-up, referral or advice." in text
    assert "An out-of-area caller gets no lead." in text
    assert "don't bring up gas safety yourself" in text


def test_the_voice_layer_speaks_the_read_back_the_safety_refusal_and_the_sign_off():
    spoken = assemble.spoken_by_server
    assert spoken("read_back", {"ok": True, "say": "I have your home at 1 Main St."})
    assert spoken("read_back", {"ok": False, "refused": "gate_read_back", "say": "I have..."})
    assert spoken("read_back", {"ok": False, "refused": "invalid_phone", "say": "Again?"}) is None
    assert spoken("read_back", {"ok": True, "say": "", "data": {"confirmed": True}}) is None
    assert spoken("find_slots", {"ok": True, "say": "Tuesday works."}) is None


def test_the_reply_rule_holds_text_only_while_a_read_back_can_come_or_on_a_safety_call():
    reply = assemble.SpokenReply()
    assert not reply.holding()
    reply.tool_done("check_service_area", {"ok": True})
    assert reply.holding()
    reply.tool_done("read_back", {"ok": True, "data": {"confirmed": True}})
    assert not reply.holding()
    reply.heard({"action": "speak_lines"})
    assert reply.holding() and reply.server_speaks("end_call", {})
    reply.heard({"action": "cleared"})
    assert not reply.holding() and not reply.server_speaks("end_call", {})


VOICE = {
    "greeting_known": "Hi, this is Summit Air. Is this {first_name}?",
    "greeting_unknown": "Thanks for calling Summit Air, this is the virtual assistant.",
    "greeting_linked": "Hi again. I think we got cut off earlier. Is this {first_name}?",
    "greeting_linked_unknown": "Hi again. I think we got cut off earlier. Who am I speaking with?",
}


def test_a_caller_whose_call_dropped_today_is_greeted_by_the_name_they_gave():
    """Eval round 2, seed 6: the agent mentions the earlier call and checks it is the same person."""
    linked = {"dropped": True, "caller_name": "Tomasz"}
    assert assemble.greeting({"linked_call": linked}, VOICE).endswith("Is this Tomasz?")
    matched = {"linked_call": linked, "customers": [{"name": "Maria Delgado", "residential": True}]}
    assert assemble.greeting(matched, VOICE).endswith("Is this Maria?")
    nameless = {"linked_call": {"dropped": True, "caller_name": None}}
    assert assemble.greeting(nameless, VOICE).endswith("Who am I speaking with?")
    # A linked call that didn't drop, or a tenant without the line, gets the usual greeting.
    finished = {"linked_call": {"dropped": False, "caller_name": "Tomasz"}}
    assert assemble.greeting(finished, VOICE) == VOICE["greeting_unknown"]
    plain = {k: v for k, v in VOICE.items() if not k.startswith("greeting_linked")}
    assert assemble.greeting({"linked_call": linked}, plain) == VOICE["greeting_unknown"]


def test_the_prompt_says_how_times_spelling_and_other_addresses_work():
    """Eval round 2: "8 to 2" is daytime, a street the agent can't confirm is spelled, and a second
    address asks whose account it goes on. The server enforces each; the prompt makes it smooth."""
    text = "\n".join((assemble.PROMPTS / p).read_text() for p in assemble.PARTS)
    assert '"8 to 2" is 8 AM to 2 PM' in text
    assert "spell a street name only when" in text and "`spelled`" in text
    # Eval round 3: a clear street is never spelled.
    assert "a clear street name" in text and "is never spelled" in text
    assert "new_address_account" in text


def test_the_caller_digits_are_read_from_spoken_digits_or_numerals():
    zips, phones = assemble.caller_digits(
        "it's one eight eight 10 64th avenue fresh meadows one one three six six and my cell is "
        "six three one five five five oh one four seven"
    )
    assert zips == {"11366"}
    assert phones == {"6315550147"}


def test_a_sentence_on_file_is_kept_when_the_caller_never_said_its_zip():
    reply = assemble.SpokenReply()
    on_file = "The address I have is 290 Bedford Avenue, Brooklyn, 11218. Is that still right?"
    assert reply.text(on_file, []) == on_file
    reply.heard(None, "yes it's one one two one eight")
    assert reply.text(on_file, []) == "Is that still right?"


def test_a_bare_confirmation_after_a_dropped_restatement_is_dropped_too():
    """Eval round 3 run 20261104-031113, heat-pump-defrost: "Is that right?" was left alone."""
    reply = assemble.SpokenReply()
    reply.heard(None, "two one two 30 41st ave bayside one one three six one")
    said = "Tomorrow morning is open. So that's 21230 41st Avenue, Bayside, 11361. Is that right?"
    assert reply.text(said, []) == "Tomorrow morning is open."
    # A question that stands on its own is kept.
    assert reply.text("Is that right?", []) == "Is that right?"


def test_a_restated_street_address_is_dropped_but_a_temperature_is_not():
    """Eval round 3 run 20261104-031113, open-job-other-address."""
    reply = assemble.SpokenReply()
    reply.heard(None, "it's one eight five seven 77th street brooklyn and it's 64 degrees in here")
    said = "So that's 1857 77th Street, Brooklyn, correct? Got it, 64 degrees. Who's home?"
    assert reply.text(said, []) == "Got it, 64 degrees. Who's home?"


def test_the_booking_reply_keeps_its_short_line_and_the_confirmation_is_spoken_by_the_server():
    """Eval round 4: one model request on the booking turn. The model says a short line in the
    reply that books, and the voice layer speaks the server's confirmation as it is."""
    reply = assemble.SpokenReply()
    line = "Perfect, booking that now."
    assert reply.text(line, [("book_appointment", {"slot": {}})]) == line
    booked = {"ok": True, "say": "You're all set for today. Anything else?"}
    assert assemble.spoken_by_server("book_appointment", booked) == booked["say"]
    assert assemble.spoken_by_server("reschedule_appointment", {"ok": True, "say": "Done."})
    assert assemble.spoken_by_server("cancel_appointment", {"ok": True, "say": "Cancelled."})
    queued = {"ok": False, "refused": "fsm_unavailable", "say": "I can't book that right now."}
    assert assemble.spoken_by_server("book_appointment", queued) == "I can't book that right now."
    refused = {"ok": False, "refused": "slot_taken", "say": "That window just filled up."}
    assert assemble.spoken_by_server("book_appointment", refused) is None


def test_a_confirmed_address_and_number_are_never_restated_by_the_model():
    reply = assemble.SpokenReply()
    reply.tool_done("read_back", {"ok": True, "data": {
        "confirmed": True, "phone": "2125550999",
        "address": {"street": "290 Bedford Ave", "unit": "", "city": "Brooklyn", "zip": "11218"},
    }})
    said = "Booked for 290 Bedford Ave, 11218. I'll call 212-555-0999 if needed. Anything else?"
    assert reply.text(said, []) == "Anything else?"


def test_top_priority_is_said_only_on_a_call_the_server_marked_urgent():
    reply = assemble.SpokenReply()
    assert reply.text("I've marked this as top priority. Let's book.", []) == "Let's book."
    reply.tool_done("find_slots", {"ok": True, "data": {"priority": {"level": "Urgent"}}})
    said = "I've marked this call top priority, so you get our soonest opening."
    assert reply.text(said, []) == said


def test_the_model_saying_it_raised_the_call_is_dropped_but_saying_it_cant_is_kept():
    """Eval run 20261104-173249, adds-fact-after-wait."""
    reply = assemble.SpokenReply()
    claims = "I've marked it as a priority because of your mother. Let me check times."
    assert reply.text(claims, []) == "Let me check times."
    assert reply.text("I'll put you first on the list. Today works.", []) == "Today works."
    assert reply.text("We've moved you up. Today works.", []) == "Today works."
    honest = "I can't mark it urgent based on something that isn't true."
    assert reply.text(honest, []) == honest
    plain = "I'll put you down for the morning, and someone will follow up."
    assert reply.text(plain, []) == plain


def test_gas_advice_is_dropped_on_a_call_with_no_safety_signal():
    """Eval round 4, gas-worry-no-smell."""
    reply = assemble.SpokenReply()
    said = "The technician will check it. If you smell gas, get everyone out and call 911."
    assert reply.text(said, []) == "The technician will check it."
    # Medical 911 advice has no gas in it and stays.
    assert reply.text("Please call 911 now.", []) == "Please call 911 now."
    reply.heard({"action": "speak_lines"})
    kept = "I'd recommend staying outside and calling 911 or the gas company now."
    assert reply.text(kept, []) == kept


def test_the_knowledge_says_apartment_buildings_are_homes(agent):
    """Eval round 4 run 20261104-040959, property-manager-books-building: the knowledge folder
    still said "not whole buildings", against eval round 2's residential rule."""
    text = assemble.instructions(agent.start()["data"], datetime(2026, 11, 2, 14, 0, tzinfo=UTC))
    assert "whole buildings" not in text
    assert "whole apartment buildings, including a building's shared boiler" in text


def test_the_model_describing_its_tools_is_never_heard():
    """Eval round 4 run 20261104-040959, burning-dust-first-heat."""
    reply = assemble.SpokenReply()
    said = "The tool says it's marked top priority. Today I have 12 to 4."
    assert reply.text(said, []) == "Today I have 12 to 4."
    assert reply.text("The system shows an opening today.", []) == ""
    # The caller's own heating system is not a tool.
    assert reply.text("Is the system a boiler?", []) == "Is the system a boiler?"


def test_carbon_monoxide_talk_is_dropped_on_a_call_with_no_safety_signal():
    """Eval round 4 run 20261104-040959, no-heat-gas-stove."""
    reply = assemble.SpokenReply()
    said = "Got it. Please don't use the stove for heat, it gives off carbon monoxide."
    assert reply.text(said, []) == "Got it."
    reply.heard({"action": "speak_lines"})
    assert reply.text(said, []) == said


def test_a_second_separate_question_in_one_reply_waits():
    """Eval round 4: one question at a time; an "or" question is one question."""
    reply = assemble.SpokenReply()
    said = "Do you know what kind of system it is? No worries if not. How old is it, roughly?"
    assert reply.text(said, []) == "Do you know what kind of system it is? No worries if not."
    one = "Is it your place, or are you renting?"
    assert reply.text(one, []) == one


def test_a_hyphenated_top_priority_claim_is_dropped_too():
    """Eval run 20261104-052332, no-heat-unknown-caller."""
    reply = assemble.SpokenReply()
    said = "Since it's below 65 inside, this is a top-priority visit. Today I have 12 to 4."
    assert reply.text(said, []) == "Today I have 12 to 4."


def test_flag_a_fee_claim_and_a_second_recommendation_are_dropped():
    reply = assemble.SpokenReply()
    said = "I'll flag this for our team. There's no charge for the visit itself. Anything else?"
    assert reply.text(said, []) == "Anything else?"
    assert reply.text("The technician will go over any costs first.", []) != ""
    reply.heard({"action": "speak_lines"})
    first = "I'd recommend staying outside and calling 911 now."
    assert reply.text(first, []) == first
    assert reply.text("Please stay outside and call 911 or the gas company.", []) == ""


def test_a_building_not_a_home_answer_stays_residential(agent):
    """Eval runs 20261104-040959 and 052332, property-manager-books-building: "it's a building,
    not a home" after the read-back made the agent decline an apartment building."""
    from app.lifecycle import sounds_commercial
    from app.tenant import load_agent
    from tests.tool_server.toolkit import TENANT_DIR

    cfg = load_agent(TENANT_DIR)[0]["commercial"]
    assert not sounds_commercial("yes that's right, it's a building not a home so note that", cfg)
    text = assemble.instructions(agent.start()["data"], datetime(2026, 11, 2, 14, 0, tzinfo=UTC))
    assert '"it\'s a building, not a home"' in text and "is still residential" in text


def test_a_shop_building_that_is_not_a_home_stays_commercial():
    """PR #102 review: "building" as an apartment sign mustn't make a business residential."""
    from app.lifecycle import sounds_commercial
    from app.tenant import load_agent
    from tests.tool_server.toolkit import TENANT_DIR

    cfg = load_agent(TENANT_DIR)[0]["commercial"]
    assert sounds_commercial("it's not a home, it's the shop building", cfg)
    assert not sounds_commercial("it's a building not a home, six apartments", cfg)


def test_an_active_member_is_told_to_be_thanked(agent, scenarios):
    """member-books-tune-up: the agent never thanked a member (eval run 20261104-174038)."""
    linda = scenarios["activeMemberWithAdultChild"]
    start = agent.start(linda["phone"])["data"]
    text = assemble.instructions(start, datetime(2026, 11, 2, 14, 0, tzinfo=UTC))
    assert "Thank them for being a member once" in text
    assert "For this caller:" in text


def test_a_caller_with_a_visit_booked_is_moved_not_booked_again(agent, scenarios):
    """known-caller-moves-visit: the agent called book_appointment before rescheduling (eval run
    20261104-174038)."""
    maria = scenarios["repeatCallerOpenJob"]
    notes = assemble.call_notes(agent.start(maria["phone"])["data"])
    assert any("call reschedule_appointment" in n and "never book_appointment" in n for n in notes)


def test_an_unknown_caller_gets_no_caller_notes(agent):
    start = agent.start()["data"]
    assert assemble.call_notes(start) == []
    assert "For this caller:" not in assemble.instructions(start)


def test_an_expired_member_is_not_thanked_as_a_member(agent, scenarios):
    frank = scenarios["expiredMember"]
    notes = assemble.call_notes(agent.start(frank["phone"])["data"])
    assert not any("active" in n for n in notes)


@pytest.mark.parametrize(
    "sentence",
    [
        "The reschedule ran without a confirmation message.",
        "I can't confirm the new time yet.",
        "Let me check the appointment again.",
        "The booking didn't return anything.",
        "Is 142 Main Street still the right address?",
        "And the number ending in 0418 the best to reach you?",
    ],
)
def test_tool_talk_and_confirm_questions_are_never_spoken(sentence):
    """The same test call: tool talk aloud, and a stacked address-and-number check before a move."""
    assert assemble.SpokenReply().sentence(sentence) == ""


@pytest.mark.parametrize(
    "sentence",
    ["What's the best number to reach you?", "What's the address there?",
     "Your tune-up is set for Friday between 12 and 4."],
)
def test_ordinary_questions_and_confirmations_are_kept(sentence):
    assert assemble.SpokenReply().sentence(sentence) == sentence


def test_moving_a_known_callers_own_visit_needs_no_address_check():
    start = {"customers": [{"residential": True, "open_jobs": [
        {"job_id": 7, "window": {"weekday": "Friday", "label": "12 to 4"}}]}]}
    notes = " ".join(assemble.call_notes(start))
    assert "needs no read-back" in notes and "don't ask them to confirm the address" in notes


@pytest.mark.parametrize(
    "sentence",
    [
        "Please stay with your partner and make sure your kid is still on the line with 911.",
        "Keep them sitting up if they're short of breath.",
        "Have them lie down and keep talking to them.",
        "Keep an eye on him until help arrives.",
        "Make sure she's breathing comfortably.",
    ],
)
def test_coaching_on_caring_for_someone_is_never_spoken(sentence):
    """Decision 033; eval run 20261104-232053, asks-agent-to-call-911 #2."""
    assert assemble.SpokenReply().sentence(sentence) == ""


@pytest.mark.parametrize(
    "sentence",
    ["Please call 911 now.", "I'm a virtual assistant, so I can't place that call.",
     "Someone will stay in touch about the visit.", "Keep the thermostat at 68 for now."],
)
def test_the_911_advice_and_ordinary_lines_are_kept(sentence):
    assert assemble.SpokenReply().sentence(sentence) == sentence



def test_a_name_the_caller_just_gave_is_not_said_straight_back():
    """Jackson after live call 2 (issue #149): "didnt like how it said my name right back"."""
    reply = assemble.SpokenReply()
    reply.heard(None, "This is, uh, Pat Rivera.")
    assert reply.text("Got it, Pat. Let me get the new address.", []) == (
        "Got it. Let me get the new address."
    )
    assert reply.text("Hi Pat, what's going on?", []) == "Hi, what's going on?"
    # A name the caller didn't just say is left alone.
    assert reply.text("Thanks, Dana.", []) == "Thanks, Dana."


def test_a_zip_is_said_as_the_zip_code_is_and_its_digits():
    """Jackson after live call 2: "just say the zip code is, not just zip 11201"."""
    reply = assemble.SpokenReply()
    reply.heard(None, "I moved to Hoyt Street.")
    assert reply.text("I found 75 Hoyt Street in Brooklyn, ZIP 11201. Is that the new address?", []) == (
        "I found 75 Hoyt Street in Brooklyn, and the ZIP code is 1 1 2 0 1. Is that the new address?"
    )


def test_an_address_the_caller_said_in_words_is_not_said_back_by_the_model():
    """Replay-2 gate call: the caller said "seventy five Hoyt Street" and "eleven two zero one",
    and the model read both back on top of the server's read-back."""
    reply = assemble.SpokenReply()
    reply.heard(None, "I moved to seventy five Hoyt Street in Brooklyn. Eleven two zero one.")
    assert reply.text("Thanks. I have 75 Hoyt Street, Brooklyn 11201. Is that right?", []) == "Thanks."


def test_a_zip_the_lookup_found_is_never_read_back_by_the_model():
    reply = assemble.SpokenReply()
    reply.heard(None, "It's 75 Hoyt Street.")
    reply.tool_done("find_address", {"ok": True, "data": {"zip": "11201"}})
    assert reply.text("Got it, the zip code is 11201.", []) == ""


@pytest.mark.parametrize(
    "said, heard",
    [
        ("What's the ZIP code 11201?", "What's the ZIP code 1 1 2 0 1?"),
        ("ZIP: 11201 is in our service area.", "ZIP: 1 1 2 0 1 is in our service area."),
        ("I have 75 Hoyt Street, ZIP 11201.", "I have 75 Hoyt Street, and the ZIP code is 1 1 2 0 1."),
    ],
)
def test_only_a_bare_zip_is_reworded_and_other_forms_only_space_the_digits(said, heard):
    """PR #155 re-check: "What's the ZIP code is 1 1 2 0 1?" and "The ZIP code is ... is in"."""
    reply = assemble.SpokenReply()
    reply.heard(None, "I need a tune-up.")
    assert reply.text(said, []) == heard


def test_the_name_is_not_echoed_from_earlier_in_the_call_or_the_account():
    """A scripted call on main after #156: "Thanks, Pat.", "Sounds good, Pat."; and a full name."""
    reply = assemble.SpokenReply()
    reply.names = {"pat", "rivera"}
    reply.heard(None, "I need a tune-up.")
    assert reply.text("Thanks, Pat Rivera.", []) == "Thanks."
    assert reply.text("Sounds good, Pat. What day works?", []) == "Sounds good. What day works?"
    assert reply.text("Sure, Friday.", []) == "Sure, Friday."


def test_the_names_on_the_matched_accounts_come_from_the_call_start():
    start = {"customers": [{"name": "Pat Rivera"}], "preferred_name": "Dr. Patel"}
    assert assemble.call_names(start) == {"pat", "rivera", "dr", "patel"}


def test_no_follow_up_after_the_callers_goodbye():
    """PR #155 re-check: a lone "Sure." after the caller said bye is the end, not a stall."""
    reply = assemble.SpokenReply()
    reply.heard(None, "Okay, thanks, bye.")
    assert reply.dead_end("Sure.", "Sure.", []) is None


def test_no_problem_answers_only_a_thank_you_or_an_apology():
    """Jackson's call on the Retell number: "No problem" to "The what?"."""
    reply = assemble.SpokenReply()
    reply.heard(None, "The what?")
    assert reply.text("No problem. What's the address there?", []) == "What's the address there?"
    assert reply.text("No worries if not.", []) == "No worries if not."
    reply.heard(None, "Okay, thanks.")
    assert reply.text("No problem.", []) == "No problem."


def test_a_lone_no_problem_is_kept_rather_than_left_silent():
    """PR #158 review: "Is it ok if I'm not home?" got "No problem." and the filter blanked it."""
    reply = assemble.SpokenReply()
    reply.heard(None, "Is it ok if I'm not home?")
    assert reply.text("No problem.", []) == "No problem."


def test_the_known_facts_are_passed_on_only_when_they_change():
    """Realism suite on #155: the "Already known" note on every caller line changed the context
    after LiveKit's early model request had started, so every turn made one request more."""
    known = assemble.KnownFacts()
    assert known.fresh({}) is None
    assert known.fresh({"system": "furnace"}) == {"system": "furnace"}
    assert known.fresh({"system": "furnace"}) is None
    assert known.fresh({"system": "furnace", "ZIP said": "11201"}) == {
        "system": "furnace", "ZIP said": "11201"
    }
    assert known.fresh({"system": "furnace", "ZIP said": "11201"}) is None


def test_a_reply_that_offers_choices_ends_with_a_question_in_both_profiles():
    """Live-like rig run on 4b160eb (issue #150): windows offered with no question, then 22 s of
    silence while the caller waited for one."""
    for name in ("identity.md", "stripped.md"):
        text = (assemble.PROMPTS / name).read_text()
        assert "ends with a direct question" in text, name
