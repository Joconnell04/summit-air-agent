"""A caller who asks the agent to wait (DECISIONS.md 081).

"Hold on" or "give me a sec" puts the call on a patient hold: the agent says a short yes and asks
nothing, and the voice worker counts the silence, saying the tenant's
check-in line every couple of minutes (voice/dead_air.py) and, after the limit, its goodbye before hanging up with the
end reason `stepped_away`. A call back from the same number then opens by saying so.

Only a sentence that starts with the request counts ("hold on", "sorry, one sec"); "it runs for
one minute then shuts off" is a symptom. While the call is on hold, a caller line counts as the
caller coming back only when it is said to the agent: "I'm still here", "I'm back", "one more sec",
"I can't find it", any question not put to someone else by name, an answer with a number ("it says 48"), or another
request to wait. Anything else ("okay kids, come on", "honey, where's the last bill?", a TV in the
room) is an aside: it is kept
on the record and still goes through the safety check, but the agent doesn't answer it and the
silence keeps counting. The worker only ever restarts the count on a line the server calls a reply.
"""

import re
import sqlite3
from datetime import datetime

from .db import iso
from .text import has_phrase, words

HOLDS = (
    "hold on", "hang on", "give me a sec", "give me a second", "give me a minute",
    "give me a moment", "give me one sec", "give me one second", "give me two minutes",
    "one sec", "one second", "one moment", "one minute please", "just a sec", "just a second",
    "just a minute", "just a moment", "wait a sec", "wait a second", "wait a minute",
    "bear with me", "be right back", "let me go check", "let me go look", "let me go grab",
    "let me go get", "let me go find", "hold please", "can you hold", "can you wait",
)
# Words a request to wait can start after: "oh, hang on", "sorry, one sec".
LEAD_WORDS = {"oh", "um", "uh", "okay", "ok", "sorry", "yeah", "yes", "hey", "so", "actually",
              "wait", "hi", "hello", "well", "alright", "sure", "and"}
# Words that say the caller is talking to the agent again. A plain "okay" or "hey" isn't one:
# "okay kids, come on" is said to someone else.
RETURNS = (
    "still here", "right here", "im here", "i am here", "im back", "i am back", "back now", "one more sec",
    "one more second", "one more minute", "still looking", "im looking", "looking for it",
    "cant find it", "i cant find", "almost done", "found it", "got it", "here it is", "i have it",
    "sorry about that", "sorry for the wait", "thanks for waiting", "thank you for waiting",
    "you there", "are you there", "you still there",
)
# The start of a line that answers what the agent asked, with a number in it ("it says 48").
ANSWER_STARTS = ("it says", "it is", "its", "theres", "there is", "i have", "the number", "the model")
# A line that opens by naming someone else is said to them: "honey, where did you put it?"
OTHERS = {"honey", "hon", "babe", "baby", "sweetie", "sweetheart", "dear", "kids", "guys", "mom",
          "dad", "buddy", "bud"}
QUESTION_WORDS = {"what", "whats", "which", "when", "where", "who", "how", "why", "can", "could",
                  "do", "did", "does", "is", "are", "was", "will", "would", "should"}
NUMBER = re.compile(r"\d")
SENTENCES = re.compile(r"(?<=[.!?])\s+|\n")


def _starts_with_hold(sentence: str) -> bool:
    tokens = words(sentence).split()
    while tokens and tokens[0] in LEAD_WORDS:
        tokens.pop(0)
    rest = " " + " ".join(tokens) + " "
    return any(rest.startswith(f" {h} ") for h in HOLDS)


def asks_to_hold(text: str) -> bool:
    """A request to the agent to wait: a sentence that starts with one ("hold on", "one minute
    please", "sorry, give me a sec"). "It runs for about one minute then shuts off" is a symptom."""
    return any(_starts_with_hold(s) for s in SENTENCES.split(text) if s.strip())


def is_reply(text: str) -> bool:
    """While on hold: the line is said to the agent, so the caller is back. Talk to someone else
    in the room ("okay kids, come on", "hey, get off that") isn't."""
    said = words(text)
    if asks_to_hold(text) or any(has_phrase(said, r) for r in RETURNS):
        return True
    # A question is to the agent unless it opens by naming someone else: "Hello?", "can you
    # still hear me?", and "Um, what was the question?", which slate call 2 (Retell
    # call_3cd4c8065f26d3de686f7a1e002) answered with "Take your time." as an aside.
    first = [w for w in said.split() if w not in LEAD_WORDS][:1]
    to_someone_else = bool(first) and first[0] in OTHERS
    # Speech-to-text and the text harness can drop the question mark: a line that starts with a
    # question word asks too ("um what was the question").
    if not to_someone_else and ("?" in text or (first and first[0] in QUESTION_WORDS)):
        return True
    if said.startswith(" it says "):
        return True
    # A line that opens with a number answers the question asked before the wait: "215 Mott
    # Street, Unit 4B." after "One second." (socket call call_7db469c659f142130bc192143e2 stayed
    # silent for two minutes).
    if first and NUMBER.match(first[0]):
        return True
    return bool(NUMBER.search(text)) and any(said.startswith(f" {s} ") for s in ANSWER_STARTS)


def _answers_too(text: str) -> bool:
    """A hold request with the answer in the same breath ("Hold on, let me grab my mail. It's 88
    Atlantic Avenue."): the caller is back already. Treated as a hold, their next answer was taken
    as said to someone else and Brian stayed silent (phone checks b2 and t1 hold-on, 6 November)."""
    for sentence in SENTENCES.split(text):
        if not sentence.strip() or _starts_with_hold(sentence):
            continue
        if NUMBER.search(sentence) or len(words(sentence).split()) >= 4:
            return True
    return False


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
    if asks_to_hold(text) and not on_safety and not _answers_too(text):
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
