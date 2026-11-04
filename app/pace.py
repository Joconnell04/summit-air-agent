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

from . import records
from .db import iso
from .text import has_phrase, words

SLOWER, FASTER, NORMAL = -1, 1, 0

# A caller's own age: "I'm 84", "I am 70 years old", "I'm in my seventies". "I'm 100 percent sure"
# and "I'm 70 minutes away" are not ages.
OWN_AGE = re.compile(
    r"\bi(?:['’]?m| am)\s+(\d{2,3})\b"
    r"(?!\s*(?:percent|%|minutes?|mins?|hours?|feet|ft|degrees?|dollars?|bucks|miles?|blocks?|"
    r"floors?|stories|pounds?|lbs?))"
)
OWN_DECADE = re.compile(r"\bi(?:['’]?m| am) in my (?:(\d)0s|(seventies|eighties|nineties))\b")
DECADES = {"seventies": 70, "eighties": 80, "nineties": 90}


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


def _any(text_words: str, phrases: list[str]) -> bool:
    return any(has_phrase(text_words, p) for p in phrases)


def hear(text: str, cfg: dict, after_read_back: bool) -> Heard:
    """Reads one caller line. `after_read_back` is True when the agent's read-back sentence is the
    last thing asked, so "can you say that again slower?" is about the address."""
    text_words = words(text)
    heard = Heard()
    slow_word = _any(text_words, cfg["slow_words"])
    about_address = _any(text_words, cfg["address_words"]) or (
        after_read_back and _any(text_words, cfg["repeat_words"])
    )
    if slow_word and about_address:
        heard.request, heard.address_only = SLOWER, True
    elif _any(text_words, cfg["slower_phrases"]):
        heard.request = SLOWER
    elif _any(text_words, cfg["faster_phrases"]):
        heard.request = FASTER
    elif _any(text_words, cfg["normal_phrases"]):
        heard.request = NORMAL
    elif text_words.strip() in cfg["repeat_lines"] or _any(text_words, cfg["repeat_phrases"]):
        heard.repeat_ask = True
    age = own_age(text)
    heard.older_adult = (age is not None and age >= cfg["older_adult_age"]) or _any(
        text_words, cfg["older_adult_phrases"]
    )
    return heard


def stepped(speed: float, direction: int, cfg: dict) -> float:
    """One step slower or faster, kept inside the tenant's bounds; NORMAL goes back to normal."""
    if direction == NORMAL:
        return cfg["normal"]
    moved = speed + direction * cfg["step"]
    return round(min(cfg["fastest"], max(cfg["slowest"], moved)), 2)


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
            speed, source = previous["speed"], "linked"
            requested, older = previous["requested"], previous["older_adult"]
        elif kept is not None:
            speed, source, requested = kept, "record", 1
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
    after_read_back = asked_seq is not None and not records.caller_turn_after(
        conn, call["id"], asked_seq
    )
    heard = hear(text, cfg, after_read_back)
    speed, changes, hints = state["speed"], {}, []
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
    if heard.older_adult and not state["older_adult"]:
        changes["older_adult"] = 1
        speed_now = changes.get("speed", speed)
        if not state["requested"] and heard.request is None and speed_now > cfg["older_adult_speed"]:
            changes.update(speed=cfg["older_adult_speed"], source="older_adult")
            _log(conn, call["id"], seq, cfg["older_adult_speed"], "call", "older_adult", now)
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
