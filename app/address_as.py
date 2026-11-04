"""What the caller asked to be called ("call me Dr. Patel", "it's Ms. Rivera"), read from their own
words on every caller line, so the agent uses it for the rest of the call, on a linked call, and
on the next call from a customer on file (a note on the customer record).

Only a form of address is kept: an optional title and one or two names. "Call me back" and "call
me at 5" are not names, and a name with a mocking or abusive word in it is refused, so the agent
never says it and it never reaches the record.
"""

import re
import sqlite3
from datetime import datetime

from . import records
from .db import iso, tx
from .text import words

# Titles a caller may ask for, as they are written for the voice.
TITLES = {
    "dr": "Dr.",
    "doctor": "Dr.",
    "mr": "Mr.",
    "mister": "Mr.",
    "mrs": "Mrs.",
    "missus": "Mrs.",
    "ms": "Ms.",
    "miss": "Miss",
    "mx": "Mx.",
    "professor": "Professor",
    "prof": "Professor",
    "reverend": "Reverend",
    "rev": "Reverend",
    "father": "Father",
    "pastor": "Pastor",
    "rabbi": "Rabbi",
    "imam": "Imam",
    "sister": "Sister",
    "judge": "Judge",
    "captain": "Captain",
    "officer": "Officer",
    "coach": "Coach",
}
# "Call me <word>" where the word is not a name: when, how, or a figure of speech.
NOT_NAMES = {
    "back", "later", "tomorrow", "today", "tonight", "again", "anytime", "whenever", "asap",
    "soon", "now", "at", "on", "in", "when", "after", "before", "around", "about", "if", "the",
    "a", "an", "your", "my", "this", "that", "it", "please", "first", "instead", "here", "there",
    "crazy", "old", "silly", "paranoid", "picky", "impatient", "cheap", "maybe",
    "so", "and", "but", "or", "with", "from", "to", "for", "by", "yes", "no", "okay", "ok",
    "calling", "speaking", "though", "thanks", "thank", "is", "im", "its", "i", "id", "ill",
    "ive", "want", "need", "like", "have", "got", "just", "thats", "dont", "can", "will", "would",
}
# Never used as a form of address: mocking honorifics, pet names and abuse. A name with any of
# these is refused whole.
REFUSED = {
    "master", "mistress", "majesty", "highness", "lord", "lady", "king", "queen", "prince",
    "princess", "god", "goddess", "emperor", "empress", "overlord", "sir", "madam", "boss",
    "daddy", "mommy", "mama", "papa", "baby", "babe", "honey", "sweetheart", "darling", "sexy",
    "hot", "stud", "idiot", "stupid", "dummy", "loser", "moron", "ass", "asshole", "bitch",
    "bastard", "dick", "shit", "fuck", "fucker", "cunt", "whore", "slut", "satan", "hitler",
    "robot", "batman", "superman", "voldemort",
}
# The words that ask for a form of address. "It's" and "this is" count only with a title, since
# "this is Maria" is an introduction, which the agent already uses.
ASKS = (
    ("any", re.compile(r" call me ")),
    ("any", re.compile(r" i go by ")),
    ("any", re.compile(r" (?:i prefer|id prefer|i like|id like) to be called ")),
    ("any", re.compile(r" (?:address me as|refer to me as) ")),
    ("titled", re.compile(r" (?:its|it is|this is|im|i am|my name is|the name is|name is) ")),
)
# Names after the trigger: a title and a surname ("Dr. Patel"), or up to two given names ("Jo
# Ann"). A clause ends the name, so "call me Bea, everyone does" is Bea.
MAX_NAMES = 2
MAX_NAMES_TITLED = 1
CLAUSE = re.compile(r"[,.;:!?]+")
NOTE = "Prefers to be called {name}."
NOTE_READ = re.compile(r"^Prefers to be called (.+)\.$")


def read(text: str) -> tuple[str, str] | None:
    """("ok", "Dr. Patel") for a form of address the caller asked for, ("refused", words) for one
    that is never used, or None when the line asks for none."""
    # "Dr." is a title, not the end of a clause.
    text = re.sub(r"\b(dr|mr|mrs|ms|mx|prof|rev)\.", r"\1", text or "", flags=re.IGNORECASE)
    for clause in CLAUSE.split(text):
        found = _read_clause(clause, text)
        if found:
            return found
    return None


def _read_clause(clause: str, text: str) -> tuple[str, str] | None:
    said = words(clause)
    # Every place a trigger starts, in order: "this is Anjali Patel, please call me Dr. Patel"
    # passes the introduction (no title) and takes what follows "call me".
    found = sorted(
        (m.start(), kind, said[m.end():])
        for kind, trigger in ASKS
        for m in trigger.finditer(said)
    )
    for _start, kind, tail in found:
        tokens = tail.split()
        title = TITLES.get(tokens[0]) if tokens else None
        if kind == "titled" and not title:
            continue
        # "Call me Your Majesty": a refused word among the first few is refused, whatever else.
        head = tokens[:3]
        if any(t in REFUSED for t in head):
            return "refused", " ".join(head)
        names = []
        most = MAX_NAMES_TITLED if title else MAX_NAMES
        for token in tokens[1 if title else 0:]:
            if token in NOT_NAMES or token.isdigit() or len(names) == most:
                break
            # A second name only when the transcript wrote it as one ("Jo Ann"), so "call me
            # bea everyone does" is Bea.
            if names and not _as_written(text, token, keep_case=True)[:1].isupper():
                break
            names.append(token)
        if not names:
            continue
        written = [_as_written(text, n) for n in names]
        return "ok", " ".join(([title] if title else []) + written)
    return None


def _as_written(text: str, token: str, keep_case: bool = False) -> str:
    """The name as the transcript wrote it ("McKenzie"), or capitalized when it came lowercase."""
    match = re.search(rf"\b({re.escape(token)})\b", text, re.IGNORECASE)
    name = match.group(1) if match else token
    return name if keep_case or name[:1].isupper() else name.capitalize()


def record(conn: sqlite3.Connection, call_id: str, text: str, seq: int,
           now: datetime) -> str | None:
    """Store what the caller asked to be called on this line, inside the turn's transaction.
    Returns the hint for the agent: `preferred_name` when a new name was kept,
    `preferred_name_refused` when one was refused, or None."""
    read_back = read(text)
    if read_back is None:
        return None
    verdict, name = read_back
    if verdict == "refused":
        return "preferred_name_refused"
    if current(conn, call_id) == name:
        return None
    conn.execute(
        "INSERT INTO call_names (call_id, name, seq, source, at) VALUES (?, ?, ?, 'caller', ?) "
        "ON CONFLICT (call_id) DO UPDATE SET name = excluded.name, seq = excluded.seq, "
        "source = excluded.source, at = excluded.at, saved_at = NULL",
        (call_id, name, seq, iso(now)),
    )
    return "preferred_name"


def current(conn: sqlite3.Connection, call_id: str) -> str | None:
    """The name to use on this call: asked for on it, or on a call it is linked to."""
    for linked in records.thread(conn, call_id):
        row = conn.execute("SELECT name FROM call_names WHERE call_id = ?", (linked,)).fetchone()
        if row:
            return row["name"]
    return None


def from_notes(customers: list[dict]) -> str | None:
    """The name on file: the latest preferred-name note of the one residential customer matched."""
    residential = [c for c in customers if c.get("residential")]
    if len(residential) != 1:
        return None
    for note in reversed(residential[0].get("notes") or []):
        match = NOTE_READ.match(note.get("text", ""))
        if match:
            return match.group(1)
    return None


def save(conn: sqlite3.Connection, fsm, call: sqlite3.Row, now: datetime) -> bool:
    """After the call, put the name on the customer record as a note, so the next call starts
    with it: the customer booked on this call, else the one customer matched to it. Nothing is
    written when no customer is known, or the record already says so."""
    row = conn.execute(
        "SELECT name FROM call_names WHERE call_id = ? AND saved_at IS NULL", (call["id"],)
    ).fetchone()
    if row is None:
        return False
    booked = conn.execute(
        "SELECT customer_id FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'booked' "
        "AND customer_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        (call["id"],),
    ).fetchone()
    customer_id = booked["customer_id"] if booked else None
    if customer_id is None:
        scoped = records.scoped_customers(conn, call["id"])
        if len(scoped) != 1:
            return False
        customer_id = next(iter(scoped))
    text = NOTE.format(name=row["name"])
    latest = [n["text"] for n in fsm.notes(customer_id) if NOTE_READ.match(n["text"] or "")]
    if not latest or latest[-1] != text:
        fsm.add_note(f"{call['id']}:preferred_name", customer_id, text)
    with tx(conn):
        conn.execute("UPDATE call_names SET saved_at = ? WHERE call_id = ?", (iso(now), call["id"]))
    return True
