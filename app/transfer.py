"""Warm transfer to a person (C-11). The server decides whether a flagged call goes to a person now
and to whom; the voice layer only dials, says the server's words, and reports what happened.

A call goes to a person only when all of these hold:
- the tenant has a transfer section and a number set for the role, in the server's environment,
  and the owner's warm transfer setting was on when the call started (app/owner_settings.py);
- it isn't a safety call (the lines tell the caller to leave and call 911) or a commercial one;
- during office hours, the caller asked for a person on this call (their own words, matched here,
  not a flag the model sets); after hours, the server's own priority and after-hours cutoffs say
  the on-call technician is needed tonight (journey 11, `tools.needs_on_call_tonight`), never the
  priority the model put on the flag;
- the flag is one a person handles, and no transfer was tried on this call yet.

The flag and its callback are stored before anyone is dialed, so a transfer nobody answers falls
back to exactly what happens without one.

A caller who demands a person (asks insistently, asks more than once, or asks while angry) gets a
cold handoff instead, decided on their own turn rather than after a flag the model raises: the
agent says one line and the voice layer sends the call straight to the role's number with a SIP
transfer, with no summary and no key press. The office takes it in office hours and the on-call
technician after hours. On a gas or carbon monoxide call it waits until the fixed lines have been
said, since those already tell the caller to get outside; a commercial call never gets one. The
flag and a callback are stored first, so a failed transfer or an unset number leaves the caller
with the stored callback line, never silence.
"""

import json
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime

from . import owner_settings, records
from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .tenant import Tenant
from .text import has_phrase, phone_spoken, words

# A caller asking for a person, rather than asking whether they are talking to one: "can I talk
# to a real person" counts, "are you a real person" doesn't.
ASK_VERBS = (
    "speak to",
    "speak with",
    "talk to",
    "talk with",
    "connect me to",
    "connect me with",
    "transfer me to",
    "put me through to",
    # Jackson's call retell_call_480748620a2ad8cec45d7c26c71: "can you route me to a human?" was
    # refused as not asked.
    "route me to",
    "patch me through to",
    "send me to",
    "pass me to",
)
# Words for a person that can't be a technician, so "get me a manager" is a request for a person and
# "get me someone out here today" is not.
PEOPLE = (
    "a person",
    "a real person",
    "a live person",
    "an actual person",
    "a human",
    "a human being",
    "a real human",
    "a live agent",
    "a real agent",
    "a representative",
    "an operator",
    "a manager",
    "a supervisor",
    "your manager",
    "the manager",
    "the owner",
)
ASK_WHO = PEOPLE + ("someone", "somebody", "the office")
ASKED_PHRASES = (
    tuple(f"{verb} {who}" for verb in ASK_VERBS for who in ASK_WHO)
    + tuple(f"{verb} {who}" for verb in ("get me", "give me", "i want", "i need", "can i get",
                                          "could i get") for who in PEOPLE)
    + ("transfer me", "put me through", "connect me", "route me", "patch me through")
)
# What may come before an ask in the same sentence for it to be a request. "I need to talk to
# someone" is one; "last time I talked to someone it took a week" and "my landlord said to talk to
# someone" are a mention, not a request (tightened for the cold handoff).
REQUEST_OPENERS = (
    "can i",
    "could i",
    "may i",
    "can you",
    "could you",
    "would you",
    "cant you",
    "i want to",
    "i wanna",
    "i would like to",
    "id like to",
    "i need to",
    "i have to",
    "let me",
    "i need you to",
    "i want you to",
    "i said",
    "i told you",
)
FILLERS = {
    "just", "please", "now", "ok", "okay", "look", "listen", "no", "yes", "yeah", "so", "and",
    "um", "uh", "hey", "then", "well", "sir", "maam", "can", "you", "honestly", "seriously",
}
# An instruction to put the caller through is a request wherever it falls in the line, since
# speech-to-text often drops the full stop before it ("it died again put me through to a person"),
# unless someone else is doing it ("last time they put me through to someone").
INSTRUCTIONS = ("put me through", "transfer me", "connect me", "route me", "patch me through",
                "get me", "give me", "let me")
NOT_THE_CALLER = {
    "they", "he", "she", "who", "we", "it", "which", "that", "never", "didnt", "dont", "wont",
    "cant", "couldnt", "wouldnt", "to", "hed", "shed", "theyd", "wed", "theyll", "youll",
}
# A request said insistently, in the same turn.
INSISTENT = (
    "right now",
    "immediately",
    "right away",
    "this minute",
    "this second",
    "i said",
    "i told you",
    "already told you",
    "already asked",
    "i asked for",
    "stop asking",
    "no more questions",
    "not a robot",
    "not a machine",
    "not a computer",
)
# Anger at the call, in the request's own line or the one just before it, turns a request into a
# demand. These are about the call or the company whatever follows them.
ANGRY = (
    "the hell",
    "bullshit",
    "ridiculous",
    "unacceptable",
    "sick of",
    "fed up",
    "pissed",
    "furious",
    "waste of time",
    "screw this",
    "this is a joke",
)
# Swearing counts too, but not when it is about the equipment ("the damn thing is dead again"):
# swearing at a broken system isn't anger at the call. The trade names its own equipment words
# (trades/<trade>/agent.yaml `equipment_words`).
SWEARING = ("damn", "dammit", "shit", "fuck", "fucking", "freaking", "crap", "crappy", "garbage",
            "useless", "stupid")
EQUIPMENT = {"thing", "system", "unit"}
ROLE_NAMES = {"office": "office", "on_call": "on-call technician"}
OUTCOMES = ("connected", "no_answer", "not_accepted", "failed")
HANDOFF_OUTCOMES = ("handed_off", "failed")
# A gas or carbon monoxide call can be handed off only once the fixed lines have been said; while
# they are due or the check question is open, the caller stays with the agent.
HANDOFF_SAFETY_STATES = ("none", "cleared", "lines_said")


def _sentences(text: str) -> list[str]:
    return [words(part) for part in re.split(r"[.?!;,\n]+", text or "") if part.strip()]


def _is_request(before: str) -> bool:
    """Whether the words before an ask make it a request: nothing but fillers ("put me through to a
    manager", "just transfer me"), or a request opener ("can I just talk to a person")."""
    said = before.split()
    while said and said[-1] in FILLERS - {"can", "you"}:
        said.pop()
    if all(w in FILLERS for w in said):
        return True
    tail = " ".join(said)
    return any(tail == opener or tail.endswith(f" {opener}") for opener in REQUEST_OPENERS)


def _instructed(phrase: str, before: str) -> bool:
    said = before.split()
    return phrase.startswith(INSTRUCTIONS) and not (said and said[-1] in NOT_THE_CALLER)


def _asking_sentences(text: str) -> list[str]:
    asking = []
    for sentence in _sentences(text):
        for phrase in ASKED_PHRASES:
            at = sentence.find(words(phrase))
            if at < 0:
                continue
            before = sentence[: at + 1]
            if _is_request(before) or _instructed(phrase, before):
                asking.append(sentence)
                break
    return asking


def asks_for_person(text: str) -> bool:
    """One caller line asks for a person, rather than mentioning one."""
    return bool(_asking_sentences(text))


def _angry(line: str, equipment: set[str]) -> bool:
    said = words(line)
    if any(has_phrase(said, phrase) for phrase in ANGRY):
        return True
    heard = said.split()
    for i, word in enumerate(heard):
        if word in SWEARING and not equipment & set(heard[i + 1 : i + 3]):
            return True
    return False


def demands_person(text: str, earlier: list[str], equipment: frozenset[str] = frozenset()) -> bool:
    """This caller line asks for a person and means it: insistently ("right now", "I said"),
    angrily (in this line or the one just before it), or again after asking on an earlier line of
    this call. A calm "can I talk to someone now?" is a request, not a demand."""
    if not _asking_sentences(text):
        return False
    said = words(text)
    if any(has_phrase(said, phrase) for phrase in INSISTENT):
        return True
    equipment = EQUIPMENT | set(equipment)
    if any(_angry(line, equipment) for line in [*earlier[-1:], text]):
        return True
    return any(asks_for_person(line) for line in earlier)


def asked_for_person(conn: sqlite3.Connection, call_id: str) -> bool:
    return any(asks_for_person(line) for line in records.caller_text(conn, call_id).split("\n"))


def number(tenant: Tenant, role: str, environ: Mapping[str, str] = os.environ) -> str:
    """The role's phone number from the environment variable the tenant names, or empty."""
    name = (tenant.transfer or {}).get("targets", {}).get(role, "")
    return environ.get(name, "").strip() if name else ""


HOLD_NUMBER = "TRANSFER_HOLD_NUMBER"


def held(tenant: Tenant, environ: Mapping[str, str]) -> Mapping[str, str]:
    """The numbers with every role pointed at the hold line, for a call started while the owner's
    hold line switch was on. With no hold number set, the numbers are left as they are, so the
    switch can never send a caller nowhere."""
    hold = environ.get(HOLD_NUMBER, "").strip()
    if not hold:
        return environ
    names = (tenant.transfer or {}).get("targets", {}).values()
    return {**environ, **{name: hold for name in names}}


def routes(tenant: Tenant, environ: Mapping[str, str]) -> dict[str, str]:
    """Where a transfer rings, as the last four digits of each number, for the owner's Settings
    page: the office, the on-call technician and the hold line. An unset number is empty."""
    targets = (tenant.transfer or {}).get("targets", {})

    def last_four(name: str) -> str:
        digits = re.sub(r"\D", "", environ.get(name, "") if name else "")
        return digits[-4:] if len(digits) >= 4 else ""

    return {"office": last_four(targets.get("office", "")),
            "on_call": last_four(targets.get("on_call", "")),
            "hold": last_four(HOLD_NUMBER)}


def role_for(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    needs_on_call: bool,
    environ: Mapping[str, str] = os.environ,
) -> str | None:
    """Who a person flag on this call goes to now, or None to keep the flag and callback alone.
    `needs_on_call` is the server's own after-hours judgement, never the flag's priority."""
    if not tenant.transfer:
        return None
    if not switched_on(conn, tenant, call):
        return None
    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        return None
    if call["commercial"]:
        return None
    if call["after_hours"]:
        role = "on_call" if needs_on_call else None
    else:
        role = "office" if asked_for_person(conn, call["id"]) else None
    if role is None or not number(tenant, role, environ):
        return None
    return role


def tried(conn: sqlite3.Connection, call_id: str) -> bool:
    """A transfer of either kind was already started on this call: there is never a second."""
    return bool(
        conn.execute(
            "SELECT 1 FROM transfers WHERE call_id = ? UNION ALL "
            "SELECT 1 FROM handoffs WHERE call_id = ?",
            (call_id, call_id),
        ).fetchone()
    )


def switched_on(conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row) -> bool:
    """Whether the owner lets calls be put through to a person: the warm transfer setting, pinned
    when the call started (app/owner_settings.py). Off stops the cold handoff too; the flag and
    callback are still stored and the caller hears the callback line."""
    return owner_settings.for_call(conn, tenant, call["id"])["warm_transfer"]


def handoff_role(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, text: str
) -> str | None:
    """Who a caller who demands a person on this line goes to, or None to leave the call with the
    agent. The office in office hours, the on-call technician after hours. Asked on every caller
    line, after the safety check, so the model never decides it."""
    if not tenant.transfer or call["commercial"]:
        return None
    if call["safety_state"] not in HANDOFF_SAFETY_STATES:
        return None
    if tried(conn, call["id"]):
        return None
    earlier = [
        r["text"]
        for r in conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' AND seq < ? "
            "ORDER BY seq",
            (call["id"], call["turn_seq"]),
        )
    ]
    # After the safety lines, whose menu offers "someone on our team right now", a plain ask is the
    # caller taking that offer, not just a request: "connect me" there is answered with the
    # handoff (call_420a919e8d863ea61b471c97e64 was told "I can't transfer you" instead).
    took_the_offer = call["safety_state"] == "lines_said" and asks_for_person(text)
    if not took_the_offer and not demands_person(text, earlier, tenant.equipment_words):
        return None
    return "on_call" if call["after_hours"] else "office"


def start_handoff(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    role: str,
    flag_id: int,
    callback_id: int | None,
    now: datetime,
    environ: Mapping[str, str] = os.environ,
) -> dict | None:
    """Record the handoff, and return what the voice layer dials, or None when nothing is dialed
    (no number set for the role, or the owner's switch off). Called inside the transaction that
    stored the flag and callback."""
    phone = number(tenant, role, environ) if switched_on(conn, tenant, call) else ""
    handoff_id = conn.execute(
        "INSERT INTO handoffs (call_id, flag_id, callback_id, role, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (call["id"], flag_id, callback_id, role, "dialing" if phone else "not_dialed", iso(now)),
    ).lastrowid
    if not phone:
        return None
    return {
        "id": handoff_id,
        "role": role,
        "phone": phone,
        "ring_seconds": tenant.transfer["ring_seconds"],
    }


def _callback_line(conn: sqlite3.Connection, tenant: Tenant, callback_id: int | None,
                   now: datetime) -> str:
    """The line a caller hears when nobody was reached: the callback time stored with the flag."""
    callback = (
        conn.execute("SELECT due_at FROM callbacks WHERE id = ?", (callback_id,)).fetchone()
        if callback_id
        else None
    )
    if callback is None:
        return tenant.line("flag_stored_no_callback")
    when = tenant.line("callback_by", time=tenant.time_spoken(parse_iso(callback["due_at"]), now))
    return tenant.line("transfer_no_answer", when=when)


def finish_handoff(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    handoff_id: int,
    outcome: str,
    now: datetime,
) -> Result:
    """What happened to the SIP transfer. Handed off means the caller's call went to the number;
    nobody here knows whether a person or a voicemail answered, so the flag and callback stay open
    for a person to close. Failed leaves the call with the agent, and `say` is the line the caller
    hears next, with the callback time stored when the flag was raised."""
    row = conn.execute(
        "SELECT * FROM handoffs WHERE id = ? AND call_id = ?", (handoff_id, call["id"])
    ).fetchone()
    if row is None or row["status"] != "dialing":
        raise Refused("invalid_state", "")
    with tx(conn):
        conn.execute(
            "UPDATE handoffs SET status = ?, finished_at = ? WHERE id = ?",
            (outcome, iso(now), handoff_id),
        )
    if outcome == "handed_off":
        return ok(status=outcome)
    return ok(_callback_line(conn, tenant, row["callback_id"], now), status=outcome)


def close_open_handoff(conn: sqlite3.Connection, call_id: str, reason: str, now: datetime) -> None:
    """At the end of a call, a handoff still dialing is settled from how the call ended: the voice
    layer marks the end as a transfer before it sends one, so a call that left us that way was
    handed off even if the report never arrived. Called inside the end-of-call transaction."""
    conn.execute(
        "UPDATE handoffs SET status = ?, finished_at = ? WHERE call_id = ? AND status = 'dialing'",
        ("handed_off" if reason == "transfer" else "failed", iso(now), call_id),
    )


def close_open_transfer(conn: sqlite3.Connection, call_id: str, reason: str, now: datetime) -> None:
    """At the end of a call, a warm transfer still dialing is settled from how the call ended, in
    case the voice layer's report never arrived: a call that left us by transfer was connected.
    The flag and callback stay open for a person to close. Called inside the end-of-call
    transaction."""
    conn.execute(
        "UPDATE transfers SET status = ?, finished_at = ? WHERE call_id = ? AND status = 'dialing'",
        ("connected" if reason == "transfer" else "failed", iso(now), call_id),
    )


def _summary(tenant: Tenant, call: sqlite3.Row, flag: sqlite3.Row, role: str) -> str:
    # Urgent only when the server decided the on-call technician is needed, not from the flag.
    priority = "Urgent. " if role == "on_call" else ""
    return tenant.line(
        "transfer_summary",
        priority=priority,
        phone=phone_spoken(call["caller_id"]),
        reason=flag["reason"].rstrip("."),
    )


# flag_person fields that say when the caller wants the call; callback_day is "today" unless
# they named a day.
TIMED = ("callback_time", "callback_after", "callback_before")


def _named_a_time(conn: sqlite3.Connection, call_id: str) -> bool:
    """The caller said when they want the call ("tomorrow afternoon"), so it waits for then and
    nobody is rung now (done-bar persona e: a billing callback was put through at 3:30 AM)."""
    row = conn.execute(
        "SELECT args_json FROM tool_calls WHERE call_id = ? AND tool = 'flag_person' AND ok = 1 "
        "ORDER BY id DESC LIMIT 1",
        (call_id,),
    ).fetchone()
    args = json.loads(row["args_json"]) if row else {}
    return any(args.get(field) for field in TIMED) or args.get("callback_day") not in (
        None, "today"
    )


def plan(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    flag_id: int,
    callback_id: int | None,
    now: datetime,
    needs_on_call: bool,
    environ: Mapping[str, str] = os.environ,
) -> Result:
    """Asked by the voice layer right after a person flag. `transfer` is null when the call stays
    with the agent; otherwise it carries the number to dial, how long to ring, the line the person
    hears when they pick up (no caller details, since it may be a voicemail greeting), the summary
    they hear once they press 1, and the handoff line the caller hears first."""
    flag = conn.execute(
        "SELECT * FROM flags WHERE id = ? AND call_id = ?", (flag_id, call["id"])
    ).fetchone()
    callback = (
        conn.execute(
            "SELECT id FROM callbacks WHERE id = ? AND call_id = ?", (callback_id, call["id"])
        ).fetchone()
        if callback_id
        else None
    )
    if flag is None or (callback_id and callback is None):
        raise Refused("invalid_state", "")
    stays = tried(conn, call["id"]) or flag["kind"] != "person" or _named_a_time(conn, call["id"])
    role = None if stays else role_for(conn, tenant, call, needs_on_call, environ)
    if role is None:
        return ok(transfer=None)
    summary = _summary(tenant, call, flag, role)
    with tx(conn):
        transfer_id = conn.execute(
            "INSERT INTO transfers (call_id, flag_id, callback_id, role, summary, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (call["id"], flag_id, callback["id"] if callback else None, role, summary, iso(now)),
        ).lastrowid
    cfg = tenant.transfer
    return ok(
        tenant.line("transfer_handoff"),
        transfer={
            "id": transfer_id,
            "role": role,
            "phone": number(tenant, role, environ),
            "ring_seconds": cfg["ring_seconds"],
            "accept_seconds": cfg["accept_seconds"],
            "prompt": tenant.line("transfer_prompt", role=ROLE_NAMES.get(role, role)),
            "summary": summary,
        },
    )


def finish(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    transfer_id: int,
    outcome: str,
    now: datetime,
) -> Result:
    """What happened on the dial. Connected closes the flag and callback, since a person has the
    caller. Anything else leaves them open, and `say` is the line the caller hears next, with the
    callback time stored when the flag was raised."""
    row = conn.execute(
        "SELECT * FROM transfers WHERE id = ? AND call_id = ?", (transfer_id, call["id"])
    ).fetchone()
    if row is None or row["status"] != "dialing":
        raise Refused("invalid_state", "")
    with tx(conn):
        conn.execute(
            "UPDATE transfers SET status = ?, finished_at = ? WHERE id = ?",
            (outcome, iso(now), transfer_id),
        )
        if outcome == "connected":
            conn.execute(
                "UPDATE callbacks SET status = 'done', updated_at = ? WHERE id = ? "
                "AND status = 'pending'",
                (iso(now), row["callback_id"]),
            )
            conn.execute("UPDATE flags SET status = 'done' WHERE id = ?", (row["flag_id"],))
    if outcome == "connected":
        return ok(status=outcome)
    return ok(_callback_line(conn, tenant, row["callback_id"], now), status=outcome)


def for_call(conn: sqlite3.Connection, call_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM transfers WHERE call_id = ?", (call_id,)).fetchone()
    return dict(row) if row else None
