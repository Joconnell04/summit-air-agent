import re

STREET_WORDS = {
    "street": "st", "avenue": "ave", "road": "rd", "place": "pl", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "court": "ct", "terrace": "ter", "parkway": "pkwy",
    "north": "n", "south": "s", "east": "e", "west": "w",
}


def phone_key(value: str) -> str:
    """Last ten digits, so (718) 555-0142, 718-555-0142 and +1 718 555 0142 all match."""
    return re.sub(r"\D", "", value)[-10:]


def street_key(value: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", value.lower()).split()
    return " ".join(STREET_WORDS.get(w, w) for w in words)


def unit_key(value: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", value.lower()).split()
    return "".join(w for w in words if w not in {"apt", "apartment", "unit", "fl", "floor"})
