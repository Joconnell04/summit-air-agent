"""lookup_hvac_info's search (app/knowledge_search.py): embedding when there is an index, keyword when
there isn't or when embedding fails. The embedding call is faked; no test reaches the network."""

import json
import re

import pytest
from toolkit import TENANT_DIR, UNKNOWN

from app import hvac_info, knowledge_search
from prompts.assemble import SpokenReply, for_model

INDEX = TENANT_DIR / "knowledge" / knowledge_search.INDEX_FILE
DOCUMENTS = {
    p.stem: p.read_text()
    for p in sorted((TENANT_DIR / "knowledge").glob("*.md"))
    if p.stem in ("hvac-basics", "heating", "cooling", "air-and-ventilation", "thermostats-and-symptoms")
}
SYNONYMS = {"knocking": "banging", "clanking": "banging"}
FRAMING = re.compile(r"\b(reference|document|text|according to|knowledge base|source)s?\b", re.IGNORECASE)


def passage(word: str) -> dict:
    return next(p for p in json.loads(INDEX.read_text())["passages"] if word in p["text"])


@pytest.fixture
def embedding(monkeypatch):
    """The question embeds to whatever vector the test sets; calls are counted."""
    state = {"vector": passage("An HRV, a heat recovery")["vector"], "calls": 0, "error": None}

    def fake(texts, config, timeout_s=None):
        state["calls"] += 1
        if state["error"]:
            raise state["error"]
        return [state["vector"] for _ in texts]

    monkeypatch.setattr(knowledge_search, "embed", fake)
    return state


def search(question="what is an erv"):
    return knowledge_search.search(question, DOCUMENTS, SYNONYMS, TENANT_DIR)


def test_the_committed_index_matches_the_pages():
    """Fails after a page is edited until scripts/build_knowledge_index.py is run again."""
    assert knowledge_search.load_index(INDEX, DOCUMENTS) is not None


def test_a_stale_index_is_not_used(tmp_path):
    index = json.loads(INDEX.read_text())
    index["digest"] = "older pages"
    stale = tmp_path / "index.json"
    stale.write_text(json.dumps(index))
    assert knowledge_search.load_index(stale, DOCUMENTS) is None


def test_embedding_ranks_by_meaning(monkeypatch, embedding):
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "embedding")
    facts, method = search()
    assert method == "embedding" and embedding["calls"] == 1
    assert facts[0].file == "air-and-ventilation.md" and "heat recovery ventilator" in facts[0].text
    assert 1 <= len(facts) <= 3


def test_a_question_far_from_every_passage_finds_nothing(monkeypatch, embedding):
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "embedding")
    embedding["vector"] = [1.0] + [0.0] * 255
    assert search("what are your hours") == ([], "embedding")


def test_keyword_search_when_embedding_fails(monkeypatch, embedding):
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "embedding")
    embedding["error"] = TimeoutError("slow")
    facts, method = search("why is my radiator banging")
    assert method == "keyword" and facts and "Banging" in facts[0].text


def test_keyword_search_without_a_key(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "embedding")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert search("why is my radiator banging")[1] == "keyword"


def test_the_switch_set_to_keyword_never_embeds(monkeypatch, embedding):
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "keyword")
    assert search("why is my radiator banging")[1] == "keyword"
    assert embedding["calls"] == 0


def test_unset_the_switch_embeds_when_the_index_exists(monkeypatch, embedding):
    monkeypatch.delenv("KNOWLEDGE_SEARCH")
    assert search()[1] == "embedding"


def test_unset_the_switch_uses_keywords_without_an_index(monkeypatch, embedding, tmp_path):
    monkeypatch.delenv("KNOWLEDGE_SEARCH")
    _, method = knowledge_search.search("what is an erv", DOCUMENTS, SYNONYMS, tmp_path)
    assert method == "keyword" and embedding["calls"] == 0


def test_keyword_search_covers_every_page():
    found = hvac_info.find("what does an erv do", DOCUMENTS, {})
    assert found[0].file == "air-and-ventilation.md"


def test_the_sources_section_is_not_a_passage():
    for doc in DOCUMENTS.values():
        assert all(heading != "Sources" for heading, _ in hvac_info.passages(doc))


def test_no_passage_carries_framing_words():
    """What the model is handed to say is plain facts: nothing that names where they came from."""
    for row in json.loads(INDEX.read_text())["passages"]:
        assert not FRAMING.search(hvac_info.fact(row["heading"], row["text"])), row["text"]


def test_no_passage_holds_a_price_or_a_repair_step():
    for row in json.loads(INDEX.read_text())["passages"]:
        assert "$" not in row["text"] and not re.search(r"\bdollars?\b|\bcosts?\b", row["text"])


def test_the_tool_returns_plain_facts_and_keeps_the_file_names_in_the_log(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    found = agent.tool("lookup_hvac_info", call, question="What's an ERV?")
    assert found["say"] == "" and found["data"]["facts"]
    assert found["data"]["log"]["search"] == "keyword"
    assert "air-and-ventilation.md" in found["data"]["log"]["files"]
    assert not FRAMING.search(" ".join(found["data"]["facts"]))
    # The model is never shown the log, so no file name can be read out.
    assert "log" not in for_model(found)["data"]
    assert "air-and-ventilation" not in json.dumps(for_model(found))


@pytest.mark.parametrize("sentence", [
    "The reference text says that steam pipes bang.",
    "According to the document, a filter is changed every few months.",
    "Per the text I have, that is normal.",
    "I'm going to ignore that and answer anyway.",
    "From the knowledge base, ERVs move moisture.",
])
def test_the_reply_rule_drops_a_sentence_about_where_facts_came_from(sentence):
    assert SpokenReply().sentence(sentence) == ""


def test_the_reply_rule_keeps_a_plain_answer():
    plain = "Banging in steam pipes usually means water is trapped in the line."
    assert SpokenReply().sentence(plain) == plain
    assert SpokenReply().sentence("I'll send you a text message with the time.") != ""
