"""A caller who asks the agent to wait (DECISIONS.md 081).

"Hold on" or "give me a sec" puts the call on a patient hold: the agent says a short yes and asks
nothing, and the voice worker counts the silence, saying the tenant's
check-in line every couple of minutes (voice/dead_air.py) and, after the limit, its goodbye before hanging up with the
end reason `stepped_away`. A call back from the same number then opens by saying so.

While the call is on hold, a caller line counts as the caller coming back only when it sounds said
to the agent: a word like "okay", "I'm back" or "hello", a yes or no, a number, or another request
to wait. Anything else ("honey, where's the last bill?", a TV in the room) is an aside: it is kept
on the record and still goes through the safety check, but the agent doesn't answer it and the
silence keeps counting. The worker only ever restarts the count on a line the server calls a reply.
"""

import re
import sqlite3
from datetime import datetime

from .db import iso
from .text import has_phrase, words

HOLDS = (
    "hold on", "hang on", "hold on a sec", "give me a sec", "give me a second", "give me a minute",
    "give me a moment", "give me one sec", "give me one second", "give me two minutes",
    "one sec", "one second", "one moment", "one minute", "just a sec", "just a second",
    "just a minute", "just a moment", "wait a sec", "wait a second", "wait a minute",
    "bear with me", "be right back", "let me go check", "let me go look", "let me go grab",
    "let me go get", "let me go find", "hold please", "can you hold", "can you wait",
)
# Words that say the caller is talking to the agent again.
RETURNS = (
    "okay", "ok", "alright", "all right", "im back", "i am back", "back now", "im here",
    "i am here", "still here", "right here", "one more sec", "one more second", "one more minute",
    "looking", "still looking", "i cant find it", "cant find it", "almost done", "sorry", "hello",
    "hi", "hey", "you there", "still there", "are you there", "thanks", "thank you", "found it",
    "got it", "here it is", "i have it", "yes", "yeah", "yep", "nope",
)
# The start of a line that answers what the agent asked ("it says 48", "it's a Carrier").
ANSWER_STARTS = ("it says", "it is", "its", "theres", "there is", "i have", "the number", "the model")
NUMBER = re.compile(r"\d")


def asks_to_hold(text: str) -> bool:
    said = words(text)
    return any(has_phrase(said, h) for h in HOLDS)


def is_reply(text: str) -> bool:
    """While on hold: the line sounds said to the agent, so the caller is back."""
    said = words(text)
    if asks_to_hold(text) or NUMBER.search(text):
        return True
    if any(has_phrase(said, r) for r in RETURNS):
        return True
    return any(said.startswith(f" {s} ") for s in ANSWER_STARTS)


def holding(conn: sqlite3.Connection, call_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM holds WHERE call_id = ? AND back_seq IS NULL", (call_id,)
    ).fetchone() is not None


def caller_line(
    conn: sqlite3.Connection, call_id: str, text: str, seq: int, now: datetime, on_safety: bool
) -> str | None:
    """The hold state after this caller line: `holding` (asked to wait), `back` (came back from a
    hold), `aside` (said on hold, not to the agent), or None (no hold). Inside the turn's
    transaction. A safety line always counts as a reply, so the agent answers it."""
    was_holding = holding(conn, call_id)
    if asks_to_hold(text) and not on_safety:
        if not was_holding:
            conn.execute(
                "INSERT INTO holds (call_id, seq, at) VALUES (?, ?, ?)", (call_id, seq, iso(now))
            )
        return "holding"
    if not was_holding:
        return None
    if on_safety or is_reply(text):
        conn.execute(
            "UPDATE holds SET back_seq = ? WHERE call_id = ? AND back_seq IS NULL", (seq, call_id)
        )
        return "back"
    return "aside"
