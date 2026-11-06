"""lookup_hvac_info: the equipment basics that answer a caller's question, as facts with no say."""

import pytest
from toolkit import TENANT_DIR, UNKNOWN

from app import hvac_info
from app.tenant import Tenant

DOCUMENT = (TENANT_DIR / "knowledge" / "hvac-basics.md").read_text()


def ask(agent, question: str) -> dict:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, question)
    return agent.tool("lookup_hvac_info", call, question=question)


def test_a_banging_radiator_gets_the_steam_pipes_fact(agent):
    found = ask(agent, "Why is my radiator banging?")
    assert found["ok"] and found["say"] == ""
    facts = found["data"]["facts"]
    assert len(facts) in (1, 2)
    assert facts[0].startswith("Common sounds and symptoms, in plain words: Banging, knocking")


def test_short_cycling_gets_its_fact(agent):
    facts = ask(agent, "My furnace keeps turning on and off every few minutes. What's that?")[
        "data"
    ]["facts"]
    assert "short cycling" in facts[0]


def test_a_filter_change_gets_the_filter_fact(agent):
    facts = ask(agent, "How often should I change my filter?")["data"]["facts"]
    assert facts[0].startswith("Filters and thermostats: Furnace and central-air systems have an air filter")


@pytest.mark.parametrize("question", [
    "What are your hours on Saturday?",
    "Do you guys do plumbing?",
    "Can you send someone today?",
    "How much does a repair cost?",
])
def test_an_unrelated_question_gets_no_facts(agent, question):
    found = ask(agent, question)
    assert found["ok"] and found["data"]["facts"] == [] and found["say"] == ""


@pytest.mark.parametrize("question,starts", [
    ("My pipes are knocking", "Common sounds and symptoms, in plain words: Banging"),
    ("There's ice on my air conditioner", "Common sounds and symptoms, in plain words: Ice"),
    ("My AC is blowing warm air", "Common sounds and symptoms, in plain words: Air that blows"),
    ("How long does a boiler last?", "Equipment age:"),
])
def test_callers_words_for_the_same_thing_find_it(question, starts):
    assert hvac_info.answer(question, DOCUMENT)[0].startswith(starts)


def test_the_document_has_no_repair_steps_or_prices():
    """The tool never returns how to fix anything or what it costs; the document holds neither."""
    lowered = DOCUMENT.lower()
    assert "$" not in DOCUMENT
    for word in ("price", "cost", "relight", "reset the", "bleed", "step 1", "unscrew"):
        assert word not in lowered, word


def test_the_lookup_document_stays_out_of_the_prompt_knowledge():
    tenant = Tenant.load(TENANT_DIR)
    assert "hvac-basics" in tenant.lookup_knowledge
    assert "hvac-basics" not in tenant.knowledge
