"""Speaking rate per call (DECISIONS.md 053, docs/tuning.md "Speaking rate during a call").

The server decides the pace from the caller's lines and records; the voice worker only applies the
speed it is given. Four things change it:

- A request to slow down or speed up, one step each way. It holds for the rest of the call, carries
  to a call linked to this one, and is remembered for the caller's number, so their next call
  starts at it.
- A request to slow down for the address alone. Only the address in the next read-back sentence is
  spoken slower; the pace then goes back to what it was.
- "Sorry, what?" said twice: one step slower for the rest of this call.
- A caller who says they are elderly, or gives their own age as 65 or more: the older-adult speed,
  unless they already asked for a pace. Priority's older-adult line is 60 (DECISIONS.md 019); 65
  for pace is Jackson's own line (053).

Phrase lists, steps and bounds are the tenant's `pace` block (agent.yaml). Without one, pace is off
and the worker leaves the voice at its default.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .db import iso
from .safety import NEGATORS
from .text import has_phrase, words

SLOWER, FASTER, NORMAL = -1, 1, 0
# What Cartesia sonic-3 accepts for `speed` (SOURCE: the LiveKit Cartesia plugin's docs). Every
# pace the server stores or returns is kept inside it, whatever a tenant or a stored row says.
SPEED_MIN, SPEED_MAX = 0.6, 2.0
SPEED_KEYS = ("normal", "slowest", "fastest", "older_adult_speed")

# A caller's own age: "I'm 84", "I am 70 years old", "I'm in my seventies". "I'm 100 percent sure",
# "I'm 70 yards away" and "I'm 60 to 70 percent sure" are not ages.
OWN_AGE = re.compile(
    r"\bi(?:['’]?m| am)\s+(\d{2,3})\b"
    r"(?!\s*(?:percent|%|minutes?|mins?|hours?|feet|ft|yards?|yds?|degrees?|dollars?|bucks|miles?|"
    r"blocks?|floors?|stories|pounds?|lbs?|to\s+\d|or\s+\d|-\s*\d))"
)
OWN_DECADE = re.compile(r"\bi(?:['’]?m| am) in my (?:(\d)0s|(seventies|eighties|nineties))\b")
DECADES = {"seventies": 70, "eighties": 80, "nineties": 90}
# How far back a negator still applies to a request ("you don't really need to speak slower"); a
# word that starts a new thought ends the look back.
NEGATION_REACH = 4
CLAUSE_BREAKS = {"and", "but", "so", "because", "though", "also", "just", "well", "okay", "ok"}


def clamp(speed: float) -> float:
    return round(min(SPEED_MAX, max(SPEED_MIN, float(speed))), 2)


def clamped_config(cfg: dict | None) -> dict | None:
    """The tenant's pace block with every speed inside what the voice accepts (read at load)."""
    if cfg is None:
        return None
    return {**cfg, **{k: clamp(cfg[k]) for k in SPEED_KEYS if k in cfg}}


@dataclass
class Heard:
    """What one caller line asks of the pace."""

    request: int | None = None  # SLOWER, FASTER or NORMAL when the caller asked for a pace
    address_only: bool = False  # the request was for the address alone
    repeat_ask: bool = False  # "sorry, what?"
    older_adult: bool = False


def own_age(text: str) -> int | None:
    """The oldest age the caller gives as their own on this line, or None."""
    text = text.lower()
    ages = [int(m[1]) for m in OWN_AGE.finditer(text)]
    for m in OWN_DECADE.finditer(text):
        ages.append(int(m[1]) * 10 if m[1] else DECADES[m[2]])
    return max(ages) if ages else None


def _matches(text_words: str, phrase: str) -> list[int]:
    """Token positions where the phrase starts."""
    tokens, target = text_words.split(), words(phrase).split()
    return [
        i for i in range(len(tokens) - len(target) + 1) if tokens[i : i + len(target)] == target
    ]


def _negated(tokens: list[str], at: int) -> bool:
    before = []
    for token in reversed(tokens[max(0, at - NEGATION_REACH) : at]):
        if token in CLAUSE_BREAKS:
            break
        before.insert(0, token)
    return any(has_phrase(" " + " ".join(before) + " ", n) for n in NEGATORS)


def _said(text_words: str, phrases: list[str]) -> bool:
    """One of the phrases occurs at least once without a negator just before it."""
    tokens = text_words.split()
    return any(not _negated(tokens, i) for p in phrases for i in _matches(text_words, p))


def _imperative(text_words: str, phrase: str, cfg: dict) -> bool:
    """"Slow down" or "speed up" said to the agent, with no object after it: "can you slow down a
    little" counts, "the motor will slow down" and "speed up the install" don't."""
    tokens = text_words.split()
    size = len(phrase.split())
    for i in _matches(text_words, phrase):
        before = tokens[i - 1] if i else None
        after = tokens[i + size] if i + size < len(tokens) else None
        if (before is None or before in cfg["imperative_before"]) and (
            after is None or after in cfg["imperative_after"]
        ) and not _negated(tokens, i):
            return True
    return False


def _whole_line(text_words: str, lines: list[str], cfg: dict) -> bool:
    kept = [t for t in text_words.split() if t not in cfg["line_fillers"]]
    return " ".join(kept) in lines


def _request(text_words: str, cfg: dict) -> int | None:
    """A request about how the agent speaks, or None. A line that names the job ("send someone
    faster", "speed up the install") is about the work, not the voice."""
    if _said(text_words, cfg["job_words"]):
        return None
    if (
        _said(text_words, cfg["slower_phrases"])
        or _imperative(text_words, cfg["slower_imperative"], cfg)
        or _whole_line(text_words, cfg["slower_lines"], cfg)
    ):
        return SLOWER
    if (
        _said(text_words, cfg["faster_phrases"])
        or _imperative(text_words, cfg["faster_imperative"], cfg)
        or _whole_line(text_words, cfg["faster_lines"], cfg)
    ):
        return FASTER
    if _said(text_words, cfg["normal_phrases"]):
        return NORMAL
    return None


def _address_request(text_words: str, cfg: dict, after_read_back: bool) -> bool:
    """A request to hear the address slower: "say the address slower", or right after the
    read-back, a slow word with a repeat word ("can you say that again slower?")."""
    if _said(text_words, cfg["job_words"]):
        return False
    if _said(text_words, cfg["address_phrases"]):
        return True
    return (
        after_read_back
        and _said(text_words, cfg["slow_words"])
        and any(has_phrase(text_words, w) for w in cfg["repeat_words"])
    )


def hear(text: str, cfg: dict, after_read_back: bool) -> Heard:
    """Reads one caller line. `after_read_back` is True when the agent's read-back sentence is the
    last thing asked, so "can you say that again slower?" is about the address."""
    text_words = words(text)
    heard = Heard()
    if _address_request(text_words, cfg, after_read_back):
        heard.request, heard.address_only = SLOWER, True
    else:
        heard.request = _request(text_words, cfg)
        if heard.request is None:
            heard.repeat_ask = text_words.strip() in cfg["repeat_lines"] or any(
                has_phrase(text_words, p) for p in cfg["repeat_phrases"]
            )
    age = own_age(text)
    heard.older_adult = (age is not None and age >= cfg["older_adult_age"]) or _said(
        text_words, cfg["older_adult_phrases"]
    )
    return heard


def stepped(speed: float, direction: int, cfg: dict) -> float:
    """One step slower or faster, kept inside the tenant's bounds; NORMAL goes back to normal."""
    if direction == NORMAL:
        return clamp(cfg["normal"])
    moved = speed + direction * cfg["step"]
    return clamp(min(cfg["fastest"], max(cfg["slowest"], moved)))


# ---- the record ----------------------------------------------------------------------------


def _state(conn: sqlite3.Connection, call_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM call_pace WHERE call_id = ?", (call_id,)).fetchone()


def _log(conn, call_id: str, seq: int, speed: float, scope: str, source: str, now) -> None:
    conn.execute(
        "INSERT INTO pace_changes (call_id, seq, speed, scope, source, at) VALUES (?, ?, ?, ?, ?, ?)",
        (call_id, seq, speed, scope, source, iso(now)),
    )


def _remember(conn, tenant_slug: str, caller_key: str, speed: float, call_id: str, now, cfg):
    """The caller's own request is kept for their number; asking for normal clears it."""
    if len(caller_key) != 10:
        return
    if speed == cfg["normal"]:
        conn.execute(
            "DELETE FROM caller_pace WHERE tenant = ? AND phone_key = ?", (tenant_slug, caller_key)
        )
        return
    conn.execute(
        "INSERT INTO caller_pace (tenant, phone_key, speed, call_id, set_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (tenant, phone_key) DO UPDATE SET speed = excluded.speed, "
        "call_id = excluded.call_id, set_at = excluded.set_at",
        (tenant_slug, caller_key, speed, call_id, iso(now)),
    )


def remembered(conn: sqlite3.Connection, tenant_slug: str, caller_key: str) -> float | None:
    row = conn.execute(
        "SELECT speed FROM caller_pace WHERE tenant = ? AND phone_key = ?", (tenant_slug, caller_key)
    ).fetchone()
    return row["speed"] if row else None


def start(
    conn: sqlite3.Connection,
    cfg: dict | None,
    tenant_slug: str,
    call_id: str,
    caller_key: str,
    previous_id: str | None,
    now: datetime,
) -> dict | None:
    """The pace a call starts at: a linked call's, else the caller's remembered one, else normal.
    Runs inside start's transaction; calling it again for the same call changes nothing."""
    if cfg is None:
        return None
    if _state(conn, call_id) is None:
        speed, source, requested, older = cfg["normal"], "default", 0, 0
        previous = _state(conn, previous_id) if previous_id else None
        kept = remembered(conn, tenant_slug, caller_key) if caller_key else None
        if previous is not None and previous["speed"] != cfg["normal"]:
            speed, source = clamp(previous["speed"]), "linked"
            requested, older = previous["requested"], previous["older_adult"]
        elif kept is not None:
            # A stored row is kept inside what the voice accepts, whatever wrote it.
            speed, source, requested = clamp(kept), "record", 1
        conn.execute(
            "INSERT INTO call_pace (call_id, speed, source, requested, older_adult) "
            "VALUES (?, ?, ?, ?, ?)",
            (call_id, speed, source, requested, older),
        )
        if source != "default":
            _log(conn, call_id, 0, speed, "call", source, now)
    return current(conn, call_id)


def current(conn: sqlite3.Connection, call_id: str) -> dict | None:
    """What the worker applies: the speed for everything it says next."""
    state = _state(conn, call_id)
    return {"speed": state["speed"]} if state else None


def caller_line(
    conn: sqlite3.Connection,
    cfg: dict | None,
    call: sqlite3.Row,
    text: str,
    seq: int,
    now: datetime,
) -> list[str]:
    """Reads a caller line inside the turn's transaction and moves the pace if it should. Returns
    hints for the model: only a pace the caller asked for is mentioned, so the agent can say yes."""
    if cfg is None:
        return []
    state = _state(conn, call["id"])
    if state is None:
        return []
    asked_seq = call["readback_asked_seq"]
    # The read-back is the last thing asked: no caller line since it was returned. (Read here, not
    # through app.records, because the tenant module loads this one.)
    after_read_back = asked_seq is not None and not conn.execute(
        "SELECT 1 FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq > ?",
        (call["id"], asked_seq),
    ).fetchone()
    heard = hear(text, cfg, after_read_back)
    speed, changes, hints = state["speed"], {}, []
    # An older caller is spoken to more slowly unless they asked for a pace for the whole call; an
    # address request on the same line ("I'm 80, say the address slower") still slows the call.
    if heard.older_adult and not state["older_adult"]:
        changes["older_adult"] = 1
        whole_call_request = heard.request is not None and not heard.address_only
        older_speed = clamp(cfg["older_adult_speed"])
        if not state["requested"] and not whole_call_request and speed > older_speed:
            speed = older_speed
            changes.update(speed=speed, source="older_adult")
            _log(conn, call["id"], seq, speed, "call", "older_adult", now)
    if heard.address_only:
        address_speed = stepped(speed, SLOWER, cfg)
        changes["address_speed"] = address_speed
        _log(conn, call["id"], seq, address_speed, "address", "request", now)
        hints.append("pace_address")
    elif heard.request is not None:
        new = stepped(speed, heard.request, cfg)
        changes.update(requested=1, repeats=0)
        _remember(conn, call["tenant"], call["caller_key"], new, call["id"], now, cfg)
        if new != speed:
            changes.update(speed=new, source="request")
            _log(conn, call["id"], seq, new, "call", "request", now)
            hints.append("pace_slower" if new < speed else "pace_faster")
    elif heard.repeat_ask:
        repeats = state["repeats"] + 1
        changes["repeats"] = repeats
        if repeats >= cfg["repeats_before_slower"]:
            new = stepped(speed, SLOWER, cfg)
            changes["repeats"] = 0
            if new != speed:
                changes.update(speed=new, source="repeats")
                _log(conn, call["id"], seq, new, "call", "repeats", now)
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(
            f"UPDATE call_pace SET {sets} WHERE call_id = ?", (*changes.values(), call["id"])
        )
    return hints


def read_back_parts(
    conn: sqlite3.Connection, call_id: str, sentence: str, address: str
) -> list[dict] | None:
    """When the caller asked for the address slower, the read-back sentence in the parts the worker
    speaks: the address at the slower speed, the words around it at the call's pace. The request is
    used up, so the next read-back is at the call's pace again. None when nothing was asked."""
    state = _state(conn, call_id)
    if state is None or state["address_speed"] is None:
        return None
    at = sentence.find(address)
    if at < 0:
        return None
    before, after = sentence[:at].strip(), sentence[at + len(address) :]
    # The comma after the address goes with it, so each part ends where a speaker would pause.
    tail = re.match(r"^[,;:.]?", after)[0]
    parts = [
        {"text": before, "speed": state["speed"]},
        {"text": address + tail, "speed": state["address_speed"]},
        {"text": after[len(tail) :].strip(), "speed": state["speed"]},
    ]
    conn.execute("UPDATE call_pace SET address_speed = NULL WHERE call_id = ?", (call_id,))
    return [p for p in parts if p["text"]]
