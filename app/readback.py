"""Does a caller line change a detail the read-back named? Used after the caller confirmed it, so
"yes, but it's 4B" or "yes, it's 144" resets the confirmation and the booking waits for a new
read-back (DECISIONS.md 083). A plain yes, or the same value said again, changes nothing.

Best effort by design, with the error on the safe side: a line read as a change costs one more
read-back, a change missed can book the wrong door. Only shapes that name an address detail count:
a unit ("4B", "four B", "apartment 3"), a house number right before a street name or right after
"it's", a street with its street word, a five-digit ZIP and a ten-digit phone number.
"""

import re

from .text import STREET_WORDS, digit_runs, number_tokens, phone_key, street_key, unit_key

UNIT_WORDS = {"apartment", "apt", "unit", "suite", "ste"}
ORDINAL = re.compile(r"^\d+(?:st|nd|rd|th)$")
# A bare number is a house number only right after one of these: "it's 144", "the number is 144".
HOUSE_LEADS = ("its", "it is", "thats", "that is", "number is", "house is", "address is", "its at",
               "make it")
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


# A value right after "not" is the one the caller rejects: "1120 Ocean Ave, not 11230 Ocean Ave",
# "it's 144, not 142" (eval run 20261104-201607, heat-and-sink-leak). The words up to the next
# break, "and", "but" or "it's", at most eight, are left out.
REJECTED = re.compile(
    r"\b(?:not|isn'?t|instead of)\b(?:\s+(?!(?:and|but|it'?s|its)\b)[^\s,.;!?]+){1,8}",
    re.IGNORECASE,
)


# A code for getting in, which is never a unit or an address: "The buzzer is 2B" answered the
# access question after the read-back confirmed "unit one F", and forced a second read-back (eval
# world, Retell call call_7f892564bd0df180ca6eb994b4a).
ACCESS_CODE = re.compile(
    r"\b(?:buzzer|intercom|gate code|door code|access code|code|lockbox|lock box|key box)\b"
    r"(?:\s+(?:is|number|code|its|it's|for|at|the|on|of|to|press|ring|use))*\s*:?\s*[\w-]+",
    re.IGNORECASE,
)


def without_access_codes(text: str) -> str:
    return ACCESS_CODE.sub(" ", text or "")


def change(text: str, readback: dict) -> str | None:
    """The read-back field this caller line gives a different value for (unit, house number,
    street, ZIP or phone), or None. A value the caller rejects ("not 142") doesn't count, nor
    does a buzzer or gate code."""
    text = REJECTED.sub(" ", without_access_codes(text))
    # "Yes, 2A and 5C" to a read-back of 5C names the unit read back: no change.
    units = _units_said(text)
    if units and unit_key(readback.get("unit", "")) not in units:
        return "unit"
    house = _house(readback.get("street", ""))
    # The read-back's own ZIP and phone groups said again ("it's 11231", "did you say seven one
    # eight", "the number you said, five five five") are never a house number (eval run
    # 20261104-234633, inside-temp-only-when-asked and expired-member-hard-of-hearing).
    phone = phone_key(readback.get("phone", ""))
    echoes = {house, readback.get("zip"), phone, phone[:3], phone[3:6], phone[6:], phone[3:]}
    for said in _houses_said(text, readback.get("street", "")):
        if said not in echoes:
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


def units_said(text: str) -> list[str]:
    """The units a line names, as unit keys, in order and without repeats: "2A and 5C". A buzzer or
    gate code isn't one."""
    return list(dict.fromkeys(_units_said(without_access_codes(text))))


def _units_said(text: str) -> list[str]:
    """Units as callers say them: "4B", "4 B", "four B", "apartment 3", "unit C". Numbers are
    read from words first, so "four b" is "4b"; ordinals ("2nd floor") are never units."""
    tokens = number_tokens(text)
    found = []
    for i, token in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if token in UNIT_WORDS and nxt:
            after = tokens[i + 2] if i + 2 < len(tokens) else ""
            if nxt.isdigit() and len(nxt) <= 4:
                found.append(unit_key(nxt + (after if _letter(after) else "")))
            elif _letter(nxt):
                found.append(nxt)
        elif token.isdigit() and len(token) <= 4 and _letter(nxt) and not _ordinal(text, token):
            if not (i and tokens[i - 1] in UNIT_WORDS):
                found.append(token + nxt)
    return found


def _letter(token: str) -> bool:
    """A unit letter: one letter, not the word "I"."""
    return len(token) == 1 and token.isalpha() and token != "i"


def _ordinal(text: str, number: str) -> bool:
    return bool(re.search(rf"\b{number}(?:st|nd|rd|th)\b", text.lower()))


# Words that follow a number that isn't a house number: "3 days", "12 to 4", "2 boilers", "4 works".
NOT_STREET = {
    "to", "or", "and", "of", "in", "at", "on", "for", "from", "by", "with", "the", "a", "is", "was",
    "am", "pm", "oclock", "days", "day", "weeks", "week", "hours", "hour", "minutes", "minute",
    "months", "month", "years", "year", "degrees", "degree", "people", "person", "kids", "children",
    "adults", "dogs", "cats", "pets", "units", "apartments", "floors", "floor", "stories", "story",
    "family", "bedrooms", "bedroom", "bathrooms", "rooms", "room", "radiators", "radiator",
    "boilers", "furnaces", "zones", "systems", "thermostats", "heaters", "windows", "tenants",
    "times", "time", "more", "percent", "dollars", "bucks", "blocks", "feet", "inches", "works",
    "sounds", "would", "will", "should", "please", "thanks", "thank", "ok", "okay", "fine", "good",
    "great", "perfect", "are", "it", "that", "this", "then", "now", "today", "tomorrow",
    "morning", "afternoon", "evening", "tonight", "yes", "yeah", "no", "sorry", "actually", "too",
    "also", "but", "so", "right", "correct", "window", "slot", "noon", "midnight", "visit",
    "appointment", "between", "healthy", "below", "above", "plus", "something", "ish", "max",
    "tops", "maybe", "total", "us", "them", "grown", "young", "little", "small", "big",
    "different", "separate", "other", "new", "older", "elderly", "seniors", "book", "can", "confirm",
    "i", "we", "you", "he", "she", "they", "my", "your", "if", "when", "just", "old", "ago", "one", "ones", "car", "cars",
}


# Words right before a number that make it a time or a date, not a house number.
TIME_LEADS = {
    "to", "at", "from", "between", "by", "around", "after", "before", "until", "till", "til", "and",
    "or", "about", "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "the",
}


def _houses_said(text: str, street: str = "") -> list[str]:
    """House numbers in the line: a number right before a street name, with or without its street
    word ("148 Main", "it's 143 Main Street", "188-10 64th Avenue"), or a bare number right after
    "it's" and ending the thought ("it's 144"). A one-digit number counts only before a street word
    or the read-back's own street name ("5 Union"), so "two healthy adults" names no house."""
    tokens = number_tokens(text)
    names = set(street_key(street).split()) - SUFFIXES
    found = []
    for i, token in enumerate(tokens):
        if not token.isdigit() or len(token) > 6 or _ordinal(text, token):
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        after = tokens[i + 2] if i + 2 < len(tokens) else ""
        prev = tokens[i - 1] if i else ""
        suffix = nxt in SUFFIXES or after in SUFFIXES
        named = nxt.isalpha() and len(nxt) > 1 and nxt not in NOT_STREET and nxt not in UNIT_WORDS
        # A time or a date is not an address: "8 to 12 book it", "November 5", "at 4 works".
        timed = prev in TIME_LEADS or prev.isdigit()
        if named and (suffix or (not timed and (len(token) > 1 or nxt in names))):
            found.append(token)
            continue
        if nxt.isdigit() and _ordinal(text, nxt):  # "188-10 64th Avenue"
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
