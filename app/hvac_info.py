"""lookup_hvac_info, keyword search: the part of the tenant's equipment pages that answers a caller's
question.

The pages (knowledge/*.md listed under `lookup_knowledge` in agent.yaml) are split into items: each
bullet, and each paragraph under a `##` heading; a `## Sources` section is notes for the maintainer
and is left out. A question is scored against them by plain word overlap, after a light stem and the
trade's list of words callers use for the same thing (`lookup_synonyms` in trades/<trade>/agent.yaml).
This is the search that runs when embeddings are off or fail (app/knowledge_search.py).

The pages have no repair steps and no prices, so nothing returned can be one.
"""

import math
import re
from dataclasses import dataclass

STOP = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for", "with", "from",
    "by", "about", "as", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
    "these", "those", "i", "im", "me", "my", "we", "our", "you", "your", "they", "their", "he",
    "she", "what", "whats", "why", "how", "when", "where", "which", "who", "do", "does", "did",
    "can", "could", "should", "would", "will", "there", "theres", "so", "just", "like", "mean",
    "means", "normal", "some", "any", "all", "get", "got", "have", "has", "had", "not", "no", "yes",
    "know", "tell", "much", "many", "often", "lot", "really", "very", "kind", "sort", "thing",
    "things", "one", "up", "out", "off", "too", "also", "usually", "home", "house", "long",
    "send", "someone", "today",
}
# A question has to share this much with an item; a second item comes back only when it scores at
# least this share of the best.
MIN_SCORE = 1.5
SECOND_SHARE = 0.75
# A word that only the heading shares counts for this much of one in the item's own text.
HEADING_WEIGHT = 0.5
MAX_FACTS = 2


@dataclass(frozen=True)
class Item:
    heading: str
    text: str
    terms: frozenset[str]
    heading_terms: frozenset[str]
    file: str = ""


def _stem(word: str) -> str:
    """Enough to make "changed", "changes" and "change" one word."""
    for suffix in ("ing", "ers", "er", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def terms(text: str, synonyms: dict[str, str]) -> frozenset[str]:
    plain = " " + " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("'", "")).split()) + " "
    for said, means in synonyms.items():
        plain = plain.replace(f" {said} ", f" {means} ")
    return frozenset(_stem(w) for w in plain.split() if w not in STOP and not w.isdigit())


def passages(document: str) -> list[tuple[str, str]]:
    """Each bullet, and each paragraph, with the `##` heading it sits under."""
    found, heading = [], ""
    for block in re.split(r"\n\s*\n", document):
        block = block.strip()
        if block.startswith("# "):
            continue
        if block.startswith("## "):
            heading, _, block = block[3:].partition("\n")
            block = block.strip()
        if heading == "Sources":
            continue
        for part in re.split(r"\n(?=- )", block):
            text = " ".join(part.removeprefix("- ").split())
            if text:
                found.append((heading, text))
    return found


def items(document: str, synonyms: dict[str, str], file: str = "") -> list[Item]:
    return [
        Item(heading, text, terms(text, synonyms), terms(heading, synonyms), file)
        for heading, text in passages(document)
    ]


def fact(heading: str, text: str) -> str:
    return f"{heading}: {text}" if heading else text


def scores(question: str, pool: list[Item], synonyms: dict[str, str]) -> list[float]:
    """One score per item. Rare words count for more, so "filter" outweighs "heat"."""
    asked = terms(question, synonyms)
    if not pool or not asked:
        return [0.0] * len(pool)
    weight = {
        t: math.log(1 + len(pool) / sum(t in i.terms | i.heading_terms for i in pool))
        for t in asked
        if any(t in i.terms | i.heading_terms for i in pool)
    }

    def score(item: Item) -> float:
        body = sum(weight[t] for t in asked & item.terms)
        return body + HEADING_WEIGHT * sum(weight[t] for t in (asked & item.heading_terms) - item.terms)

    return [score(i) for i in pool]


def best(pool: list[Item], scored: list[float]) -> list[Item]:
    """The best one or two items, or none when nothing shares enough with the question."""
    order = sorted(range(len(pool)), key=lambda n: (-scored[n], n))
    if not order or scored[order[0]] < MIN_SCORE:
        return []
    top = scored[order[0]]
    return [pool[n] for n in order[:MAX_FACTS] if scored[n] >= top * SECOND_SHARE]


def answer(question: str, document: str, synonyms: dict[str, str] | None = None) -> list[str]:
    pool = items(document, synonyms or {})
    return [fact(i.heading, i.text) for i in best(pool, scores(question, pool, synonyms or {}))]


def find(question: str, documents: dict[str, str], synonyms: dict[str, str]) -> list[Item]:
    """The same search over every page at once, so the best two come from anywhere."""
    pool = [i for name, text in documents.items() for i in items(text, synonyms, f"{name}.md")]
    return best(pool, scores(question, pool, synonyms))
