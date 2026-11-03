"""Normalizing what callers say: phone numbers, street names, and lines of speech."""

import re

STREET_WORDS = {
    "street": "st",
    "avenue": "ave",
    "road": "rd",
    "place": "pl",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "terrace": "ter",
    "parkway": "pkwy",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}


def phone_key(value: str) -> str:
    """Last ten digits, so (718) 555-0142, 718-555-0142 and +1 718 555 0142 all match."""
    return re.sub(r"\D", "", value or "")[-10:]


def phone_spoken(value: str) -> str:
    """A number read back in groups: 718, 555, 0142."""
    key = phone_key(value)
    return f"{key[:3]}, {key[3:6]}, {key[6:]}" if len(key) == 10 else value


def phone_display(value: str) -> str:
    key = phone_key(value)
    return f"({key[:3]}) {key[3:6]}-{key[6:]}" if len(key) == 10 else value


def street_key(value: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower()).split()
    return " ".join(STREET_WORDS.get(w, w) for w in words)


def unit_key(value: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (value or "").lower()).split()
    return "".join(w for w in words if w not in {"apt", "apartment", "unit", "fl", "floor"})


def words(value: str) -> str:
    """Lowercase words separated by single spaces, punctuation dropped, so phrase checks are simple
    substring checks: "I smell GAS!" becomes "i smell gas"."""
    value = (value or "").lower().replace("'", "").replace("’", "")
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", value).split()) + " "


def has_phrase(text_words: str, phrase: str) -> bool:
    """Whole-word phrase match on text already passed through words()."""
    return words(phrase) in text_words
