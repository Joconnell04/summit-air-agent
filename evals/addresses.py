"""The address lookup in a scratch world: answers recorded from NYC Planning's GeoSearch, played
back, so an eval never calls out and gives the same answer every run.

Only the addresses in `addresses.yaml` are known. Any other house and street is answered as an
outage, so the agent asks for the ZIP as it did before the lookup existed. That keeps the older
scenarios as they were: their addresses are made up and many don't match a real ZIP ("620 Bedford
Ave, 11235" is 11249 in the city's directory), which a live lookup would question.

To add an address: `uv run python -m evals.addresses "248 Duffield St" ...`, which asks the live
service and writes what it found.
"""

import sys
from pathlib import Path

import httpx
import yaml

from app import geocode

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "evals" / "addresses.yaml"


def key(house: str, street: str) -> str:
    return f"{geocode.house_key(house)}|{geocode.street_match_key(street)}"


def load(path: Path = BOOK) -> dict[str, list[geocode.Place]]:
    raw = yaml.safe_load(path.read_text()) or {}
    return {k: [geocode.Place(**p) for p in entry["places"]] for k, entry in raw.items()}


def geocoder(boroughs: dict[str, str], path: Path = BOOK) -> geocode.Geocoder:
    book = load(path)

    def answer(house: str, street: str) -> list[geocode.Place]:
        found = book.get(key(house, street))
        if found is None:
            raise geocode.Unavailable(f"{house} {street} is not in evals/addresses.yaml")
        return list(found)

    return geocode.Geocoder([("recorded", answer)], boroughs)


def record(queries: list[str], url: str, path: Path = BOOK) -> None:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    http = httpx.Client(timeout=10, headers={"User-Agent": "summit-air-agent"})
    for query in queries:
        split = geocode.split_address(query)
        if not split:
            raise SystemExit(f"no house number in {query!r}")
        places = geocode.geosearch(http, url, *split)
        raw[key(*split)] = {
            "query": query,
            "places": [geocode.place_json(p) for p in places],
        }
        print(f"{query}: {len(places)} results")
    path.write_text(
        "# Recorded GeoSearch answers for the eval world (evals/addresses.py). Regenerate an entry\n"
        "# with: uv run python -m evals.addresses \"<house> <street>\"\n"
        + yaml.safe_dump(raw, sort_keys=True, allow_unicode=True)
    )


if __name__ == "__main__":
    config = yaml.safe_load((ROOT / "tenants" / "summit-air" / "config.yaml").read_text())
    record(sys.argv[1:], config["address_lookup"]["geosearch_url"])
