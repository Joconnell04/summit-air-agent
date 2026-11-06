"""lookup_hvac_info: the part of the tenant's equipment basics that answers a caller's question.

The document (knowledge/hvac-basics.md, listed under `lookup_knowledge` in agent.yaml) is split into
items: each bullet, and each paragraph under a `##` heading. A question is scored against them by
plain word overlap, after a light stem and the trade's list of words callers use for the same thing
(`lookup_synonyms` in trades/<trade>/agent.yaml).
No embeddings: the document is a page long, and an answer can be traced to the words that matched.

The document has no repair steps and no prices, so nothing returned can be one.
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


def items(document: str, synonyms: dict[str, str]) -> list[Item]:
    """Each bullet, and each paragraph, with the `##` heading it sits under."""
    found, heading = [], ""
    for block in re.split(r"\n\s*\n", document):
        block = block.strip()
        if block.startswith("# "):
            continue
        if block.startswith("## "):
            heading, _, block = block[3:].partition("\n")
            block = block.strip()
        for part in re.split(r"\n(?=- )", block):
            text = " ".join(part.removeprefix("- ").split())
            if text:
                found.append(Item(heading, text, terms(text, synonyms), terms(heading, synonyms)))
    return found


def answer(question: str, document: str, synonyms: dict[str, str] | None = None) -> list[str]:
    """The best one or two items for the question, or none when nothing shares enough with it.
    Rare words count for more, so "filter" outweighs "heat"."""
    synonyms = synonyms or {}
    pool = items(document, synonyms)
    asked = terms(question, synonyms)
    if not pool or not asked:
        return []
    weight = {
        t: math.log(1 + len(pool) / sum(t in i.terms | i.heading_terms for i in pool))
        for t in asked
        if any(t in i.terms | i.heading_terms for i in pool)
    }

    def score(item: Item) -> float:
        body = sum(weight[t] for t in asked & item.terms)
        return body + HEADING_WEIGHT * sum(weight[t] for t in (asked & item.heading_terms) - item.terms)

    scored = sorted(((score(i), n, i) for n, i in enumerate(pool)), key=lambda s: (-s[0], s[1]))
    best = scored[0][0]
    if best < MIN_SCORE:
        return []
    kept = [i for score, _, i in scored[:MAX_FACTS] if score >= best * SECOND_SHARE]
    return [f"{i.heading}: {i.text}" if i.heading else i.text for i in kept]
