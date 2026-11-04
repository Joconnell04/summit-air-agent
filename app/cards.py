"""Card numbers and billing on the phone (DECISIONS.md 080).

Card digits are taken out of every caller line and tool argument before anything stores them: the
turns table, the tool log and its JSON lines, job notes, flags and the technician summary all see
the marker instead. It is the one exception to decision 064 (the bench shows everything as
recorded): card digits are never recorded in the first place.

A run of digits counts as a card when it is 13 to 19 digits, passes the Luhn check, and is grouped
the way a card is read (one block, fours, Amex's 4-6-5, or one digit at a time). A phone number with
a ZIP after it (3-3-4-5) is grouped like neither, so it stays. After a card word ("my card number
is", "the code on the back"), the digits that follow in the line are taken out whatever they are,
up to a word that starts something else ("phone", "zip", "address").

What the agent says when a caller offers a card, and the billing callback, are the server's lines
(app/lifecycle.py).
"""

import re
from typing import Any

from .text import has_phrase, words

MARKER = "[card number removed]"
CARD_DIGITS = range(13, 20)
DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
# Groups of digits joined by a single space, dash, dot or comma: "4111 1111 1111 1111".
DIGIT_RUN = re.compile(r"(?<![\w/])\d+(?:[ \t.,-]\d+)*(?![\w/])")
# Digits said as words, one at a time: "four one one one ...".
WORD_RUN = re.compile(rf"\b{_DIGIT_WORD}(?:[\s,-]+{_DIGIT_WORD})+\b", re.IGNORECASE)
# Words that say card digits follow. Matched on the line as written, case-insensitively.
CARD_CUES = re.compile(
    r"\b(?:(?:credit|debit)\s+card(?:\s+number)?|card\s+(?:number|no\.?|#)|card\s+is|"
    r"number\s+on\s+the\s+card|visa|mastercard|master\s+card|amex|american\s+express|discover\s+card|"
    r"expiration\s+date|cvv|cvc|security\s+code|code\s+on\s+the\s+back)\b",
    re.IGNORECASE,
)
# Words that end what a card cue covers: the caller has moved on to something else.
CUE_STOPS = re.compile(
    r"\b(?:phone|cell|call\s+me|zip|zip\s+code|address|street|avenue|apartment|apt|unit|"
    r"buzzer|gate)\b",
    re.IGNORECASE,
)
# Digits after a cue, in any form: a group, a date like 08/28, or digit words.
CUED_DIGITS = re.compile(
    rf"\d[\d /.,-]*\d|\d|\b{_DIGIT_WORD}(?:[\s,-]+{_DIGIT_WORD})*\b", re.IGNORECASE
)


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card_grouping(groups: list[str]) -> bool:
    """One block, fours with a shorter last group, Amex's 4-6-5 (or 4-6-4), or one digit at a time."""
    sizes = [len(g) for g in groups]
    if len(sizes) == 1 or all(s == 1 for s in sizes):
        return True
    if sizes in ([4, 6, 5], [4, 6, 4]):
        return True
    return all(s == 4 for s in sizes[:-1]) and 1 <= sizes[-1] <= 4


def _is_card(groups: list[str]) -> bool:
    digits = "".join(groups)
    return len(digits) in CARD_DIGITS and _card_grouping(groups) and luhn(digits)


def _digit_runs(text: str) -> str:
    def replace(match: re.Match) -> str:
        groups = re.split(r"[ \t.,-]", match.group(0))
        return MARKER if _is_card(groups) else match.group(0)

    return DIGIT_RUN.sub(replace, text)


def _word_runs(text: str) -> str:
    def replace(match: re.Match) -> str:
        found = re.findall(r"[a-z]+", match.group(0), re.IGNORECASE)
        groups = [DIGIT_WORDS[w.lower()] for w in found]
        return MARKER if _is_card(groups) else match.group(0)

    return WORD_RUN.sub(replace, text)


def _after_cues(text: str) -> str:
    """Every digit after a card cue in the line, up to a word that starts something else."""
    out, at = [], 0
    for cue in CARD_CUES.finditer(text):
        if cue.start() < at:
            continue
        stop = CUE_STOPS.search(text, cue.end())
        end = stop.start() if stop else len(text)
        span = CUED_DIGITS.sub(MARKER, text[cue.end() : end])
        out.append(text[at : cue.end()] + span)
        at = end
    out.append(text[at:])
    return "".join(out)


def redact(text: str) -> str:
    """The text with card digits replaced by the marker. Repeated markers are joined into one."""
    if not text:
        return text
    cleaned = _after_cues(_word_runs(_digit_runs(text)))
    return re.sub(rf"{re.escape(MARKER)}(?:[\s,/-]*{re.escape(MARKER)})+", MARKER, cleaned)


def scrub(value: Any) -> Any:
    """Any JSON-like value with card digits taken out of every string in it."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def removed(text: str) -> bool:
    """The line had card digits taken out of it."""
    return MARKER in text


def offers_card(text: str, cfg: dict) -> bool:
    """The caller reads a card out, or asks to pay by card on this call. `cfg` is the trade's
    `payments` block. A line that only mentions a card ("I paid by card last time") is neither."""
    if removed(text):
        return True
    said = words(text)
    return any(has_phrase(said, p) for p in cfg.get("card_phrases", []))


def billing_dispute(text: str, cfg: dict) -> bool:
    """The caller disputes a charge or a bill: "I was charged twice", "my bill is wrong"."""
    said = words(text)
    return any(has_phrase(said, p) for p in cfg.get("billing_phrases", []))
