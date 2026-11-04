"""Redaction for the debug bench (app/bench.py), applied to every value it shows, because the bench
is opened on shared screens. Phone numbers keep their last four digits, a service location keeps
its street name only, and anything that looks like a key, token or secret is replaced.

Structured values are redacted by field name (a `zip` field is dropped whatever it holds); free
text, such as a line the caller said or the system prompt, is redacted by pattern.
"""

import hashlib
import re
from typing import Any

MASK = "•"
HIDDEN = "[redacted]"

# Ten digits with optional country code, brackets and separators, including a number read out as
# "212, 555, 7011". Not preceded or followed by another digit, so dates and ids are left alone.
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.,-]*)?\(?(\d{3})\)?[\s.,-]*(\d{3})[\s.,-]*(\d{4})(?!\d)")
STREET_SUFFIX = (
    r"(?:st|street|ave|avenue|rd|road|pl|place|blvd|boulevard|dr|drive|ln|lane|ct|court|ter|"
    r"terrace|pkwy|parkway|way|hwy|highway|sq|square|loop)"
)
# A house number in front of a street name of one to three words: "45 Forest Ave".
HOUSE_NUMBER = re.compile(
    rf"\b\d{{1,6}}[a-z]?(?:-\d{{1,4}})?\s+(?=(?:[\w'.]+\s+){{1,3}}{STREET_SUFFIX}\b)",
    re.IGNORECASE,
)
LEADING_NUMBER = re.compile(r"^\s*\d{1,6}[a-z]?(?:-\d{1,4})?\s+", re.IGNORECASE)
ZIP = re.compile(r"(?<![\d.-])\d{5}(?:-\d{4})?(?![\d-])(?!\.\d)")
UNIT = re.compile(r"\b(apt|apartment|unit|suite|floor)\.?\s*#?\s*[\w-]{1,6}\b|#\s*\d[\w-]{0,5}\b", re.IGNORECASE)
EMAIL = re.compile(r"\b[\w.+-]+@([\w-]+\.[\w.-]+)\b")
# A house number before a name with no street word ("123 Main", "123 Broadway"): a number then a
# capitalized word, unless the word is a unit of time, a month or a weekday.
NOT_STREETS = (
    r"AM|PM|Am|Pm|Degrees?|Minutes?|Hours?|Days?|Weeks?|Months?|Years?|Percent|Calls?|Times?|"
    r"January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun"
)
BARE_HOUSE_NUMBER = re.compile(
    rf"(?:\b([A-Za-z]+)\s+)?\b(\d{{1,6}}[A-Za-z]?)\s+"
    rf"(?=(?!(?:{NOT_STREETS})\b)(?:[A-Z][a-z]+|broadway|bowery)\b)"
)
# Words a number follows that make it a count or a date, not a house number: "Round 4 Jackson",
# "version 3 Dana", "Nov 3 Brooklyn".
COUNTING = {
    "round", "version", "call", "calls", "line", "turn", "step", "rule", "seed", "part", "phase",
    "decision", "exploration", "issue", "pr", "run", "sample", "number", "no", "of", "and", "to",
    "from", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
    "dec", "january", "february", "march", "april", "june", "july", "august", "september",
    "october", "november", "december", "day", "week", "page", "item", "lane", "batch",
}


def _bare_house_number(match: re.Match) -> str:
    before = match.group(1)
    if before and before.lower() in COUNTING:
        return match.group(0)
    return (f"{before} " if before else "") + f"{MASK * 2} "
# Digits said as words ("seven one eight", "five oh one"), and digits said one at a time
# ("4 4 1 1"): three or more in a row.
DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
SPOKEN_DIGITS = re.compile(rf"\b{DIGIT_WORD}(?:[\s,.-]+{DIGIT_WORD}){{2,}}\b", re.IGNORECASE)
SPACED_DIGITS = re.compile(r"(?<![\d.])\b\d(?:[\s,-]+\d\b){2,}(?![.\d])")
# A door, gate or alarm code said in a sentence: "the gate code is 4411".
CODE = re.compile(
    r"\b((?:gate|door|lockbox|lock box|alarm|access|entry|garage|building|keypad)\s+code|"
    r"passcode|pin)(\s*(?:is|was|:|=|of)?\s*)#?\d[\d -]{1,10}\d",
    re.IGNORECASE,
)
KEY_LIKE = re.compile(
    r"\b(?:sk-|sk_live_|sk_test_|pk-|pk_live_|pk_test_|rk-|rk_live_|rk_test_|proj-|ghp_|gho_|"
    r"ghs_|github_pat_|xox[abpr]-|AKIA|lsv2_|eyJ)[-_A-Za-z0-9.]{10,}"
    r"|\b(?=[A-Za-z0-9]{32,}\b)(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]+\b"
    # Base64 with + / =, as in many bearer tokens: long, with upper case, lower case and a digit.
    r"|(?<![\w+/])(?=[A-Za-z0-9+/]{32,})(?=\S*[A-Z])(?=\S*[a-z])(?=\S*\d)[A-Za-z0-9+/]{32,}={0,2}"
)
BEARER = re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)

PHONE_FIELDS = {"phone", "caller_id", "caller_key", "phone_key", "dialed_number", "number",
                "callback_number", "to_number", "from_number"}
# Parts of an address after the street name.
ADDRESS_DROP = {"zip", "zip_code", "postal_code", "unit", "apartment", "city", "state",
                "house_number", "latitude", "longitude", "lat", "lng", "to_address"}
STREET_FIELDS = {"street", "address_line", "line1", "street_address"}
SECRET_WORDS = ("token", "secret", "password", "apikey", "authorization", "credential", "bearer",
                "gate_code", "access_code", "door_code", "idempotency")


def phone(match: re.Match) -> str:
    return f"{MASK * 3}-{MASK * 3}-{match.group(3)}"


def text(value: str) -> str:
    """Free text with phone numbers, house numbers, units, ZIPs, codes, emails and keys masked."""
    value = BEARER.sub(lambda m: f"{m.group(1)} {HIDDEN}", value)
    value = KEY_LIKE.sub(HIDDEN, value)
    value = EMAIL.sub(lambda m: f"{MASK * 3}@{m.group(1)}", value)
    value = CODE.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK * 4}", value)
    value = PHONE.sub(phone, value)
    value = SPOKEN_DIGITS.sub(MASK * 3, value)
    value = SPACED_DIGITS.sub(MASK * 3, value)
    value = HOUSE_NUMBER.sub(f"{MASK * 2} ", value)
    value = BARE_HOUSE_NUMBER.sub(_bare_house_number, value)
    value = UNIT.sub(lambda m: f"{(m.group(1) or 'unit').lower()} {MASK * 2}", value)
    return ZIP.sub(MASK * 5, value)


def street(value: str) -> str:
    """A street line with its house number taken off: "45 Forest Ave" is "Forest Ave"."""
    return text(LEADING_NUMBER.sub("", value))


def _secret(key: str) -> bool:
    key = key.lower().replace("-", "_")
    return key == "key" or key.endswith("_key") or any(word in key for word in SECRET_WORDS)


def value(obj: Any, key: str = "") -> Any:
    """Any JSON-like value, redacted by field name where it has one and by pattern otherwise.
    Dropped address parts stay as keys with a masked value, so the shape of a request still reads."""
    name = key.lower()
    if isinstance(obj, dict):
        return {k: value(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [value(v, key) for v in obj]
    if obj is None or isinstance(obj, bool):
        return obj
    if name in PHONE_FIELDS:
        return text(str(obj))
    if _secret(name):
        return HIDDEN
    if name in ADDRESS_DROP:
        return MASK * 3
    if isinstance(obj, (int, float)):
        return obj
    if not isinstance(obj, str):
        return obj
    if name in STREET_FIELDS:
        return street(obj)
    return text(obj)


def call_key(call_id: str) -> str:
    """A short stand-in for a call id in the bench's addresses and file names: a LiveKit call id
    carries the caller's number ("call-_+1646..._mKdA"), and an address bar or a download name is
    not redacted."""
    return hashlib.sha256(call_id.encode()).hexdigest()[:12]
