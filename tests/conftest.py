import pytest


@pytest.fixture(autouse=True)
def no_embedding_calls(monkeypatch):
    """lookup_hvac_info asks OpenAI to embed the question when the tenant has a built index
    (app/knowledge_search.py); tests use the keyword search unless one sets the switch itself."""
    monkeypatch.setenv("KNOWLEDGE_SEARCH", "keyword")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
