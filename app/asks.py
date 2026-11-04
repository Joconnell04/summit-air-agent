"""What a caller asks for beyond the visit itself, read by the server on every caller line: work
the company doesn't do, and a technician by name (DECISIONS.md 084 and 085)."""

import json
import re
import sqlite3
from datetime import datetime

from . import records
from .db import iso
from .tenant import Tenant
from .text import has_phrase, words

# Words that turn a mention around: "not the sink, the heat", "the fridge is fine".
NEGATORS = ("not", "no", "dont", "isnt", "nothing wrong with", "not about")
FINE_AFTER = ("is fine", "is ok", "is okay", "works", "is working", "are fine")


def not_offered(text: str, services: dict) -> list[str]:
    """The kinds of work in this line that the company doesn't do, in the order the tenant lists
    them. A kind the tenant doesn't list as not offered is never declined."""
    kinds = services.get("kinds", {})
    line = words(text)
    found = []
    for kind in services.get("not_offered", {}):
        if any(_asked(line, phrase) for phrase in kinds.get(kind, [])):
            found.append(kind)
    return found


def _asked(line: str, phrase: str) -> bool:
    target = words(phrase)
    start = line.find(target)
    while start >= 0:
        before = line[: start + 1].split()[-3:]
        after = line[start + len(target) - 1 :]
        negated = any(n in before for n in NEGATORS) or any(
            after.startswith(f" {f} ") for f in FINE_AFTER
        )
        if not negated:
            return True
        start = line.find(target, start + 1)
    return False


def decline_not_offered(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, text: str, now: datetime
) -> list[str]:
    """For each kind of work in the line the company doesn't do, and not already declined on this
    call: keep a lead for the office (the jobs loop sends it to the FSM) and return the sentence the
    agent says. Runs inside the turn's transaction."""
    said = []
    for kind in not_offered(text, tenant.services):
        key = f"{call['id']}:not_offered:{kind}"
        if conn.execute("SELECT 1 FROM leads WHERE fsm_key = ?", (key,)).fetchone():
            continue
        spec = tenant.services["not_offered"][kind]
        scoped = records.scoped_customers(conn, call["id"])
        summary = f"Asked for {spec['what']}, which we don't do; suggested {spec['suggest']}"
        lead = {
            "callReasonId": tenant.fsm["lead_call_reason_ids"]["not_offered"],
            "customerId": next(iter(scoped)) if len(scoped) == 1 else None,
            "leadCustomerName": "Caller",
            "leadPhone": call["caller_id"] if len(call["caller_key"]) == 10 else None,
            "summary": f"{summary}. Caller: {' '.join(text.split())[:200]}",
            "campaignId": tenant.fsm["campaign_id"],
        }
        conn.execute(
            """INSERT INTO leads (call_id, kind, summary, status, fsm_key, request_json, created_at)
               VALUES (?, 'not_offered', ?, 'queued', ?, ?, ?)""",
            (call["id"], summary, key, json.dumps(lead), iso(now)),
        )
        said.append(tenant.line("not_offered", what=spec["what"], suggest=spec["suggest"]))
    return said


# A technician asked for by name: "can I get Mike again", "send Mike back out", "is Mike
# available". Speech-to-text may write the name in lower case, so a word in the name's place counts
# unless it is a common word ("can you get someone out", "get it working again").
NAME = r"([A-Za-z][a-z]+)"
NAMED = (
    re.compile(
        rf"\b(?i:get|send|have|want|request|ask for|bring back|book)\s+{NAME}\s+"
        r"(?i:again|back|out|this time|to come|for this|if (?:he|she|they)|please)\b"
    ),
    re.compile(rf"\b(?i:is|was|can|could)\s+{NAME}\s+(?i:available|free|working|come|do it)\b"),
)
# The same technician as last time, with no name: still a request to pass on.
SAME_TECH = ("same technician", "same tech", "same guy", "same person", "same man", "same woman",
             "same lady", "whoever came last time", "the one who came last time")
NOT_NAMES = {
    "summit", "someone", "somebody", "anyone", "anybody", "everyone", "it", "you", "me", "them",
    "him", "her", "us", "that", "this", "one", "the", "a", "an", "power", "help", "things", "everything", "there", "here", "now", "today", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "back", "out",
    "they", "he", "she", "we", "i", "my", "your", "tech", "technician", "guy", "unit", "system",
    "service", "anything", "something",
}


def technician_asked(text: str, equipment: frozenset[str] = frozenset()) -> str | None:
    """The technician the caller asks for, by name, or "the same technician as last time"; None
    when the line asks for nobody. `equipment` is the trade's words for its systems, so "get the
    heat back" names nobody."""
    for pattern in NAMED:
        for match in pattern.finditer(text or ""):
            word = match.group(1).lower()
            if word not in NOT_NAMES and word not in equipment:
                return match.group(1).capitalize()
    line = words(text)
    if any(has_phrase(line, p) for p in SAME_TECH):
        return "the same technician as last time"
    return None


def note_technician(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, text: str, now: datetime
) -> str | None:
    """A request for a technician becomes a note for the dispatcher on the job, once per name per
    call. The server never assigns by name; the note says so. Returns the name noted, or None."""
    asked = technician_asked(text, tenant.equipment_words)
    if not asked:
        return None
    note = f"Caller asked for {asked}. Not promised; dispatcher to decide."
    exists = conn.execute(
        "SELECT 1 FROM notes WHERE call_id = ? AND attach_to = 'job' AND text = ?",
        (call["id"], note),
    ).fetchone()
    if exists:
        return None
    conn.execute(
        "INSERT INTO notes (call_id, attach_to, customer_id, fsm_note_id, text, created_at) "
        "VALUES (?, 'job', NULL, NULL, ?, ?)",
        (call["id"], note, iso(now)),
    )
    return asked
