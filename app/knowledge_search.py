"""The search behind lookup_hvac_info: passages ranked by meaning, with the keyword search as the
fallback (app/hvac_info.py).

KNOWLEDGE_SEARCH=keyword|embedding picks one. Unset, embedding is used when the tenant has a built
index (knowledge/lookup-index.json, written by scripts/build_knowledge_index.py), else keyword.
Embedding asks OpenAI to embed the question, ranks the index's passages by cosine, adds a small
bonus for words the question shares with the passage, and keeps the top one to three above a
threshold. A missing key, a slow or failed request, or an index that no longer matches the pages
falls back to the keyword search, so a call never waits on this longer than timeout_s.
Rolling back is KNOWLEDGE_SEARCH=keyword.

Nothing here puts framing words in a fact: a fact is the page's own sentence. The file it came from
is returned apart, for the call log only."""

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from app import hvac_info

log = logging.getLogger("summit.knowledge_search")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
INDEX_FILE = "lookup-index.json"
EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


@dataclass(frozen=True)
class Fact:
    text: str
    file: str


def settings() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())["knowledge"]


def digest(documents: dict[str, str]) -> str:
    """Changes whenever a page's passages do, so an index built from older pages is noticed."""
    parts = [f"{name}\n{h}\n{t}" for name, doc in sorted(documents.items()) for h, t in hvac_info.passages(doc)]
    return hashlib.sha256("\n\n".join(parts).encode()).hexdigest()


def embed(texts: list[str], config: dict, timeout_s: float | None = None) -> list[list[float]]:
    """One vector per text. Raises on a missing key, a slow request or any error status."""
    key = os.environ.get(config.get("key_env", "OPENAI_API_KEY"))
    if not key:
        raise RuntimeError("no OpenAI key")
    response = httpx.post(
        EMBEDDINGS_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": config["model"], "input": texts, "dimensions": config["dimensions"]},
        timeout=timeout_s or config["timeout_s"],
    )
    response.raise_for_status()
    return [row["embedding"] for row in sorted(response.json()["data"], key=lambda r: r["index"])]


def build_index(documents: dict[str, str], config: dict | None = None) -> dict:
    config = config or settings()
    rows = [(name, h, t) for name, doc in sorted(documents.items()) for h, t in hvac_info.passages(doc)]
    vectors = embed([hvac_info.fact(h, t) for _, h, t in rows], config, timeout_s=60)
    return {
        "model": config["model"],
        "dimensions": config["dimensions"],
        "digest": digest(documents),
        "passages": [
            {"file": f"{name}.md", "heading": h, "text": t, "vector": [round(x, 5) for x in v]}
            for (name, h, t), v in zip(rows, vectors, strict=True)
        ],
    }


def load_index(path: Path, documents: dict[str, str]) -> dict | None:
    """The built index, or None when there is none or it was built from other pages."""
    if not path.exists():
        return None
    index = json.loads(path.read_text())
    if index.get("digest") != digest(documents):
        log.warning("%s is out of date; run scripts/build_knowledge_index.py", path)
        return None
    return index


def cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


def by_meaning(question: str, index: dict, synonyms: dict[str, str], config: dict) -> list[Fact]:
    rows = index["passages"]
    asked = embed([question], config)[0]
    pool = [
        hvac_info.Item(r["heading"], r["text"], hvac_info.terms(r["text"], synonyms),
                       hvac_info.terms(r["heading"], synonyms), r["file"])
        for r in rows
    ]
    words = hvac_info.scores(question, pool, synonyms)
    top_words = max(words, default=0.0)
    cosines = [cosine(asked, r["vector"]) for r in rows]
    ranked = sorted(
        (c + config["keyword_weight"] * (w / top_words if top_words else 0.0), c, n)
        for n, (c, w) in enumerate(zip(cosines, words, strict=True))
    )[::-1]
    if not ranked or ranked[0][1] < config["min_cosine"]:
        return []
    kept = [
        n for _, c, n in ranked[: config["max_passages"]]
        if c >= config["min_cosine"] and c >= ranked[0][1] - config["close_cosine"]
    ]
    return [Fact(hvac_info.fact(rows[n]["heading"], rows[n]["text"]), rows[n]["file"]) for n in kept]


def by_words(question: str, documents: dict[str, str], synonyms: dict[str, str]) -> list[Fact]:
    return [Fact(hvac_info.fact(i.heading, i.text), i.file) for i in hvac_info.find(question, documents, synonyms)]


def search(
    question: str, documents: dict[str, str], synonyms: dict[str, str], folder: Path
) -> tuple[list[Fact], str]:
    """The facts for a question and which search found them ("embedding" or "keyword")."""
    index_path = folder / "knowledge" / INDEX_FILE
    chosen = os.environ.get("KNOWLEDGE_SEARCH") or ("embedding" if index_path.exists() else "keyword")
    if chosen == "embedding":
        index = load_index(index_path, documents)
        if index:
            try:
                return by_meaning(question, index, synonyms, settings()), "embedding"
            except Exception as error:  # noqa: BLE001 - any failure means the keyword search answers
                log.warning("embedding search failed, using keywords: %s", error)
    return by_words(question, documents, synonyms), "keyword"
