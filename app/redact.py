"""Redaction for the debug bench (app/bench.py), applied to every value it shows, because the bench
is opened on shared screens. Phone numbers keep their last four digits, a service location keeps
its street name only, and anything that looks like a key, token or secret is replaced.

Structured values are redacted by field name (a `zip` field is dropped whatever it holds); free
text, such as a line the caller said or the system prompt, is redacted by pattern.
"""

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
KEY_LIKE = re.compile(
    r"\b(?:sk-|pk-|rk-|ghp_|gho_|ghs_|github_pat_|xox[abpr]-|AKIA|lsv2_|eyJ)[-_A-Za-z0-9.]{10,}"
    r"|\b(?=[A-Za-z0-9]{32,}\b)(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]+\b"
)

PHONE_FIELDS = {"phone", "caller_id", "caller_key", "phone_key", "dialed_number", "number",
                "callback_number", "to_number", "from_number"}
# Parts of an address after the street name.
ADDRESS_DROP = {"zip", "zip_code", "postal_code", "unit", "apartment", "city", "state",
                "house_number", "latitude", "longitude", "lat", "lng", "to_address"}
STREET_FIELDS = {"street", "address_line", "line1", "street_address"}
SECRET_WORDS = ("token", "secret", "password", "api_key", "apikey", "authorization", "credential",
                "gate_code", "access_code", "fsm_key", "idempotency")


def phone(match: re.Match) -> str:
    return f"{MASK * 3}-{MASK * 3}-{match.group(3)}"


def text(value: str) -> str:
    """Free text with phone numbers, house numbers, units, ZIPs, emails and keys masked."""
    value = KEY_LIKE.sub(HIDDEN, value)
    value = EMAIL.sub(lambda m: f"{MASK * 3}@{m.group(1)}", value)
    value = PHONE.sub(phone, value)
    value = HOUSE_NUMBER.sub(f"{MASK * 2} ", value)
    value = UNIT.sub(lambda m: f"{(m.group(1) or 'unit').lower()} {MASK * 2}", value)
    return ZIP.sub(MASK * 5, value)


def street(value: str) -> str:
    """A street line with its house number taken off: "45 Forest Ave" is "Forest Ave"."""
    return text(LEADING_NUMBER.sub("", value))


def _secret(key: str) -> bool:
    key = key.lower()
    return any(word in key for word in SECRET_WORDS)


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
    if _secret(name):
        return HIDDEN
    if name in ADDRESS_DROP:
        return MASK * 3
    if isinstance(obj, (int, float)):
        if name in PHONE_FIELDS:
            return text(str(obj))
        return obj
    if not isinstance(obj, str):
        return obj
    if name in STREET_FIELDS:
        return street(obj)
    return text(obj)
