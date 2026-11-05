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


GOODBYES = ("bye", "goodbye", "good bye", "talk to you later", "have a good one", "have a good day")


def says_goodbye(text: str) -> bool:
    text_words = words(text)
    return any(has_phrase(text_words, g) for g in GOODBYES)


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


DIGIT_WORDS = {
    "oh": "0", "o": "0", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def digit_runs(text: str) -> list[str]:
    """The runs of digits a line says, however speech-to-text wrote them: "11226", "11 226", "one
    one two two six", "one one two double six" and "eleven two two six" are each one run, "11226";
    "seventy five" is "75". Punctuation ends a run, so "11226, 718 555 0142" is two."""
    runs = []
    for part in re.split(r"[.,;:!?]", text or ""):
        runs += _digit_runs(part)
    return runs


def _digit_runs(text: str) -> list[str]:
    # A ZIP said in pairs ("Eleven two zero one") was heard as "201" and asked for again (realism
    # suite on #155, live-replay-2-move-and-tuneup).
    runs, current, double, tens = [], "", False, False
    for token in words(text).split():
        if token == "double":
            double = True
            continue
        digits = token if token.isdigit() else DIGIT_WORDS.get(token)
        if digits is None and token in TEENS:
            digits = str(TEENS[token])
        if digits is None and token in TENS:
            digits = str(TENS[token])
        if digits is None:
            if current:
                runs.append(current)
            current, double, tens = "", False, False
            continue
        if tens and token in DIGIT_WORDS and digits != "0":
            current = current[:-1] + digits  # "seventy five" is 75, not 705
        else:
            current += digits * 2 if double and len(digits) == 1 else digits
        double, tens = False, token in TENS
    if current:
        runs.append(current)
    return runs



TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}


def number_tokens(text: str) -> list[str]:
    """The line as lowercase tokens with each spoken number written as digits, the way an address
    is said in New York: "two forty eight" is 248, "two hundred forty eight" is 248, "forty two
    forty one" is 4241 (42-41), "ten sixty two" is 1062, "one oh six two" is 1062. Punctuation is
    kept as its own token, so it ends a number. Letters spelled one by one are joined first."""
    text = join_spelled(text or "").lower().replace("'", "").replace("’", "")
    raw = re.findall(r"\d+(?:st|nd|rd|th)\b|[a-z]+|\d+|[^\sa-z\d]", text)
    out: list[str] = []
    chunks: list[str] = []  # the number being read, as digit strings to be joined

    def flush():
        if chunks:
            out.append("".join(chunks))
            chunks.clear()

    i = 0
    while i < len(raw):
        token = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else ""
        if re.fullmatch(r"\d+(?:st|nd|rd|th)", token):
            # "34th": a numbered street, never part of the number before it ("77-10 34th Ave").
            flush()
            out.append(re.sub(r"\D", "", token))
        elif token == "-" and chunks and (nxt.isdigit() or nxt in DIGIT_WORDS or nxt in TEENS
                                          or nxt in TENS):
            pass  # a Queens house number, "41-12"
        elif token.isdigit():
            chunks.append(token)
        elif token in DIGIT_WORDS and not (token in ("o", "oh") and not chunks):
            chunks.append(DIGIT_WORDS[token])
        elif token in TEENS:
            chunks.append(str(TEENS[token]))
        elif token in TENS:
            value = TENS[token]
            if nxt in DIGIT_WORDS and nxt not in ("oh", "o", "zero"):
                value += int(DIGIT_WORDS[nxt])
                i += 1
            chunks.append(str(value))
        elif token in ("hundred", "thousand") and chunks and chunks[-1].isdigit():
            scale = 100 if token == "hundred" else 1000
            base = int(chunks.pop()) * scale
            # "two hundred forty eight", "two hundred and five"
            j = i + 1
            if j < len(raw) and raw[j] == "and":
                j += 1
            rest = 0
            if j < len(raw) and raw[j] in TENS:
                rest = TENS[raw[j]]
                if j + 1 < len(raw) and raw[j + 1] in DIGIT_WORDS and raw[j + 1] not in ("oh", "o", "zero"):
                    rest += int(DIGIT_WORDS[raw[j + 1]])
                    j += 1
                i = j
            elif j < len(raw) and raw[j] in TEENS:
                rest, i = TEENS[raw[j]], j
            elif j < len(raw) and raw[j] in DIGIT_WORDS and raw[j] not in ("oh", "o", "zero"):
                rest, i = int(DIGIT_WORDS[raw[j]]), j
            chunks.append(str(base + rest))
        else:
            flush()
            out.append(token)
        i += 1
    flush()
    return out


ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9}
SPOKEN_NUMBER = re.compile(
    rf"\b(?:((?:a|one)[\s-]+hundred)(?:[\s-]+and)?[\s-]+)?({'|'.join(TENS)})"
    rf"(?:[\s-]+({'|'.join(ONES)}))?\b"
)
SPOKEN_HUNDRED = re.compile(rf"\b(?:a|one)[\s-]+hundred(?:[\s-]+and)?[\s-]+({'|'.join(ONES)})\b")
DECADE_WORDS = {"twenties": 20, "thirties": 30, "forties": 40, "fifties": 50, "sixties": 60,
                "seventies": 70, "eighties": 80, "nineties": 90}


def spoken_numbers(value: str) -> str:
    """Lowercased text with spoken tens written as digits: "eighty-four" is "84" and "fifty two
    degrees" is "52 degrees". Only twenty to ninety-nine (and "a hundred and five") change, the
    range of ages and temperatures, so "no one" and "one or two" keep their words."""

    def digits(m: re.Match) -> str:
        return str((100 if m[1] else 0) + TENS[m[2]] + (ONES[m[3]] if m[3] else 0))

    value = SPOKEN_NUMBER.sub(digits, (value or "").lower())
    return SPOKEN_HUNDRED.sub(lambda m: str(100 + ONES[m[1]]), value)


SPOKEN_DIGITS = {"zero": 0, "oh": 0, "o": 0, **ONES}
# "Fiftyish" and "50ish": a few degrees either way.
ISH_REACH = 4


def numbers_said(value: str) -> list[tuple[int, float, float]]:
    """The numbers in a line of speech, each as (word position, lowest, highest) in words(): "52"
    and "fifty two" are (n, 52, 52); "the fifties" and "50s" are (n, 50, 59); "fiftyish" is (n,
    46, 54); two digits said one by one, "five oh" or "four five", are (n, 50, 50) and (n, 45, 45).
    """
    found = []
    text = re.sub(
        rf"\b({'|'.join(TENS)})ish\b", lambda m: f"{TENS[m[1]]}ish", (value or "").lower()
    )
    tokens = words(spoken_numbers(text)).split()
    for i, token in enumerate(tokens):
        decade = re.fullmatch(r"(\d)0s", token)
        ish = re.fullmatch(r"(\d+)ish", token)
        pair = i + 1 < len(tokens) and token in ONES and tokens[i + 1] in SPOKEN_DIGITS
        if ish:
            found.append((i, int(ish[1]) - ISH_REACH, int(ish[1]) + ISH_REACH))
        elif pair:
            number = ONES[token] * 10 + SPOKEN_DIGITS[tokens[i + 1]]
            found.append((i, number, number))
        elif decade:
            found.append((i, int(decade[1]) * 10, int(decade[1]) * 10 + 9))
        elif token in DECADE_WORDS:
            found.append((i, DECADE_WORDS[token], DECADE_WORDS[token] + 9))
        elif token.isdigit():
            found.append((i, int(token), int(token)))
    return found
