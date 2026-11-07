"""Put a few scripted commercial calls through the running tool server, so the owner view's Leads
list has today's commercial callers in it for a demo. Each call is what a voice layer would send:
the call start, the caller's words, record_lead and the call end, so the lead is real and reaches
ServiceTitan the same way. Run on the server from the app checkout, after any live world reset:

    uv run --env-file ../.env python -m scripts.seed_commercial_leads

A call already in the tool server's database (its id carries the day) is skipped, so a second
run the same day adds nothing.
The numbers sit outside the seed's pool of random customer numbers, so none matches a customer.
"""

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app import db
from app.settings import Settings

DIALED = "(516) 212-9051"
NEW_YORK = ZoneInfo("America/New_York")

CALLS = [
    {
        "caller": "(718) 555-0921",
        "said": "Hi, I manage a dental office in Park Slope. We have two rooftop units and we're "
        "looking for someone to take on a maintenance contract.",
        "contact_name": "Dana Ruiz",
        "company": "Ruiz Family Dental",
        "need": "Maintenance contract for two rooftop units at a dental office in Park Slope",
    },
    {
        "caller": "(347) 555-0934",
        "said": "I run a gym on Atlantic Avenue. The AC keeps short-cycling and the members are "
        "complaining. Can someone quote a new rooftop unit?",
        "contact_name": "Marco Lin",
        "company": "Atlantic Ave Fitness",
        "need": "AC short-cycling at a gym; wants a service visit and a quote for a new rooftop unit",
    },
    {
        "caller": "(929) 555-0947",
        "said": "We're a coffee roaster in Greenpoint and the roasting room gets way too hot. "
        "We need cooling for about 1,200 square feet.",
        "contact_name": "Noor Haddad",
        "company": "Greenpoint Coffee Roasters",
        "need": "Cooling for a 1,200 square foot roasting room; wants an estimate",
    },
    {
        "caller": "(718) 555-0958",
        "said": "I'm the facilities lead at a school in Bay Ridge. I'd like someone to look at our "
        "boiler before the winter break and talk about a service agreement.",
        "contact_name": "Ellen Ford",
        "company": "Shore Road Learning Center",
        "need": "Boiler check before winter break at a school, and a service agreement",
    },
]


def seed(http, db_path: Path, now: datetime) -> list[str]:
    """Run the calls through `http` (a client on the tool server); returns the ids it made."""
    conn = db.connect(db_path)
    try:
        done = {r["id"] for r in conn.execute("SELECT id FROM calls WHERE id LIKE 'seed_commercial_%'")}
    finally:
        conn.close()
    made = []
    for n, call in enumerate(CALLS, start=1):
        call_id = f"seed_commercial_{now:%Y%m%d}_{n}"
        if call_id in done:
            print(f"{call_id}: already there")
            continue
        http.post("/calls/start", json={
            "call_id": call_id, "caller_id": call["caller"], "dialed_number": DIALED,
            "direction": "inbound",
        })
        http.post(f"/calls/{call_id}/turn", json={"speaker": "caller", "text": call["said"]})
        lead = http.post("/tools/record_lead", json={
            "call_id": call_id, "kind": "commercial",
            "details": {key: call[key] for key in ("contact_name", "company", "need")},
        }).json()
        if lead["say"]:
            http.post(f"/calls/{call_id}/turn", json={"speaker": "agent", "text": lead["say"]})
        http.post(f"/calls/{call_id}/end", json={"reason": "agent_end"})
        print(f"{call_id}: {'lead stored' if lead['ok'] else lead}")
        made.append(call_id)
    return made


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8100", help="the tool server")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as http:
        seed(http, Settings().db_path, datetime.now(NEW_YORK))


if __name__ == "__main__":
    main()
