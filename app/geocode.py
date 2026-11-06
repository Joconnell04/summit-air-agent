"""Finding the ZIP for a street address the caller said, so the agent never has to guess one
(docs/tools.md, find_address). The server still decides: app/tools.py takes a ZIP from a lookup
only when the house number and street came from the caller's own lines, and only when the lookup
found exactly one place.

Backends, tried in order:
- NYC Planning's GeoSearch (geosearch.planninglabs.nyc), built on the city's own Property Address
  Directory. Free, no key. It reads Queens house numbers ("41-12"), street aliases ("B'way") and
  addresses with no borough, and answers in about 90 ms (MEASURED, six queries from the build Mac).
- The US Census geocoder, only when GeoSearch fails or times out. It needs a city and misses some
  Queens addresses, so it is the fallback, not the first choice (docs/explorations/015).

Both are fuzzy search engines: they return near matches ("248 Duffield" brings back 16 Duffield
too). Only a result with the same house number and the same street counts here.

A lookup is started as soon as a caller's line has a house number and a street (`prefetch`), so
the answer is usually back before the agent asks for it. Answers are kept per house and street,
whatever borough the agent passes, and the borough is applied after.
"""

import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass

import httpx

from .text import DIGIT_WORDS, number_tokens, street_key

log = logging.getLogger("summit.geocode")

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
# Results asked of GeoSearch. Near matches come first for a common street ("120 Bay St" brings
# back Bay 20th, Bay 31st and so on), so ask for enough that an exact match in another borough
# isn't cut off (ASSUMED; its limit is 40).
RESULTS = 25
# One backend's longest wait (ASSUMED: ten times GeoSearch's measured answer, and short enough
# that the Census fallback still fits a lookup turn).
REQUEST_TIMEOUT_S = 1.5
# How long the find_address tool waits for an answer still on its way, and how long the read-back
# and booking checks wait (ASSUMED; a read-back turn shouldn't stall for a check that only adds
# a question).
FIND_WAIT_S = 3.0
CHECK_WAIT_S = 0.5
KEEP = 1000  # answers kept, oldest dropped first

ORDINAL_SUFFIX = re.compile(r"^(\d+)(?:st|nd|rd|th)$")
STREET_TYPE_WORDS = {"st", "ave", "rd", "pl", "blvd", "dr", "ln", "ct", "ter", "pkwy", "way"}
# A house number and a street in a caller's line: "248 Duffield Street", "41-12 Bell Blvd",
# "2214 Avenue U". Used only to start a lookup early; the tool gets the agent's own reading.
STREET_TYPES = (
    r"street|st|avenue|ave|road|rd|place|pl|boulevard|blvd|drive|dr|lane|ln|court|ct|terrace|"
    r"ter|parkway|pkwy|way|broadway"
)
ADDRESS_IN_LINE = re.compile(
    rf"\b(\d+(?:-\d+)?[a-z]?)\s+((?:avenue|ave)\s+[a-z]\b|(?:[a-z0-9']+\s+){{0,3}}?(?:{STREET_TYPES})\b)",
    re.IGNORECASE,
)


class Unavailable(Exception):
    """No backend answered: the agent asks for the ZIP instead."""


@dataclass(frozen=True)
class Place:
    house: str
    street: str  # as the city writes it, "248 DUFFIELD STREET" without the number: "DUFFIELD STREET"
    zip: str
    borough: str
    area: str  # the neighborhood, "Downtown Brooklyn"; empty when the backend gives none
    bbl: str = ""  # the city's borough-block-lot, from GeoSearch's PAD addendum; empty otherwise

    def spoken_street(self) -> str:
        """ "248 Duffield Street", "77-10 34th Avenue": the city's capitals made readable, with
        numbered streets given their ordinal."""
        out = []
        for word in self.street.split():
            if word.isdigit():
                out.append(ordinal(int(word)))
            else:
                out.append(word.capitalize() if len(word) > 1 else word)
        return f"{self.house} {' '.join(out)}"


@dataclass(frozen=True)
class Found:
    """`one`: one place, so its ZIP is the address's. `several`: the same address in more than one
    place (the same street in two boroughs). `none`: no such house on that street. `unavailable`:
    no backend answered in time."""

    status: str
    places: tuple[Place, ...] = ()
    source: str = ""

    def zips(self) -> list[str]:
        return [p.zip for p in self.places]


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def house_key(house: str) -> str:
    """ "41-12", "4112" and "41 12" are one house number; letters are kept ("12A")."""
    return re.sub(r"[^0-9a-z]", "", (house or "").lower())


def street_name_words(street: str) -> list[str]:
    """The words of a street that name it, without its type: "Duffield" in "Duffield St", and
    "Court" in "Court Street" (a type word only when another word names the street)."""
    key = street_match_key(street).split()
    while len(key) > 1 and key[-1] in STREET_TYPE_WORDS:
        key.pop()
    named = [w for w in key if w not in ("n", "s", "e", "w")]
    return named or key


def street_match_key(street: str) -> str:
    """The street compared with the city's: "E 19th St", "East 19 Street" and "EAST 19 STREET"
    are one key."""
    parts = []
    for word in street_key(street).split():
        number = ORDINAL_SUFFIX.match(word)
        parts.append(number.group(1) if number else word)
    return " ".join(parts)


def split_address(street: str) -> tuple[str, str] | None:
    """ "248 Duffield St" into ("248", "Duffield St"); None without a house number."""
    match = re.match(r"\s*(\d+(?:\s*-\s*\d+)?[A-Za-z]?)\s+(.+)", street or "")
    if not match:
        return None
    # The street ends where the unit or the borough starts: "Main Street, Apt 5F", "Main St #5F".
    # "Duffield Street 3rd Floor" ends before "3rd".
    street = re.split(
        r",|#|\s(?:\d+(?:st|nd|rd|th)?\s+(?:floor|fl)|apt|apartment|unit|suite|ste|floor|fl)\b",
        match.group(2),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return re.sub(r"\s+", "", match.group(1)), street.strip()


def addresses_in(text: str) -> list[tuple[str, str]]:
    """House numbers and streets in a caller's line, written as digits or said one by one ("two
    four eight Duffield Street"), to start their lookups early."""
    found = []
    for line in (text or "", _with_digits(text or ""), " ".join(number_tokens(text or ""))):
        for m in ADDRESS_IN_LINE.finditer(line):
            if (m.group(1), m.group(2).lower()) not in [(h, s.lower()) for h, s in found]:
                found.append((m.group(1), m.group(2)))
    return found


def _with_digits(text: str) -> str:
    """The line with digits said as words written as digits, a run of them joined into one
    number: "two four eight Duffield" becomes "248 Duffield"."""
    out, run = [], ""
    for token in re.findall(r"[A-Za-z0-9']+", text):
        digit = token if token.isdigit() else DIGIT_WORDS.get(token.lower())
        if digit is not None:
            run += digit
            continue
        if run:
            out.append(run)
            run = ""
        out.append(token)
    if run:
        out.append(run)
    return " ".join(out)


@dataclass(frozen=True)
class Area:
    """The tenant's side of the lookup (config.yaml `address_lookup`): where to ask, and the place
    names its callers use, so this module names no city."""

    geosearch_url: str
    census_suffix: str  # added to a Census query, the state: ", NY"
    boroughs: dict[str, str]  # what a caller says, lowercase, to the directory's borough name
    zip_boroughs: dict[str, str]  # a ZIP's first three digits to its borough, for the Census

    @classmethod
    def from_config(cls, cfg: dict) -> "Area":
        return cls(
            cfg["geosearch_url"],
            cfg.get("census_suffix", ""),
            {k.lower(): v for k, v in (cfg.get("boroughs") or {}).items()},
            {str(k): v for k, v in (cfg.get("zip_boroughs") or {}).items()},
        )

    def borough_for_zip(self, zip_code: str) -> str:
        return self.zip_boroughs.get(zip_code[:3], "")


def pick(candidates: Iterable[Place], house: str, street: str, hint: str = "",
         source: str = "", boroughs: dict[str, str] | None = None) -> Found:
    """The places that are this house on this street, one per ZIP, in the borough the caller
    named, or narrowed by the neighborhood they named when that leaves any."""
    want_house, want_street = house_key(house), street_match_key(street)
    exact = [
        p for p in candidates
        if house_key(p.house) == want_house and street_match_key(p.street) == want_street
    ]
    # "Brooklyn, DUMBO" or "DUMBO in Brooklyn": a borough among the words is kept to, and what's
    # left narrows by neighborhood.
    hint_words = " ".join(re.sub(r"[^a-z ]", " ", (hint or "").lower()).split())
    borough, rest = "", hint_words
    for said, name in sorted((boroughs or {}).items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(said)}\b", rest):
            borough, rest = name, " ".join(re.sub(rf"\b{re.escape(said)}\b", " ", rest).split())
            break
    rest = " ".join(w for w in rest.split() if w not in ("in", "the", "of", "near"))
    if borough:
        # A borough the caller named is kept to: the street isn't there if no match is.
        exact = [p for p in exact if p.borough == borough]
    if rest:
        # A neighborhood narrows only when it matches; the city's neighborhood names don't
        # always match the ones people use.
        exact = [p for p in exact if rest in p.area.lower()] or exact
    by_zip: dict[str, Place] = {}
    for place in exact:
        by_zip.setdefault(place.zip, place)
    places = tuple(by_zip.values())
    status = "none" if not places else "one" if len(places) == 1 else "several"
    return Found(status, places, source)


# ---- backends: each takes (http, house, street) and returns candidate places ------------------


def geosearch_places(features: list[dict]) -> list[Place]:
    """Places from GeoSearch features (also how the eval book's recorded answers are read)."""
    places = []
    for feature in features:
        p = feature.get("properties") or {}
        if not p.get("housenumber") or not p.get("postalcode"):
            continue
        places.append(
            Place(
                p["housenumber"],
                p.get("street", ""),
                p["postalcode"],
                p.get("borough", ""),
                p.get("neighbourhood", ""),
                str(((p.get("addendum") or {}).get("pad") or {}).get("bbl", "")),
            )
        )
    return places


def pluto_units(http: httpx.Client, url: str, bbl: str) -> int | None:
    """Residential units in the city's tax lot (MapPLUTO's unitsres): 9 at 199 East 4th Street.
    None when the lot isn't listed."""
    r = http.get(url, params={"$select": "unitsres", "$where": f"bbl={int(bbl)}"})
    r.raise_for_status()
    rows = r.json()
    if not rows or rows[0].get("unitsres") in (None, ""):
        return None
    return int(float(rows[0]["unitsres"]))


def geosearch(http: httpx.Client, url: str, house: str, street: str) -> list[Place]:
    r = http.get(url, params={"text": f"{house} {street}", "size": RESULTS})
    r.raise_for_status()
    return geosearch_places(r.json().get("features", []))


def census(http: httpx.Client, area: Area, house: str, street: str) -> list[Place]:
    r = http.get(
        CENSUS_URL,
        params={
            "address": f"{house} {street}{area.census_suffix}",
            "benchmark": "Public_AR_Current",
            "format": "json",
        },
    )
    r.raise_for_status()
    places = []
    for match in r.json().get("result", {}).get("addressMatches", []):
        parts = [s.strip() for s in match.get("matchedAddress", "").split(",")]
        zip_code = parts[-1] if parts else ""
        number_and_street = split_address(parts[0]) if parts else None
        borough = area.borough_for_zip(zip_code)
        if not number_and_street or not borough:
            continue  # outside the city
        places.append(Place(*number_and_street, zip_code, borough, parts[1].title()))
    return places


Backend = Callable[[str, str], list[Place]]


class Geocoder:
    """Lookups by house and street, asked early and kept. `backends` are (name, function) pairs
    tried in order until one answers; an empty answer is an answer (no such address), a failure
    or timeout moves on to the next."""

    def __init__(self, backends: list[tuple[str, Backend]], boroughs: dict[str, str] | None = None,
                 workers: int = 4, units: Callable[[str], int | None] | None = None):
        self.backends = backends
        self.boroughs = boroughs or {}
        # Residential units by borough-block-lot (pluto_units), when the tenant has a source.
        self.units = units
        self.unit_counts: dict[str, int | None] = {}
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="geocode")
        self.answers: OrderedDict[tuple[str, str], Future] = OrderedDict()
        # Reentrant: a lookup that fails at once runs its callback inside _future, under the lock.
        self.guard = threading.RLock()

    def _future(self, house: str, street: str) -> Future:
        key = (house_key(house), street_match_key(street))
        with self.guard:
            future = self.answers.get(key)
            if future is None:
                future = self.pool.submit(self._fetch, house, street)
                self.answers[key] = future
                future.add_done_callback(lambda f, k=key: self._forget_failure(k, f))
                while len(self.answers) > KEEP:
                    self.answers.popitem(last=False)
            return future

    def _forget_failure(self, key: tuple[str, str], future: Future) -> None:
        """A failed lookup isn't kept, so the next ask tries the backends again."""
        if future.exception() is not None:
            with self.guard:
                if self.answers.get(key) is future:
                    del self.answers[key]

    def _fetch(self, house: str, street: str) -> tuple[str, list[Place]]:
        for name, backend in self.backends:
            started = time.perf_counter()
            try:
                places = backend(house, street)
            except Exception as exc:  # noqa: BLE001 - any failure moves to the next backend
                log.info(json.dumps({"event": "geocode_failed", "backend": name,
                                     "error": type(exc).__name__}))
                continue
            ms = round((time.perf_counter() - started) * 1000)
            log.info(json.dumps({"event": "geocode", "backend": name, "ms": ms,
                                 "results": len(places)}))
            return name, places
        raise Unavailable("no geocoder answered")

    def prefetch(self, house: str, street: str) -> None:
        if house_key(house) and street_match_key(street):
            self._future(house, street)

    def prefetch_line(self, text: str) -> None:
        for house, street in addresses_in(text):
            self.prefetch(house, street)

    def units_at(self, place: Place, wait_s: float = REQUEST_TIMEOUT_S) -> int | None:
        """Residential units in the building, or None when unknown, slow or failed: the unit
        question then falls to the agent, as before."""
        if not self.units or not place.bbl:
            return None
        if place.bbl not in self.unit_counts:
            try:
                count = self.pool.submit(self.units, place.bbl).result(timeout=wait_s)
            except Exception:  # noqa: BLE001 - a missing count only means the agent asks
                return None
            self.unit_counts[place.bbl] = count
        return self.unit_counts[place.bbl]

    def find(self, house: str, street: str, hint: str = "", wait_s: float = FIND_WAIT_S) -> Found:
        """This house on this street, or `unavailable` when nothing answered within wait_s."""
        try:
            source, places = self._future(house, street).result(timeout=wait_s)
        except (FutureTimeout, Unavailable):
            return Found("unavailable")
        return pick(places, house, street, hint, source, self.boroughs)


def from_settings(name: str, cfg: dict | None) -> Geocoder | None:
    """The geocoder GEOCODER names for a tenant with an `address_lookup` section: `geosearch`
    (GeoSearch, then the Census on failure), or None."""
    if not name or name == "off" or not cfg:
        return None
    if name != "geosearch":
        raise ValueError(f"unknown GEOCODER {name!r}; use geosearch or off")
    area = Area.from_config(cfg)
    http = httpx.Client(timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": "summit-air-agent"})
    pluto = cfg.get("units_url")
    return Geocoder(
        [
            ("geosearch", lambda h, s: geosearch(http, area.geosearch_url, h, s)),
            ("census", lambda h, s: census(http, area, h, s)),
        ],
        area.boroughs,
        units=(lambda bbl: pluto_units(http, pluto, bbl)) if pluto else None,
    )


def place_json(place: Place) -> dict:
    return asdict(place)
