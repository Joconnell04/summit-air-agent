"""Card numbers and billing on the phone (DECISIONS.md 080).

Card digits are taken out of every caller line and tool argument before anything stores them: the
turns table, the tool log and its JSON lines, job notes, flags and the technician summary all see
the marker instead. It is the one exception to decision 064 (the bench shows everything as
recorded): card digits are never recorded in the first place.

A run of digits is taken out when it is grouped the way a card is read (fours, Amex's 4-6-5, or one
digit at a time) and is either a whole card (13 to 19 digits that pass the Luhn check, or 15 or 16
that don't, a likely speech-to-text slip) or the start of one (8 digits or more; one digit at a
time, a run the length of a phone number stays). A phone number, a phone with a ZIP after it
(3-3-4-5), a house number, a date or an amount is grouped like none of these, so it stays. Right
after a card word ("my card number is", "the code on the back"), the digits that follow are taken
out whatever their length; further along the line only a card-like run is. On the caller's line
after one that had digits taken out, a line that is little more than digits loses them too, so a
card split at a turn boundary is never stored.

What the agent says when a caller offers a card, and the billing callback, are the server's lines
(app/lifecycle.py).
"""

import re
from typing import Any

from .text import has_phrase, words

MARKER = "[card number removed]"
WHOLE_CARD = range(13, 20)
SLIP_LENGTHS = {15, 16}
START_DIGITS = 8
PHONE_LENGTHS = {10, 11}
KEEP_LENGTHS = {5, 10, 11}
CONTINUATION_WORDS = 3
DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
# Digits in groups joined by a single space, dash, dot or comma ("4111 1111 1111 1111"), or digits
# said as words ("four one one one").
RUN = re.compile(
    rf"(?<![\w/])\d+(?:[ \t.,-]\d+)*(?![\w/])|\b{_DIGIT_WORD}(?:[\s,-]+{_DIGIT_WORD})*\b",
    re.IGNORECASE,
)
# A card word, the few words that can sit between it and the digits, and the digits right after:
# a group, a date like 08/28, or digit words.
_CUE = (
    r"\b(?:(?:credit|debit)\s+card(?:\s+number)?|card\s+(?:number|no\.?|\#)|card\s+is|"
    r"number\s+on\s+the\s+card|visa|mastercard|master\s+card|amex|american\s+express|"
    r"discover\s+card|expiration\s+date|cvv|cvc|security\s+code|code\s+on\s+the\s+back)\b"
)
_GAP = r"(?:[\s,:#]+(?:is|was|its|it's|number|no|ending\s+in)?)*[\s,:#]*"
CUED = re.compile(
    rf"({_CUE}{_GAP})(\d[\d /.-]*\d|\d|{_DIGIT_WORD}(?:[\s,-]+{_DIGIT_WORD})*\b)",
    re.IGNORECASE,
)


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _groups(run: str) -> list[str]:
    """The digit groups of a run: separated digits, or one group per spoken digit."""
    spoken = re.findall(r"[a-z]+", run, re.IGNORECASE)
    if spoken:
        return [DIGIT_WORDS[w.lower()] for w in spoken]
    return [g for g in re.split(r"[ \t.,-]", run) if g]


def _card_like(groups: list[str]) -> bool:
    """A whole card, or the start of one, by how the digits are grouped (see the module note)."""
    digits = "".join(groups)
    sizes = [len(g) for g in groups]
    singles = len(sizes) > 1 and all(size == 1 for size in sizes)
    fours = len(sizes) > 1 and all(size == 4 for size in sizes[:-1]) and 1 <= sizes[-1] <= 4
    amex = sizes in ([4, 6, 5], [4, 6, 4])
    whole = len(digits) in WHOLE_CARD and (fours or singles or amex or len(sizes) == 1)
    if whole and (luhn(digits) or len(digits) in SLIP_LENGTHS):
        return True
    if singles:
        return len(digits) >= START_DIGITS and len(digits) not in PHONE_LENGTHS
    return fours and len(digits) >= START_DIGITS


def _runs(text: str) -> str:
    def replace(match: re.Match) -> str:
        return MARKER if _card_like(_groups(match.group(0))) else match.group(0)

    return RUN.sub(replace, text)


def _after_cues(text: str) -> str:
    """The digits right after a card word, whatever their length: "card number is 4111", "the
    code on the back is 123", "expiration date 08/28"."""
    return CUED.sub(lambda m: m.group(1) + MARKER, text)


def _continuation(text: str) -> str:
    """The rest of a card the caller kept reading on the next line, if the line is mostly digits.
    A run the length of a phone number or a ZIP stays."""
    other = re.findall(r"[a-z']+", RUN.sub(" ", text), re.IGNORECASE)
    if len(other) > CONTINUATION_WORDS:
        return text

    def replace(match: re.Match) -> str:
        run = match.group(0)
        digits = "".join(_groups(run))
        # A lone "one" ("one more sec") is a word, not part of a card.
        return run if len(digits) < 2 or len(digits) in KEEP_LENGTHS else MARKER

    return RUN.sub(replace, text)


def redact(text: str, continuing: bool = False) -> str:
    """The text with card digits replaced by the marker. Repeated markers are joined into one.
    `continuing`: the caller's previous line had card digits taken out."""
    if not text:
        return text
    cleaned = _after_cues(_runs(text))
    if continuing:
        cleaned = _continuation(cleaned)
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
