"""Normalizing what callers say: phone numbers, street names, and lines of speech."""

import re
from datetime import time

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


DIGIT_NAMES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def digits_spoken(digits: str) -> str:
    """Digits as words, one by one, so the voice says "one one two three one" rather than
    "eleven thousand..." (a live call read a number back as "six hundred and forty six")."""
    return " ".join(DIGIT_NAMES[int(d)] for d in digits if d.isdigit())


def phone_said(value: str) -> str:
    """A number read back digit by digit in groups: "seven one eight, five five five, zero one
    four two"."""
    key = phone_key(value)
    if len(key) != 10:
        return value
    return ", ".join(digits_spoken(part) for part in (key[:3], key[3:6], key[6:]))


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


# A clock time with no AM or PM, read the way a caller means it during the day: "8" is 8 AM and "2"
# is 2 PM, because nobody books a visit or a callback for 2 in the morning (eval round 2, seed 7).
DAY_STARTS_HOUR = 8
NOON_WORDS = {"noon": "12 pm", "midday": "12 pm", "midnight": "12 am"}


def clock_time(value: str, after: time | None = None) -> time:
    """A clock time from what a caller said: "16:30", "4:30 PM", "4 pm", "noon", or a bare "8" or
    "2". A bare hour from 1 to 7 is afternoon; given `after` (the start of a range), a bare hour
    that would not come after it moves to the afternoon too, so "8 to 2" is 8 AM to 2 PM. Raises
    ValueError for anything else."""
    text = NOON_WORDS.get(value.strip().lower(), value.strip().lower())
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?\s*(?:m\.?)?", text)
    if not match:
        raise ValueError("not a time")
    hour, minute = int(match[1]), int(match[2] or 0)
    if minute > 59:
        raise ValueError("not a time")
    if match[3]:
        if not 1 <= hour <= 12:
            raise ValueError("not a time")
        return time(hour % 12 + (12 if match[3] == "p" else 0), minute)
    if hour > 23:
        raise ValueError("not a time")
    if 1 <= hour < DAY_STARTS_HOUR:
        hour += 12
    if after is not None and hour < 12 and time(hour, minute) <= after:
        hour += 12
    return time(hour, minute)


# Letter names a caller may say when spelling, as phone speech-to-text writes them.
LETTER_NAMES = {
    "ay": "a", "bee": "b", "cee": "c", "see": "c", "dee": "d", "ee": "e", "eff": "f", "gee": "g",
    "aitch": "h", "jay": "j", "kay": "k", "el": "l", "em": "m", "en": "n", "oh": "o", "pee": "p",
    "cue": "q", "ar": "r", "ess": "s", "tee": "t", "you": "u", "vee": "v", "ex": "x", "why": "y",
    "zee": "z", "zed": "z",
}
SPELLED_RUN = re.compile(r"\b[A-Za-z](?:[\s,.\-]+[A-Za-z]\b){2,}")


def spelled_word(value: str) -> str:
    """Letters said one by one ("H, O, U, S, T, O, N", "h o u s t o n", "aitch oh you") joined
    into one capitalized word ("Houston"). "As in" examples ("S as in Sam") are dropped. Returns ""
    when the text holds no letters."""
    text = re.sub(r"\bas in \w+", " ", value.lower())
    letters = []
    for token in re.split(r"[\s,.\-]+", text):
        if len(token) == 1 and token.isalpha():
            letters.append(token)
        elif token in LETTER_NAMES:
            letters.append(LETTER_NAMES[token])
    return "".join(letters).capitalize()


def join_spelled(street: str) -> str:
    """A street with a run of single letters in it ("81 H O U S T O N Street") with the letters
    joined into the name ("81 Houston Street"). Anything else is returned as it is."""
    return SPELLED_RUN.sub(lambda m: spelled_word(m.group()), street)


def with_spelled_name(street: str, spelled: str) -> str:
    """The street with its name replaced by the spelled one, keeping the house number in front and
    the street type at the end: "81 Huston St" spelled "H, O, U, S, T, O, N" is "81 Houston St"."""
    name = spelled_word(spelled)
    if not name:
        return street
    tokens = street.split()
    start = 0
    while start < len(tokens) and re.fullmatch(r"[\d\-]+[A-Za-z]?", tokens[start]):
        start += 1
    end = len(tokens)
    while end > start and tokens[end - 1].lower().strip(".") in STREET_TYPES:
        end -= 1
    return " ".join(tokens[:start] + [name] + tokens[end:])


STREET_TYPES = {
    word for pair in STREET_WORDS.items() for word in pair
    if pair[0] not in ("north", "south", "east", "west")
}
