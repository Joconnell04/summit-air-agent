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
    assert "Stay warm out there" in text


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
