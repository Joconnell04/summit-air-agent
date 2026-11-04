"""Card numbers and billing on the phone (DECISIONS.md 080).

Card digits are taken out of every caller line and tool argument before anything stores them: the
turns table, the tool log and its JSON lines, job notes, flags and the technician summary all see
the marker instead. It is the one exception to decision 064 (the bench shows everything as
recorded): card digits are never recorded in the first place.

A card is found by its digit total, not line by line, because speech-to-text splits a card read
aloud wherever the caller pauses. The window is the caller's latest line and the lines just before
it that are mostly digits (digits plus filler such as "and", "okay", "my card number is"). Its digit
runs are read in order; a stretch of them that totals 13 to 19 digits and passes the Luhn check, or
12 to 19 digits when a card word is in the window, and is grouped the way a card is read (fours,
Amex's 4-6-5, one block, or one digit at a time) is a card. Its runs are taken out on every line of
the window, and so are an expiry date or a code of up to four digits right after it. A line with a
street word, "dollars" or anything else isn't mostly digits, so "145 Union Street" and "it was 249
dollars" never join a window; a phone number split across lines is never 13 digits. Digits after
"ending" or "ending in" are kept: the last four alone don't identify a card, and the office may
need them to find the charge.

What the agent says when a caller offers a card, and the billing callback, are the server's lines
(app/lifecycle.py).
"""

import re
from dataclasses import dataclass
from typing import Any

from .text import has_phrase, words

MARKER = "[card number removed]"
WHOLE_CARD = range(13, 20)
CUED_CARD = range(12, 20)
TAIL_DIGITS = 4
DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
# A run of digits: groups joined by a space, comma, dash or dot ("4111 1111"), a date ("08/28"),
# or digits said as words ("four one one one"); or a marker left by an earlier redaction.
RUN = re.compile(
    rf"{re.escape(MARKER)}|\d{{1,2}}/\d{{2,4}}(?![\w/])|(?<![\w/])\d+(?:[ \t,.-]\d+)*(?![\w/])"
    rf"|\b{_DIGIT_WORD}(?:[\s,-]+{_DIGIT_WORD})*\b",
    re.IGNORECASE,
)
CARD_WORDS = re.compile(
    r"\b(?:(?:credit|debit)\s+card|card\s+(?:number|no|is)|number\s+on\s+the\s+card|visa|"
    r"mastercard|master\s+card|amex|american\s+express|discover\s+card)\b",
    re.IGNORECASE,
)
# Words that can sit among the digits of a card read out without making the line about anything
# else. "Zip", "street", "dollars", "phone" and every other word end a window.
FILLER = {
    "and", "okay", "ok", "um", "uh", "so", "its", "it", "is", "my", "the", "a", "card", "number",
    "credit", "debit", "visa", "mastercard", "master", "amex", "american", "express", "discover",
    "expiration", "expiry", "exp", "expires", "date", "security", "code", "cvv", "cvc", "on",
    "back", "of", "yeah", "yes", "then", "thats", "that", "sorry", "wait", "let", "me", "see",
    "again", "next", "rest", "ending", "in", "here", "go", "you", "are", "hold", "dash",
}
ENDING = re.compile(r"\bending(?:\s+in)?\s*$", re.IGNORECASE)


@dataclass
class Run:
    line: int
    start: int
    end: int
    groups: list[str]
    known: bool = False  # a marker: a card already taken out
    kept: bool = False  # the last four after "ending in"
    card: bool = False

    @property
    def digits(self) -> str:
        return "".join(self.groups)

    @property
    def tail_shaped(self) -> bool:
        """An expiry date or a security code: a date, or four digits or fewer."""
        return "/" in "".join(self.groups) or len(self.digits) <= TAIL_DIGITS


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _groups(text: str) -> list[str]:
    spoken = re.findall(r"[a-z]+", text, re.IGNORECASE)
    if spoken:
        return [DIGIT_WORDS[w.lower()] for w in spoken]
    if "/" in text:
        return [text]
    return [g for g in re.split(r"[ \t,.-]", text) if g]


def _filler(text: str) -> bool:
    return all(w in FILLER for w in words(text).split())


def _runs(line: str, index: int) -> list[tuple[Run, str]]:
    """Each run in the line with the text just before it."""
    out, at = [], 0
    for match in RUN.finditer(line):
        before = line[at : match.start()]
        known = match.group(0) == MARKER
        run = Run(index, match.start(), match.end(), [] if known else _groups(match.group(0)), known)
        run.kept = bool(ENDING.search(line[: match.start()]))
        out.append((run, before))
        at = match.end()
    return out


def mostly_digits(line: str) -> bool:
    """Digits, or a marker, with nothing but filler around them."""
    if not RUN.search(line):
        return False
    return _filler(RUN.sub(" ", line))


def _card_grouping(sizes: list[int]) -> bool:
    if len(sizes) == 1 or all(size == 1 for size in sizes):
        return True
    if sizes in ([4, 6, 5], [4, 6, 4]):
        return True
    return all(size == 4 for size in sizes[:-1]) and 1 <= sizes[-1] <= 4


def _is_card(runs: list[Run], cued: bool) -> bool:
    groups = [g for run in runs for g in run.groups]
    digits = "".join(groups)
    if any("/" in g for g in groups) or not _card_grouping([len(g) for g in groups]):
        return False
    return (len(digits) in WHOLE_CARD and luhn(digits)) or (cued and len(digits) in CUED_CARD)


def _chains(lines: list[str]) -> list[list[Run]]:
    """The window's runs in order, split where words other than filler come between two of them
    or before kept last four digits. Lines of the window run on into each other."""
    chains: list[list[Run]] = [[]]
    for index, line in enumerate(lines):
        for run, before in _runs(line, index):
            if run.kept or (chains[-1] and not _filler(before)):
                chains.append([])
            chains[-1].append(run)
            if run.kept:
                chains.append([])
    return [c for c in chains if c]


def _mark(chain: list[Run], cued: bool) -> None:
    """Marks the card in the chain, and the expiry or code right after it."""
    i = 0
    while i < len(chain):
        found = None
        for j in range(i, len(chain)):
            if any(r.known or r.kept for r in chain[i : j + 1]):
                break
            if len("".join(r.digits for r in chain[i : j + 1])) > max(WHOLE_CARD):
                break
            if _is_card(chain[i : j + 1], cued):
                found = j
        if found is None and not chain[i].known:
            i += 1
            continue
        end = i if found is None else found
        for run in chain[i : end + 1]:
            run.card = not run.known
        k = end + 1
        while k < len(chain) and not chain[k].kept and (chain[k].known or chain[k].tail_shaped):
            chain[k].card = not chain[k].known
            k += 1
        i = k


def redact_window(lines: list[str]) -> list[str]:
    """The lines with card digits taken out. `lines` are the caller's, oldest first; only the last
    line and the mostly-digit lines just before it can form a card together."""
    if not lines:
        return lines
    start = len(lines) - 1
    if mostly_digits(lines[-1]):
        while start > 0 and mostly_digits(lines[start - 1]):
            start -= 1
    window = lines[start:]
    cued = bool(CARD_WORDS.search(" ".join(window)))
    chains = _chains(window)
    for chain in chains:
        _mark(chain, cued)
    out = list(window)
    for run in sorted((r for c in chains for r in c if r.card), key=lambda r: -r.start):
        line = out[run.line]
        out[run.line] = line[: run.start] + MARKER + line[run.end :]
    joined = [
        re.sub(rf"{re.escape(MARKER)}(?:[\s,/-]*{re.escape(MARKER)})+", MARKER, line) for line in out
    ]
    return lines[:start] + joined


def redact(text: str) -> str:
    """One piece of text on its own, such as a tool argument."""
    return redact_window([text])[-1] if text else text


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
