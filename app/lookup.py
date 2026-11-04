"""The richer customer lookup: everything a call needs about one customer in one round trip
(docs/tools.md, spec S-15a). Used by /calls/start and find_customer."""

import sqlite3
from datetime import datetime, timedelta

from .db import iso, parse_iso
from .fsm import FsmAdapter
from .tenant import Tenant
from .text import phone_display, phone_key

OPEN_JOB_STATUSES = ["Scheduled", "Dispatched", "InProgress", "Hold"]
RECENT_CALLS = 3
RECENT_NOTES = 10
LOCAL_CALL_DAYS = 30


def membership_summary(tenant: Tenant, memberships: list[dict]) -> dict | None:
    """Active beats expired beats cancelled, so a lapsed plan reads as expired, never as missing."""
    if not memberships:
        return None
    order = {"Active": 0, "Expired": 1, "Canceled": 2}
    latest_first = sorted(memberships, key=lambda m: m["to"], reverse=True)
    best = min(latest_first, key=lambda m: order.get(m["status"], 3))
    plan = tenant.membership_types.get(best["membershipTypeId"], {})
    return {
        "status": best["status"],
        "plan": plan.get("name"),
        "ends": best["to"],
        "perks": plan.get("perks", {}) if best["status"] == "Active" else {},
    }


def window_text(tenant: Tenant, start: str, end: str, now: datetime | None = None) -> dict:
    """An arrival window as said. Given `now`, `passed` says the window ended before it, so a visit
    still open after its window is never told to the caller as coming up (DECISIONS.md 083 note)."""
    local_start, local_end = tenant.local(parse_iso(start)), tenant.local(parse_iso(end))

    def hour(moment: datetime) -> str:
        return moment.strftime("%-I:%M").replace(":00", "")

    text = {
        "date": local_start.date().isoformat(),
        "weekday": local_start.strftime("%A"),
        "label": f"{hour(local_start)} to {hour(local_end)}",
    }
    if now is not None:
        text["passed"] = local_end <= now
    return text


def fsm_calls(
    fsm: FsmAdapter, customer_id: int, phones: list[str], since: datetime | None = None
) -> list[dict]:
    """The customer's calls in ServiceTitan's log, newest first. The call feed is filtered by phone
    number, so this reads each of the customer's numbers and keeps the calls ServiceTitan matched to
    this customer or to nobody."""
    seen, found = set(), []
    for number in dict.fromkeys(phones):
        for call in fsm.calls_from(number, since):
            customer = call["leadCall"].get("customer")
            if call["id"] in seen or (customer and customer["id"] != customer_id):
                continue
            seen.add(call["id"])
            found.append(call)
    return sorted(found, key=lambda c: c["leadCall"]["receivedOn"], reverse=True)


def call_line(call: dict) -> dict:
    lead_call = call["leadCall"]
    return {
        "at": lead_call["receivedOn"],
        "reason": (lead_call.get("reason") or {}).get("name", ""),
        "call_type": lead_call.get("callType"),
    }


def open_jobs(
    fsm: FsmAdapter,
    tenant: Tenant,
    customer_id: int,
    now: datetime,
    locations: list[dict] | None = None,
) -> list[dict]:
    """Each open job with its next appointment and, from the customer's locations when they are
    given, the street it is at, so the agent can tell which visit is at which address."""
    streets = {loc["id"]: loc["address"]["street"] for loc in locations or []}
    jobs = []
    for job in fsm.jobs(customer_id, OPEN_JOB_STATUSES):
        appointments = [a for a in fsm.appointments(job["id"]) if a["status"] != "Canceled"]
        next_appt = appointments[-1] if appointments else None
        jobs.append(
            {
                "job_id": job["id"],
                "job_type": tenant.job_type_codes.get(job["jobTypeId"]),
                "status": job["jobStatus"],
                "priority": job["priority"],
                "summary": job["summary"],
                "location_id": job["locationId"],
                "street": streets.get(job["locationId"], ""),
                "appointment_id": next_appt["id"] if next_appt else None,
                "appointment_status": next_appt["status"] if next_appt else None,
                "window": window_text(
                    tenant, next_appt["arrivalWindowStart"], next_appt["arrivalWindowEnd"], now
                )
                if next_appt
                else None,
            }
        )
    return jobs


def customer_bundle(
    fsm: FsmAdapter, tenant: Tenant, conn: sqlite3.Connection, customer: dict, now: datetime
) -> dict:
    customer_id = customer["id"]
    phones = [c for c in fsm.contacts(customer_id) if c["type"] in ("Phone", "MobilePhone")]
    done = [j for j in fsm.jobs(customer_id, ["Completed"]) if j.get("completedOn")]
    last_service = max((j["completedOn"] for j in done), default=None)
    calls = [
        call_line(c)
        for c in fsm_calls(fsm, customer_id, [p["value"] for p in phones])[:RECENT_CALLS]
    ]
    locations = fsm.locations(customer_id)
    keys = [phone_key(p["value"]) for p in phones]
    if keys:
        # Calls this agent handled are in our own records, not the FSM's call log.
        since = iso(now - timedelta(days=LOCAL_CALL_DAYS))
        rows = conn.execute(
            f"SELECT started_at, outcome FROM calls WHERE started_at >= ? AND caller_key IN "
            f"({','.join('?' * len(keys))}) AND ended_at IS NOT NULL ORDER BY started_at DESC",
            (since, *keys),
        ).fetchall()
        calls += [
            {"at": r["started_at"], "reason": r["outcome"] or "", "call_type": "Agent"}
            for r in rows
        ]
        calls = sorted(calls, key=lambda c: c["at"], reverse=True)[:RECENT_CALLS]
    return {
        "customer_id": customer_id,
        "name": customer["name"],
        "type": customer["type"],
        "residential": customer["type"] not in tenant.rejected_customer_types,
        "phones": [
            {
                "number": phone_display(p["value"]),
                "ending": phone_key(p["value"])[-4:],
                # Who the number belongs to (owner, a son who calls for his mother) is whatever the
                # office wrote in the contact's memo; ServiceTitan contacts have no role or name.
                "memo": p.get("memo") or "",
            }
            for p in phones
        ],
        "locations": [
            {
                "location_id": loc["id"],
                "street": loc["address"]["street"],
                "unit": loc["address"]["unit"],
                "city": loc["address"]["city"],
                "zip": loc["address"]["zip"],
                "in_service_area": loc["address"]["zip"] in tenant.service_area,
            }
            for loc in locations
        ],
        "membership": membership_summary(tenant, fsm.memberships(customer_id)),
        "open_jobs": open_jobs(fsm, tenant, customer_id, now, locations),
        "last_service_date": last_service[:10] if last_service else None,
        "recent_calls": calls,
        "notes": [
            {
                "text": n["text"],
                "at": n["createdOn"],
                "by": "agent" if n["createdById"] == tenant.fsm.get("agent_user_id") else "staff",
            }
            for n in fsm.notes(customer_id)[-RECENT_NOTES:]
        ],
        "equipment": [
            {
                "system": (e.get("type") or {}).get("name") or e["name"],
                "brand": e["manufacturer"],
                "install_year": int(e["installedOn"][:4]) if e.get("installedOn") else None,
            }
            for e in fsm.equipment(customer_id, [loc["id"] for loc in locations])
        ],
    }


def history(
    fsm: FsmAdapter, tenant: Tenant, customer_id: int, what: str, now: datetime, months_back: int
) -> list[dict]:
    """Older history for a customer already matched on the call, read-only."""
    since = now - timedelta(days=30 * months_back)
    if what == "jobs":
        return [
            {
                "job_id": j["id"],
                "job_type": tenant.job_type_codes.get(j["jobTypeId"]),
                "status": j["jobStatus"],
                "summary": j["summary"],
                "created": j["createdOn"][:10],
                "completed": (j["completedOn"] or "")[:10] or None,
            }
            for j in fsm.jobs(customer_id)
            if parse_iso(j["createdOn"]) >= since
        ]
    if what == "calls":
        numbers = [
            c["value"] for c in fsm.contacts(customer_id) if c["type"] in ("Phone", "MobilePhone")
        ]
        return [call_line(c) for c in fsm_calls(fsm, customer_id, numbers, since)]
    return [
        {
            "system": (e.get("type") or {}).get("name") or e["name"],
            "brand": e["manufacturer"],
            "model": e["model"],
            "installed": e["installedOn"],
        }
        for e in fsm.equipment(customer_id)
    ]
