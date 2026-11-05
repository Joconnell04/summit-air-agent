"""The native Retell agent (voice/retell_native): a one-page prompt with the safety lines word for
word, facts in a knowledge base with no prices, scoped functions, and Retell's own End Call."""

from pathlib import Path

import yaml

from prompts import assemble
from voice import names as local_names
from voice.retell_native import build

TENANT = Path(__file__).resolve().parents[2] / "tenants" / "summit-air"
BASE = "https://bridge.example.com"


def specs():
    return assemble.tool_specs(assemble.job_types(TENANT))


def test_the_prompt_has_every_safety_line_word_for_word_and_the_calls_context():
    agent = yaml.safe_load((TENANT / "agent.yaml").read_text())
    page = build.page(TENANT)
    for spec in agent["safety"]["kinds"].values():
        assert " ".join(spec["lines"].split()) in page
    assert page.rstrip().endswith(build.CALL_CONTEXT)
    assert build.END_CALL_LINE not in page and "{company}" not in page


def test_the_knowledge_base_has_all_five_boroughs_and_no_prices():
    texts = build.knowledge(TENANT)
    joined = "\n".join(t["text"] for t in texts)
    for borough in ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"):
        assert borough in joined
    assert "$" not in joined and "config.yaml" not in joined
    assert "8 AM to 8 PM" in joined
    # Nothing in both: the hours live only in the knowledge base's own text.
    assert sum("8 a.m. to 8 p.m." in t["text"] for t in texts) == 0


def test_the_functions_are_scoped_and_end_call_is_retells_own():
    tools = build.functions(specs(), BASE, "+15555550100")
    names = [t["name"] for t in tools]
    assert names[:-2] == list(build.FUNCTIONS)
    assert "add_note" not in names and "mark_do_not_call" not in names
    end, transfer = tools[-2], tools[-1]
    assert end == {**end, "type": "end_call", "speak_during_execution": False}
    assert transfer["type"] == "transfer_call"
    assert transfer["transfer_destination"] == {"type": "predefined", "number": "+15555550100"}
    slow = {t["name"] for t in tools if t.get("speak_during_execution") and t["type"] == "custom"}
    assert {"find_slots", "book_appointment", "find_address"} <= slow
    assert all(t["url"].startswith(f"{BASE}/retell/tools/") for t in tools if t["type"] == "custom")


def test_the_agent_keeps_the_tested_dials_and_adds_the_handbook():
    body = build.agent_body("llm_1", BASE, "voice_1", "sonic-3", local_names.load(TENANT))
    assert body["agent_name"] == build.AGENT_NAME
    assert body["stt_mode"] == "accurate" and body["responsiveness"] == 1.0
    assert body["enable_backchannel"] is True and body["reminder_trigger_ms"] == 6000
    assert body["handbook_config"]["nato_phonetic_alphabet"] is False
    assert body["handbook_config"]["echo_verification"] is True
    assert body["contact_memory_config"] == {"enable_read": False, "enable_update": False}
    assert body["begin_message_delay_ms"] == 0
    houston = [d for d in body["pronunciation_dictionary"] if d["word"] == "Houston Street"]
    assert houston and houston[0]["phoneme"].startswith("ˈhaʊ")
    assert {"Manhattan", "Bronx", "radiator", "boiler", "carbon monoxide"} <= set(body["boosted_keywords"])
