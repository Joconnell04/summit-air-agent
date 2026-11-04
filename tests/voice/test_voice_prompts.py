"""The prompt files and tool list both voice layers load (prompts/)."""

import json
import re
from datetime import UTC, datetime

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
    assert "call `end_call` without saying anything first" in text
    assert "the technician prices the work in person" in text
    assert "Never say whether that person is a customer" in text
    assert "to a renter only, never to an owner or landlord" in text
    assert "A yes still counts when the caller says more in the same turn" in text
    assert 'Give no medical advice, not even "keep them upright"' in text
    assert "if they ask what to do for the person, the 911 operator will tell them" in text
    assert "never an owner or landlord: one neutral line" in text
    assert "Once someone is calling 911, carry on to booking the visit as usual." in text
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
    assert "spell it" in text and "`spelled`" in text
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
