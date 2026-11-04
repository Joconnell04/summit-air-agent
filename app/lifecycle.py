"""The calls the voice layer makes around a conversation: start, every turn, end."""

import json
import re
import sqlite3
from datetime import datetime, timedelta

from . import (
    address_as,
    cards,
    dead_air,
    lookup,
    owner_settings,
    pace,
    recordings,
    records,
    rules,
    safety,
    transfer,
)
from . import priority as prio
from .db import iso, parse_iso, tx
from .envelope import Result, ok
from .fsm import FsmAdapter, FsmRejected, FsmUnavailable
from .schemas import EndIn, StartIn, TurnIn
from .tenant import Tenant
from .text import digit_runs, has_phrase, phone_key, words

RUN_WORDS = r"(?:i|we) (?:run|manage|operate|work at)"
# Owning is matched only against businesses: "I own the building" or "I manage the building" is a
# landlord or a super, and an apartment building is residential (spec S-05, journey 8).
OWN_WORDS = r"(?:i|we) own"


# Words that end a street or place name. A commercial word right before one is part of a name:
# "540 Church Ave", "Hotel Street" (spec S-05, PR #88).
STREET_SUFFIXES = {
    "ave", "avenue", "st", "street", "rd", "road", "pl", "place", "blvd", "boulevard", "pkwy",
    "parkway", "ln", "lane", "ct", "court", "dr", "drive", "ter", "terrace",
}
# "Plaza" is a business ("it's in a plaza") unless it ends a name ("Grand Army Plaza").
PLAZA_GENERIC = {"a", "an", "the", "this", "that", "our", "my", "strip", "shopping", "small"}
# Words that place an address by a landmark: "right off the corner by the church", "across from
# the school". The business word after one is a place, not the caller's business (eval run
# 20261104-183233, no-heat-gas-stove).
LANDMARK_LEADS = ("by", "near", "next to", "across from", "behind", "beside", "opposite", "past",
                  "around the corner from", "down the street from", "up the street from")
LANDMARK_FILLER = {"a", "an", "the", "that", "this", "big", "old", "little", "new"}
NUMBER_WORDS = {"oh", "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine", "ten", "eleven", "twelve", "twenty", "thirty", "forty", "fifty", "sixty",
                "seventy", "eighty", "ninety", "hundred"}


def without_place_names(text: str, business_words: set[str]) -> str:
    """The line, as words, with those that are part of a street or place name blanked out, so an
    address is never read as a business: the word before a street word ("Church Ave"), a business
    word right after a house number ("540 Church"), "plaza" when it ends a name ("Grand Army
    Plaza"), and a landmark the caller places the address by ("by the church"). Each sentence or comma-separated part is read on its own, so "I run a clinic. Dr.
    Patel does billing" and "my restaurant, St Marks" keep their business word."""
    parts = [_place_names(words(part), business_words) for part in re.split(r"[.,;:!?]", text)]
    return " " + " ".join(p for p in parts if p) + " "


def _place_names(text_words: str, business_words: set[str]) -> str:
    tokens = text_words.split()
    kept = list(tokens)
    for i, token in enumerate(tokens):
        before = tokens[i - 1] if i else None
        if token in STREET_SUFFIXES and before is not None:
            kept[i - 1] = "_"
        if token in business_words and before is not None and (
            before.isdigit() or before in NUMBER_WORDS
        ):
            kept[i] = "_"
        if token == "plaza" and before is not None and before not in PLAZA_GENERIC:
            kept[i] = "_"
        if token in business_words and _after_landmark_lead(tokens[:i]):
            kept[i] = "_"
    return " ".join(kept)


def _after_landmark_lead(before: list[str]) -> bool:
    """The words before end with a landmark lead, past an article: "by the", "next to that"."""
    trimmed = list(before)
    while trimmed and trimmed[-1] in LANDMARK_FILLER:
        trimmed.pop()
    ending = " " + " ".join(trimmed) + " "
    return any(ending.endswith(f" {lead} ") for lead in LANDMARK_LEADS)


def sounds_commercial(text: str, cfg: dict) -> bool:
    """Does the caller describe a business, with commercial equipment? Homes, apartments, co-ops and
    whole apartment buildings are residential. Best effort by design: the agent and the FSM account
    type catch the rest, and a false positive costs a booking, so the tenant's lists stay narrow."""
    single = {w for w in cfg["phrases"] + cfg["owned"] + cfg["run"] if " " not in w.strip()}
    text_words = without_place_names(text, single)
    for phrase in cfg["not"]:
        text_words = text_words.replace(words(phrase), " ")
    if any(has_phrase(text_words, p) for p in cfg["phrases"]):
        return True
    # "Not a home" is a business unless the line says it's an apartment building, and a business
    # word in the same line wins: "it's not a home, it's the shop building" (PR #102 review).
    if any(has_phrase(text_words, p) for p in cfg.get("not_home", [])):
        names_business = any(has_phrase(text_words, p) for p in cfg["owned"] + cfg["run"])
        if names_business or not any(
            has_phrase(text_words, p) for p in cfg.get("apartment_signs", [])
        ):
            return True
    owned = "|".join(map(re.escape, cfg["owned"]))
    run = "|".join(map(re.escape, cfg["run"]))
    return bool(
        re.search(rf" (?:my|our) (?:{owned}) ", text_words)
        or re.search(rf" {RUN_WORDS} (?:a|an|the) (?:\w+ )?(?:{run}) ", text_words)
        or re.search(rf" {OWN_WORDS} (?:a|an|the) (?:\w+ )?(?:{owned}) ", text_words)
        # "We're a restaurant": the caller says what they are ("we're" is "were" once the
        # apostrophe is dropped).
        or re.search(rf" (?:were|we are) (?:a|an) (?:\w+ )?(?:{run}) ", text_words)
    )


# Words that can sit between a negator and an approval phrase: "I don't need to ask my landlord".
APPROVAL_FILLER = {"need", "needs", "to", "have", "has", "got", "gotta", "even", "really"}


def approval_unclear(text: str, cfg: dict) -> bool:
    """The caller says someone else, such as a co-op board or a landlord, has to approve the work
    (eval round 2, co-op-owner-mentions-board). A negated mention ("no, I don't need to ask my
    landlord") says the opposite, so it doesn't count."""
    text_words = words(text)
    for phrase in cfg["unclear_phrases"]:
        target = words(phrase)
        start = text_words.find(target)
        while start >= 0:
            kept = text_words[: start + 1].split()
            while kept and kept[-1] in APPROVAL_FILLER:
                kept.pop()
            before = " " + " ".join(kept) + " "
            if not any(before.endswith(words(n)) for n in safety.NEGATORS):
                return True
            start = text_words.find(target, start + 1)
    return False


DO_NOT_CALL_PHRASES = (
    "dont call me",
    "do not call me",
    "stop calling",
    "never call",
    "dont ever call",
    "take me off your list",
    "remove my number",
)


# How a caller gives a name on a call ("this is Tomasz", "my name is Ana"). Only a capitalized word
# counts, as speech-to-text writes names, so "this is about my heat" names nobody.
NAME_SAID = re.compile(r"\b(?:[Mm]y name is|[Tt]his is|[Ii]t's|[Ii]t is)\s+([A-Z][a-z]+)")
NOT_NAMES = {"Summit", "Okay", "Just", "Still", "Really", "So", "About", "For", "The", "My", "Me"}
# The last turns of a linked call the agent sees, so it doesn't ask again for what was said.
RESUME_TURNS = 12


def _name_said(lines: list[str]) -> str | None:
    for line in lines:
        for match in NAME_SAID.finditer(line):
            if match[1] not in NOT_NAMES:
                return match[1]
    return None


def _linked(
    conn: sqlite3.Connection, tenant: Tenant, caller_key: str, call_id: str, now: datetime
) -> tuple[sqlite3.Row | None, list[sqlite3.Row]]:
    """The call this one picks up from, and the calls within the drop window. A call from the same
    number a few minutes after any call is linked; a call later the same day is linked only to one
    that dropped, so a caller who finished a call this morning starts fresh this afternoon (eval
    round 2, seed 6). Same number and same local day are the gate; a call before midnight never
    links to one after it unless it falls inside the drop window."""
    if not caller_key:
        return None, []
    day_start = datetime.combine(tenant.local(now).date(), datetime.min.time(), tenant.tz)
    window_start = now - timedelta(minutes=tenant.repeat["link_within_minutes"])
    calls = [
        c
        for c in records.recent_calls(
            conn, tenant.slug, caller_key, now, min(day_start, window_start)
        )
        if c["id"] != call_id
    ]
    recent = [c for c in calls if parse_iso(c["started_at"]) >= window_start]
    # Only the latest call today, and only if it dropped: a caller who finished a call after the
    # drop (a callback, say) starts fresh (PR #75 review).
    today = [c for c in calls if parse_iso(c["started_at"]) >= day_start]
    same_day_drop = today[0] if today and records.dropped(today[0]) else None
    return (recent[0] if recent else same_day_drop), recent


def _resume(conn: sqlite3.Connection, previous: sqlite3.Row) -> dict:
    """Where the last call stopped, so the agent can pick up without redoing anything, and the name
    the caller gave on it, so the agent can check it is the same person first."""
    turns = conn.execute(
        "SELECT speaker, text FROM turns WHERE call_id = ? ORDER BY seq DESC LIMIT ?",
        (previous["id"], RESUME_TURNS),
    ).fetchall()[::-1]
    named = conn.execute(
        "SELECT request_json FROM bookings WHERE call_id = ? AND kind = 'book' ORDER BY id DESC",
        (previous["id"],),
    ).fetchone()
    caller_lines = [
        t["text"]
        for t in conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' ORDER BY seq",
            (previous["id"],),
        )
    ]
    # The caller's own name: what they said on the call, else the booking's contact. A booking put
    # on its own account names the person who lives there, not the caller (PR #75 review).
    request = json.loads(named["request_json"]) if named else {}
    booked_name = request.get("contact_name") or (
        request.get("details", {}).get("name") if request.get("account") != "own" else None
    )
    name = _name_said(caller_lines) or booked_name
    last = {
        speaker: conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND speaker = ? ORDER BY seq DESC LIMIT 1",
            (previous["id"], speaker),
        ).fetchone()
        for speaker in ("caller", "agent")
    }
    booked = conn.execute(
        "SELECT fsm_job_id, status, window_start FROM bookings WHERE call_id = ? AND kind = 'book' "
        "AND status IN ('sending', 'booked', 'queued')",
        (previous["id"],),
    ).fetchone()
    return {
        "call_id": previous["id"],
        "ended_at": previous["ended_at"],
        "dropped": records.dropped(previous),
        # The caller asked the agent to wait and never came back, so the worker hung up after
        # saying they could call back (DECISIONS.md 081). The greeting says so.
        "stepped_away": previous["end_reason"] == "stepped_away",
        "last_caller_line": last["caller"]["text"] if last["caller"] else None,
        "last_agent_line": last["agent"]["text"] if last["agent"] else None,
        "booking": dict(booked) if booked else None,
        "safety_state": previous["safety_state"],
        "started_at": previous["started_at"],
        "caller_name": name.split()[0] if name else None,
        "preferred_name": address_as.current(conn, previous["id"]),
        "turns": [{"speaker": t["speaker"], "text": t["text"]} for t in turns],
    }


def start(
    conn: sqlite3.Connection,
    tenant: Tenant,
    fsm: FsmAdapter,
    args: StartIn,
    now: datetime,
    record: bool = False,
) -> Result:
    """Lookup before hello: the caller's records, a recent call to resume, and the safety state
    carried from a dropped safety call. Calling it twice for one call id is harmless. `record` says
    the server has somewhere to keep recordings; the call carries the file name to record under."""
    caller_key = phone_key(args.caller_id)
    known = caller_key if len(caller_key) == 10 else ""
    previous, recent = _linked(conn, tenant, known, args.call_id, now)
    with tx(conn):
        conn.execute(
            """INSERT OR IGNORE INTO calls (id, tenant, caller_id, caller_key, dialed_number,
                   direction, started_at, after_hours, linked_call_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                args.call_id,
                tenant.slug,
                args.caller_id,
                known,
                args.dialed_number,
                args.direction,
                iso(now),
                int(not tenant.is_open(now)),
                previous["id"] if previous else None,
            ),
        )
        call = records.get_call(conn, args.call_id)
        # The owner's settings, read once and kept for the whole call (app/owner_settings.py).
        values = owner_settings.pin(conn, tenant, args.call_id)
        pace.start(
            conn,
            tenant.pace,
            tenant.slug,
            args.call_id,
            known,
            previous["id"] if previous else None,
            now,
        )
        # A safety call that dropped carries on where it was: if the lines weren't said, they come
        # first on this call too.
        if (
            previous
            and previous["safety_state"] in ("signal", "lines_said", "checking")
            and call["safety_state"] == "none"
        ):
            records.update_call(
                conn,
                args.call_id,
                safety_state=previous["safety_state"],
                safety_lines_at=previous["safety_lines_at"],
                safety_kind=previous["safety_kind"],
                safety_signal_at=previous["safety_signal_at"],
                safety_flag_id=previous["safety_flag_id"],
            )
    call = records.get_call(conn, args.call_id)

    customers, fsm_ok = [], True
    if known:
        try:
            found = fsm.customers_by_phone(known)[:5]
            with tx(conn):
                for customer in found:
                    records.add_scope(
                        conn, args.call_id, customer["id"], "caller_id", customer["type"]
                    )
            customers = [lookup.customer_bundle(fsm, tenant, conn, c, now) for c in found]
        except (FsmUnavailable, FsmRejected):
            fsm_ok = False
    _record_known_out_of_area(conn, args.call_id, customers, now)

    # With two-party consent on, the caller hears that the call may be recorded before anything
    # is kept. A call that opens with the safety lines has no greeting to carry the notice, so it
    # isn't recorded at all.
    consent = values["two_party_consent"]
    record_this = recordings.wanted(tenant, values, record) and not (
        consent and call["safety_state"] == "signal"
    )
    drops = sum(1 for c in recent if records.dropped(c))
    offer_callback = drops >= tenant.repeat["drops_before_callback_offer"]
    data = {
        "call_id": args.call_id,
        "tenant": {"slug": tenant.slug, "name": tenant.name, "timezone": str(tenant.tz)},
        "after_hours": bool(call["after_hours"]),
        "caller": {
            "phone": args.caller_id,
            "known_number": bool(known),
            "do_not_call": bool(known) and records.is_do_not_call(conn, known),
        },
        "customers": customers,
        "fsm_available": fsm_ok,
        "linked_call": _resume(conn, previous) if previous else None,
        # What the caller asked to be called: on a linked call, else on their record.
        "preferred_name": address_as.current(conn, args.call_id) or address_as.from_notes(customers),
        "offer_callback": offer_callback,
        "knowledge": tenant.knowledge,
        # The owner's live edits, pinned to this call (app/rules.py).
        "house_rules": rules.start_call(conn, tenant, args.call_id, now),
        # The speed the worker speaks at from the greeting on (app/pace.py).
        "pace": pace.current(conn, args.call_id),
        "recording": (
            {"object_key": recordings.object_key(tenant, args.call_id)} if record_this else None
        ),
        # Said by the voice layer just before the greeting (or a placed call's opening line).
        "recording_notice": recordings.notice(tenant) if consent else None,
    }
    say = tenant.line("repeated_drops") if offer_callback else ""
    if call["safety_state"] == "signal":
        lines = safety.lines_for(tenant.safety, call["safety_kind"])
        data["safety"] = {"action": "speak_lines", "kind": call["safety_kind"], "text": lines}
        say = lines
    return ok(say, **data)


def _record_known_out_of_area(
    conn: sqlite3.Connection, call_id: str, customers: list[dict], now: datetime
) -> None:
    """A known caller whose one location on file is outside the area is counted as out of area at
    the start, so the count doesn't depend on the agent checking the ZIP (spec S-28). With several
    locations the call's ZIP isn't known yet, so it waits for the read-back or a ZIP check. If they
    give a new address inside the area and book, the owner view leaves the call out of the count."""
    locations = [loc for c in customers for loc in c["locations"]]
    if len(locations) != 1 or locations[0]["in_service_area"]:
        return
    with tx(conn):
        for loc in locations:
            conn.execute(
                "INSERT OR IGNORE INTO out_of_area (call_id, zip, at) VALUES (?, ?, ?)",
                (call_id, loc["zip"], iso(now)),
            )


def _at_risk_asked(conn: sqlite3.Connection, call_id: str, phrases: list[str]) -> bool:
    """Whether an agent line on the thread already asked who is home and whether anyone is at
    risk."""
    return any(
        t["speaker"] == "agent" and any(has_phrase(words(t["text"]), p) for p in phrases)
        for t in records.thread_turns(conn, call_id)
    )


PAYMENT_REASON = "Caller wants to pay by card: send a payment link or call to take the payment"
BILLING_REASON = "Billing: the caller disputes a charge; the office calls back"
# How far ahead the office's opening is looked for, in quarter hours (a long weekend fits).
OPEN_SEARCH_STEPS = 7 * 24 * 4


def _has_flag(conn: sqlite3.Connection, call_id: str, kind: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM flags WHERE call_id = ? AND kind = ?", (call_id, kind)
    ).fetchone() is not None


def _first_open(tenant: Tenant, moment: datetime) -> datetime:
    """The first quarter hour from this moment that is inside the company's hours."""
    for _ in range(OPEN_SEARCH_STEPS):
        if tenant.is_open(moment):
            return moment
        moment = moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)
        moment += timedelta(minutes=15)
    raise ValueError("the company is never open")


def office_callback_due(tenant: Tenant, now: datetime) -> datetime:
    """When the office calls back about a bill: `billing_minutes` after it is next open, inside its
    hours, so the call is the office's and never the on-call technician's (DECISIONS.md 080)."""
    minutes = timedelta(minutes=tenant.callbacks["billing_minutes"])
    due = _first_open(tenant, now) + minutes
    while not tenant.is_open(due):
        due = _first_open(tenant, due) + minutes
    return due


def _billing_line(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, now: datetime
) -> str:
    """A caller disputing a charge: a flag and a callback from the office, stored before the line
    that promises it is said."""
    due = office_callback_due(tenant, now)
    records.add_flag(conn, call["id"], "billing", BILLING_REASON, now, "Normal")
    callback_id = records.add_callback(conn, tenant, call, due, BILLING_REASON, now)
    if callback_id is None:
        return tenant.line("flag_stored_no_callback")
    return tenant.line(
        "billing_callback", when=tenant.line("callback_by", time=tenant.time_spoken(due, now))
    )


def _electrical(
    conn: sqlite3.Connection, tenant: Tenant, call_id: str, text: str, seq: int, now: datetime
) -> bool:
    """Records an electrical report on this line, and says whether the electrical lines are still
    owed on this call (heard, and not said yet)."""
    row = conn.execute("SELECT * FROM electrical WHERE call_id = ?", (call_id,)).fetchone()
    line = safety.electrical_line(text, tenant.safety)
    if line and row is None:
        conn.execute(
            "INSERT INTO electrical (call_id, heard_seq, heard_line) VALUES (?, ?, ?)",
            (call_id, seq, line),
        )
        return True
    return row is not None and row["said_at"] is None


def _spoken_line(
    conn: sqlite3.Connection,
    tenant: Tenant,
    call: sqlite3.Row,
    text: str,
    seq: int,
    now: datetime,
    on_safety: bool,
) -> dict | None:
    """A line the voice layer says word for word for this caller line, if any: the card stop, the
    billing callback or the electrical lines, in that order. Never on a gas or carbon monoxide
    call, whose lines come first; an electrical report heard then is said once the call clears."""
    owed = _electrical(conn, tenant, call["id"], text, seq, now)
    if on_safety:
        return None
    if cards.offers_card(text, tenant.payments):
        if not _has_flag(conn, call["id"], "payment"):
            records.add_flag(conn, call["id"], "payment", PAYMENT_REASON, now, "Normal")
        return {"kind": "card", "text": tenant.line("card_stop")}
    if cards.billing_dispute(text, tenant.payments) and not _has_flag(conn, call["id"], "billing"):
        return {"kind": "billing", "text": _billing_line(conn, tenant, call, now)}
    if owed:
        conn.execute("UPDATE electrical SET said_at = ? WHERE call_id = ?", (iso(now), call["id"]))
        return {"kind": "electrical", "text": safety.electrical_lines(tenant.safety)}
    return None


def turn(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, args: TurnIn, now: datetime
) -> Result:
    """Log the line and run the safety check on every caller line (docs/call-journeys.md, spine 4)."""
    cfg = tenant.safety
    seq = call["turn_seq"] + 1
    action = safety.SafetyAction("none", call["safety_kind"])
    changes: dict = {"turn_seq": seq}
    hints = []
    stop_recording = card_stop = False
    spoken, hold = None, None
    with tx(conn):
        if args.speaker == "caller":
            heating = cfg.get("heating_check")
            heating_asked = bool(heating) and records.agent_said(
                conn, call["id"], cfg["vague"][heating["kind"]]["question"]
            )
            confirm_asked = any(
                records.agent_said(conn, call["id"], spec["confirm_question"])
                for spec in cfg["vague"].values()
                if spec.get("confirm_question")
            )
            # The confirm question asks the negative way, so its answer is read the other way
            # round. It is the one being answered while the call is checking and it is the check
            # question the agent asked last.
            answering_confirm = False
            if call["safety_state"] == "checking":
                spec = cfg["vague"][call["safety_kind"]]
                confirm = spec.get("confirm_question")
                answering_confirm = bool(confirm) and confirm == records.last_said(
                    conn, call["id"], [confirm, spec["question"]]
                )
            action = safety.check_caller_turn(
                args.text,
                call["safety_state"],
                call["safety_kind"],
                call["safety_variant"],
                cfg,
                heating_asked,
                confirm_asked,
                answering_confirm,
            )
            if action.action == "speak_lines":
                changes.update(safety_state="signal", safety_kind=action.kind)
                if call["safety_signal_at"] is None or call["safety_kind"] != action.kind:
                    label = cfg["kinds"][action.kind]["label"]
                    changes["safety_signal_at"] = iso(now)
                    changes["safety_flag_id"] = records.add_flag(
                        conn,
                        call["id"],
                        "safety",
                        cfg["flag_reason"].format(label=label.capitalize()),
                        now,
                        "Urgent",
                    )
            elif action.action == "speak_variant":
                changes["safety_variant"] = action.variant
            elif action.action == "ask_check":
                changes.update(safety_state="checking", safety_kind=action.kind)
            elif action.action == "cleared":
                changes.update(safety_state="cleared")
                reason = (
                    "Caller said there was no smell or alarm after the safety lines"
                    if call["safety_state"] == "lines_said"
                    else "Caller said no to the safety check"
                )
                records.add_flag(conn, call["id"], "safety_check_cleared", reason, now, "Low")
            # Gas or carbon monoxide on this call after this line: their lines and questions come
            # before anything else. A check the caller just cleared isn't.
            gas_or_co = action.action in ("speak_lines", "speak_variant", "ask_check") or (
                action.action == "none" and call["safety_state"] not in ("none", "cleared")
            )
            # The card stop, the billing callback or the electrical lines (DECISIONS.md 080, 082).
            spoken = _spoken_line(conn, tenant, call, args.text, seq, now, gas_or_co)
            # A caller who asks the agent to wait, or comes back (DECISIONS.md 081).
            hold = dead_air.caller_line(
                conn, call["id"], args.text, seq, now, gas_or_co or spoken is not None
            )
            if hold == "holding" and spoken is None:
                hints.append("caller_holding")
            # Card digits were read out: the recording stops and is deleted, as for a caller who
            # objects, so the digits aren't kept in the audio either (DECISIONS.md 080).
            if cards.removed(args.text) and not recordings.declined(conn, call["id"]):
                recordings.decline(conn, call["id"], seq, now)
                stop_recording, card_stop = True, True
            # The caller asking for a pace, "sorry, what?" twice, or an older caller (app/pace.py).
            hints += pace.caller_line(conn, tenant.pace, call, args.text, seq, now)
            text_words = words(args.text)
            # Marked at once on the caller's words (spec S-49); the agent still says the line.
            if len(call["caller_key"]) == 10 and any(
                has_phrase(text_words, p) for p in DO_NOT_CALL_PHRASES
            ):
                records.mark_do_not_call(conn, call, now)
                hints.append("do_not_call")
            # A caller who says not to record isn't recorded, whatever the consent setting: the
            # voice layer is told to stop and to say so, and the jobs loop deletes the file
            # instead of keeping it.
            if recordings.objected(args.text) and not recordings.declined(conn, call["id"]):
                recordings.decline(conn, call["id"], seq, now)
                stop_recording = True
            # A form of address the caller asked for ("call me Dr. Patel"), kept for the call.
            named = address_as.record(
                conn, call["id"], args.text, seq, now, address_as.place_words(tenant.address_lookup)
            )
            if named:
                hints.append(named)
            # A caller who describes a business can't book, whatever the agent does next (S-05).
            if sounds_commercial(args.text, tenant.commercial):
                changes["commercial"] = 1
                hints.append("commercial")
            # Who can approve isn't settled: the agent offers a callback, and a booking the caller
            # asks for gets a call before dispatch (tools.book_appointment reads the same lines).
            if approval_unclear(args.text, tenant.approval):
                hints.append("approval_unclear")
            # The server, not the model, says when the call reaches booking or closing, and only
            # then sends the owner's guidance for that stage, as a [server] note. Never on a safety
            # call; a stage reached during one gets its note once the safety state clears.
            rules.advance_stage(conn, call, now)
            on_safety = call["safety_state"] not in ("none", "cleared") or action.action != "none"
            # A caller who asks for a higher priority with no new fact gets a calm answer from a
            # note; nothing is raised, since only facts raise priority (DECISIONS.md 073).
            if not on_safety and prio.raising_push(args.text, tenant.priority):
                asked = _at_risk_asked(conn, call["id"], tenant.priority.get("at_risk_asked", []))
                hints.append("priority_pushed_asked" if asked else "priority_pushed")
            if not on_safety:
                stage, items = rules.note_due(conn, tenant, call)
                if stage:
                    hints += [f"stage_{stage}"] + [f"guidance:{item}" for item in items]
        elif call["safety_state"] == "signal" and safety.agent_said_lines(
            args.text, call["safety_kind"], cfg
        ):
            changes.update(safety_state="lines_said", safety_lines_at=iso(now))
        records.update_call(conn, call["id"], **changes)
        conn.execute(
            "INSERT INTO turns (call_id, seq, speaker, text, at, timings_json, safety_action) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                call["id"],
                seq,
                args.speaker,
                args.text,
                iso(now),
                json.dumps(args.timings),
                action.action if action.action != "none" else None,
            ),
        )
    if action.action in ("speak_lines", "speak_variant", "ask_check"):
        hints.append("journey:safety")
    if action.action == "cleared":
        stood_down = call["safety_state"] == "lines_said"
        hints.append("safety_stood_down" if stood_down else "safety_check_cleared")
    # After the lines, every caller line carries a reminder that the safety call doesn't book.
    if args.speaker == "caller" and action.action == "none" and call["safety_state"] == "lines_said":
        hints.append("safety_call")
    payload = None
    if action.action != "none":
        payload = {"action": action.action, "kind": action.kind, "text": action.text}
    say = action.text or (spoken["text"] if spoken else "")
    if stop_recording:
        # Stopped for card digits, the recording's own line isn't said: the card line is.
        recording = {"action": "stop", "text": "" if card_stop else recordings.stopped_line(tenant)}
    else:
        recording = None
    known = (
        already_known(conn, call["id"], tenant.system_words, frozenset(tenant.service_area))
        if args.speaker == "caller"
        else {}
    )
    return ok(
        say,
        seq=seq,
        safety=payload,
        spoken=spoken,
        hold={"state": hold} if hold else None,
        hints=hints,
        known=known,
        pace=pace.current(conn, call["id"]),
        recording=recording,
    )


# What the agent's tool calls have already recorded, by field. Read from the tool log, so it costs
# no model request (eval round 4: the agent asked again for what the caller had told it).
KNOWN_FROM_DETAILS = {
    "name": "name",
    "who_home": "who's home",
    "approver": "who approves",
    "system_type": "system",
    "arrival_notes": "access",
}
KNOWN_MAX = 40  # characters a value is cut to, so the note stays a few dozen tokens
# From the caller's own lines (eval round 4, spell-street-letter-by-letter: the agent asked a
# caller who had said "my home" whether she rents, and for a name she had given).
KNOWN_NAME = re.compile(r"\b(?:my name is|my names|name is|the names|this is)\s+([a-z]+(?: [a-z]+)?)")
RENTS = ("i rent", "im renting", "we rent", "were renting", "im a tenant", "my landlord", "our landlord")
OWNS = ("i own", "we own", "my home", "my house", "our house", "our home", "its my place",
        "its our place", "im the owner", "homeowner")
NOT_A_NAME = {"summit", "the", "a", "my", "it", "about", "regarding", "calling"}
SYSTEM_HEDGES = ("dont know", "not sure", "no idea", "maybe", "or a", "or the", "either")


def already_known(
    conn: sqlite3.Connection,
    call_id: str,
    systems: tuple[str, ...] = (),
    area_zips: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Facts this call's tool calls carried: the ZIP checked, the address and phone read back
    (marked confirmed once the caller said yes) and the intake a booking attempt sent, so the
    agent doesn't ask again. Empty until a tool has recorded something."""
    known: dict[str, str] = {}
    rows = conn.execute(
        "SELECT tool, args_json FROM tool_calls WHERE call_id = ? ORDER BY id", (call_id,)
    ).fetchall()
    for row in rows:
        args = json.loads(row["args_json"] or "{}")
        if row["tool"] == "check_service_area" and args.get("zip"):
            known["ZIP"] = args["zip"]
        if row["tool"] == "find_slots":
            facts = args.get("facts") or {}
            if facts.get("who_home"):
                known["who's home"] = facts["who_home"]
        if row["tool"] in ("book_appointment", "reschedule_appointment"):
            for field, label in KNOWN_FROM_DETAILS.items():
                value = (args.get("details") or {}).get(field)
                if value:
                    known[label] = value
    said_lines = [
        r["text"]
        for r in conn.execute(
            "SELECT text FROM turns WHERE call_id = ? AND speaker = 'caller' ORDER BY seq",
            (call_id,),
        )
    ]
    for raw in said_lines:
        # A ZIP the caller said is known before any tool checks it (eval run 20261104-053435,
        # gas-worry-no-smell: asked for the ZIP twice after the caller gave it).
        # Only a ZIP the company serves, so a five-digit house number ("21230 41st Avenue") is never
        # taken for one (PR #102 review); any other ZIP is asked for and checked as usual.
        zips = [run for run in digit_runs(raw) if len(run) == 5 and run in area_zips]
        if zips and "ZIP" not in known:
            known["ZIP said"] = zips[-1]
    lines = [words(raw).strip() for raw in said_lines]
    for line in lines:
        padded_system = f" {line} "
        # The trade's system words (eval run 20261104-053435, heat-pump-defrost), only when the
        # caller names one without hedging: "a heat pump or a boiler, I don't know" is still a
        # question for the agent (PR #102 review).
        named = {s for s in systems if f" {s} " in padded_system}
        named -= {s for s in named for other in named if s != other and s in other}
        hedged = any(f" {h} " in padded_system for h in SYSTEM_HEDGES)
        if len(named) == 1 and not hedged:
            known.setdefault("system", named.pop())
        said = KNOWN_NAME.search(line)
        if said and said.group(1).split()[0] not in NOT_A_NAME:
            known.setdefault("name", said.group(1).title())
        padded = f" {line} "
        if any(f" {p} " in padded for p in RENTS):
            known["rents or owns"] = "rents"
        elif any(f" {p} " in padded for p in OWNS):
            known.setdefault("rents or owns", "owns")
    # A ZIP the address lookup found for the caller's street (tools.find_address).
    for row in conn.execute(
        "SELECT zips_json FROM address_lookups WHERE call_id = ? AND status = 'one' ORDER BY id",
        (call_id,),
    ):
        known["ZIP"] = json.loads(row["zips_json"])[0]
    preferred = address_as.current(conn, call_id)
    if preferred:
        known["call them"] = preferred
    readback = conn.execute(
        "SELECT readback_json, readback_confirmed_at FROM calls WHERE id = ?", (call_id,)
    ).fetchone()
    if readback and readback["readback_json"]:
        asked = json.loads(readback["readback_json"])
        mark = " (confirmed)" if readback["readback_confirmed_at"] else ""
        known["address"] = f"{asked['street']}, {asked['zip']}{mark}"
        known["phone"] = f"ending {asked['phone'][-4:]}{mark}"
    return {k: v if len(v) <= KNOWN_MAX else v[: KNOWN_MAX - 3] + "..." for k, v in known.items()}


def end(
    conn: sqlite3.Connection, tenant: Tenant, call: sqlite3.Row, args: EndIn, now: datetime
) -> Result:
    """Close the call, record its outcome, and make sure a dropped safety call gets a person."""
    if call["ended_at"]:
        return ok(outcome=call["outcome"], already_ended=True)
    with tx(conn):
        if call["safety_state"] in ("signal", "checking"):
            records.add_flag(
                conn,
                call["id"],
                "safety_dropped",
                "Safety call ended before the lines were said",
                now,
                "Urgent",
            )
            due = now + timedelta(minutes=tenant.callbacks["dropped_safety_minutes"])
            records.add_callback(conn, tenant, call, due, "Safety call dropped; call back", now)
        # A safety caller who confirmed the address for a visit but left before a time was set
        # gets a person to call back and book it once they're safe (PR #74 review).
        on_safety = call["safety_signal_at"] and call["safety_state"] != "cleared"
        booked = conn.execute(
            "SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' "
            "AND status IN ('sending', 'booked', 'queued')",
            (call["id"],),
        ).fetchone()
        if on_safety and call["readback_confirmed_at"] and not booked:
            records.add_flag(
                conn,
                call["id"],
                "person",
                "Safety call: the caller confirmed the address for a visit but left before a "
                "time was set; call back to book it once they're safe",
                now,
                "Urgent",
            )
        transfer.close_open_handoff(conn, call["id"], args.reason, now)
        outcome = _outcome(conn, call)
        records.update_call(
            conn,
            call["id"],
            ended_at=iso(now),
            end_reason=args.reason,
            outcome=outcome,
            summary_status="pending",
        )
    return ok(outcome=outcome)


def _outcome(conn: sqlite3.Connection, call: sqlite3.Row) -> str:
    def has(sql: str) -> bool:
        return bool(conn.execute(sql, (call["id"],)).fetchone())

    if has("SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'booked'"):
        return "booked"
    if has("SELECT 1 FROM bookings WHERE call_id = ? AND kind = 'book' AND status = 'queued'"):
        return "queued"
    if call["safety_signal_at"] and call["safety_state"] != "cleared":
        return "safety"
    if call["commercial"]:
        return "declined_commercial"
    if has("SELECT 1 FROM out_of_area WHERE call_id = ?"):
        return "declined_out_of_area"
    if has(
        "SELECT 1 FROM bookings WHERE call_id = ? AND kind IN ('reschedule', 'cancel') AND status = 'done'"
    ):
        return "changed_booking"
    if has("SELECT 1 FROM callbacks WHERE call_id = ?"):
        return "callback"
    if has("SELECT 1 FROM flags WHERE call_id = ? AND kind = 'person'"):
        return "flagged"
    return "no_booking"
