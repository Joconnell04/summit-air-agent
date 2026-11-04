"""Does a caller line change a detail the read-back named? Used after the caller confirmed it, so
"yes, but it's 4B" or "yes, it's 144" resets the confirmation and the booking waits for a new
read-back (DECISIONS.md 077). A plain yes, or the same value said again, changes nothing.

Best effort by design, with the error on the safe side: a line read as a change costs one more
read-back, a change missed can book the wrong door. Only shapes that name an address detail count:
a unit ("4B", "apartment 3"), a house number right after "it's" or right before a street, a street
with its street word, a five-digit ZIP and a ten-digit phone number.
"""

import re

from .text import STREET_WORDS, digit_runs, number_tokens, phone_key, street_key, unit_key, words

UNIT_WORDS = {"apartment", "apt", "unit", "suite", "ste"}
# "4B", "12c": a unit as callers say it. Ordinals ("2nd", "64th") are streets and floors.
UNIT_TOKEN = re.compile(r"^\d{1,4}[a-z]$")
ORDINAL = re.compile(r"^\d+(?:st|nd|rd|th)$")
# A bare number is a house number only right after one of these: "it's 144", "the number is 144".
HOUSE_LEADS = ("its", "it is", "thats", "that is", "number is", "house is", "address is", "its at")
# Words that may follow a house number to end the thought: "it's 144, sorry".
HOUSE_TAIL = {"", ",", ".", "!", "?", ";", "not", "sorry", "actually", "instead"}
SUFFIXES = set(STREET_WORDS) | {v for v in STREET_WORDS.values()} | {"blvd", "pkwy", "ter"}
SUFFIXES -= {"north", "south", "east", "west", "n", "s", "e", "w"}
SUFFIXES |= {"street", "st", "avenue", "ave", "road", "rd", "place", "pl", "way"}
NOT_NAMES = {"the", "a", "my", "on", "at", "its", "it's", "is", "this", "that", "same", "yes", "yeah",
             "no", "i", "me", "and", "but", "so", "oh", "ok", "okay"}
ORDINAL_WORDS = {"first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
                 "ninth", "tenth", "eleventh", "twelfth"}
# A street named after one of these is a cross street or a landmark, not the address: "off
# Flatbush Avenue", "between Union and Sackett Street".
CROSS_LEADS = {"off", "near", "by", "of", "and", "between", "from", "past", "to", "corner"}


def _house(street: str) -> str:
    """The house number a read-back street starts with, as digits: "188-10 64th Ave" is 18810."""
    first = (street or "").split()[:1]
    return re.sub(r"\D", "", first[0]) if first and re.search(r"\d", first[0]) else ""


def change(text: str, readback: dict) -> str | None:
    """The read-back field this caller line gives a different value for (unit, house number,
    street, ZIP or phone), or None."""
    tokens = words(text).split()
    unit = _unit_said(tokens)
    if unit is not None and unit != unit_key(readback.get("unit", "")):
        return "unit"
    house = _house(readback.get("street", ""))
    for said in _houses_said(text):
        if said != house:
            return "house number"
    street = _street_said(text)
    if street and street not in street_key(readback.get("street", "")):
        return "street"
    for run in digit_runs(text):
        if len(run) == 5 and run not in (readback.get("zip"), house):
            return "ZIP"
        if len(run) in (10, 11) and phone_key(run) != phone_key(readback.get("phone", "")):
            return "phone"
    return None


def _unit_said(tokens: list[str]) -> str | None:
    for i, token in enumerate(tokens):
        if token in UNIT_WORDS and i + 1 < len(tokens):
            value = tokens[i + 1]
            # "apartment 4 b": a unit said as a number and a letter.
            if value.isdigit() and i + 2 < len(tokens) and len(tokens[i + 2]) == 1:
                value += tokens[i + 2]
            if value.isdigit() or UNIT_TOKEN.match(value) or len(value) == 1:
                return unit_key(value)
        if UNIT_TOKEN.match(token) and not ORDINAL.match(token):
            return token
    return None


def _houses_said(text: str) -> list[str]:
    """House numbers in the line: a number right before a street with its street word, or a bare
    number right after "it's" and ending the thought."""
    tokens = number_tokens(text)
    found = []
    for i, token in enumerate(tokens):
        if not token.isdigit() or len(token) > 6:
            continue
        # A numbered street ("64th Avenue") is not a house number.
        if re.search(rf"\b{token}(?:st|nd|rd|th)\b", text.lower()):
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        after = tokens[i + 2] if i + 2 < len(tokens) else ""
        # "144 Main Street", "188-10 64th Avenue": a number before a named street.
        if nxt and (nxt in SUFFIXES or after in SUFFIXES) and not ORDINAL.match(token):
            found.append(token)
            continue
        before = " " + " ".join(tokens[max(0, i - 3) : i]) + " "
        if any(before.endswith(f" {lead} ") for lead in HOUSE_LEADS) and nxt in HOUSE_TAIL:
            found.append(token)
    return found


def _street_said(text: str) -> str:
    """A street name with its street word ("Union Avenue", "65th Ave"), as a street key, or "".
    The name has to be written as one, capitalized or a numbered street, so "my mother's place"
    and "call me Dr. Patel" name no street."""
    tokens = re.findall(r"[A-Za-z0-9']+", text or "")
    for i, token in enumerate(tokens):
        if token.lower() not in SUFFIXES or i == 0:
            continue
        name = tokens[i - 1]
        lead = tokens[i - 2].lower() if i > 1 else ""
        numbered = bool(ORDINAL.match(name.lower()))
        if not (name[:1].isupper() or numbered) or lead in CROSS_LEADS:
            continue
        if name.lower() in NOT_NAMES or name.lower() in ORDINAL_WORDS:
            continue
        return street_key(f"{name} {token}")
    return ""
