"""Code checks: pass or fail from the tool server's record of the call, never from a model.

Each check reads the call's timeline (app/trace.py, the same shape /debug/calls/{id} returns), so the
in-process stack and the Retell baseline are judged by the same evidence. A scenario lists checks
under `must:` as `- <check>: <argument>`; a check returns the reasons it failed, or nothing.
"""
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import cards
from app import pace as pace_rules
from app import priority as prio
from app.tenant import load_agent
from app.text import phone_said, street_key, unit_key, words

TENANT_DIR = Path(__file__).resolve().parent.parent / "tenants" / "summit-air"
BOOKED = ("booked", "queued")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
LEAD_KINDS = ("commercial", "membership", "install", "not_offered")
NEW_YORK = ZoneInfo("America/New_York")
DIGIT_WORDS = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def safety_config(folder: Path = TENANT_DIR) -> dict:
    """The tenant's safety config with its trade's phrases, as the server reads it."""
    return load_agent(folder)[0]["safety"]


def priority_config(folder: Path = TENANT_DIR) -> dict:
    """The tenant's priority config with its trade's rules, as the server reads it."""
    return load_agent(folder)[0]["priority"]


@dataclass
class Evidence:
    trace: dict
    safety: dict
    # The scratch ServiceTitan after the call (evals/world.py), on the in-process stack only.
    st_db: Path | None = None

    def st(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        if self.st_db is None:
            raise LookupError("no ServiceTitan record: the check runs on the in-process stack")
        conn = sqlite3.connect(self.st_db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @property
    def call(self) -> dict:
        return self.trace["call"]

    def turns(self) -> list[dict]:
        return [e for e in self.trace["events"] if e["type"] == "turn"]

    def agent_lines(self) -> list[str]:
        return [t["text"] for t in self.turns() if t["speaker"] == "agent"]

    def tools(self, name: str | None = None, ok: bool | None = None) -> list[dict]:
        found = [e for e in self.trace["events"] if e["type"] == "tool"]
        if name is not None:
            found = [e for e in found if e["tool"] == name]
        if ok is not None:
            found = [e for e in found if e["ok"] == ok]
        return found

    def bookings(self) -> list[dict]:
        return [b for b in self.trace["bookings"] if b["kind"] == "book" and b["status"] in BOOKED]

    def reply_after(self, caller_index: int) -> str | None:
        """The first agent line after the caller turn at this index in turns()."""
        for t in self.turns()[caller_index + 1 :]:
            if t["speaker"] == "agent":
                return t["text"]
        return None

    def lines_for(self, kind: str) -> str:
        return self.safety["kinds"][kind]["lines"]


@dataclass
class Check:
    validate: Callable[[Any], None]
    run: Callable[[Any, Evidence], list[str]]
    # True when the check forbids something, so a miss stands even on a call that never finished
    # (a booking before the read-back is wrong however the call ends). Other checks need the end.
    prohibits: Callable[[Any], bool] = lambda arg: False


def _one_of(*allowed: Any) -> Callable[[Any], None]:
    def validate(arg: Any) -> None:
        if arg not in allowed:
            raise ValueError(f"expected one of {allowed}, got {arg!r}")

    return validate


def _keys(allowed: set[str], short: tuple = ()) -> Callable[[Any], None]:
    def validate(arg: Any) -> None:
        if arg in short:
            return
        if not isinstance(arg, dict) or not set(arg) <= allowed:
            raise ValueError(f"expected {short or ''} or a mapping with keys from {sorted(allowed)}")

    return validate


def _strings(arg: Any) -> None:
    items = arg if isinstance(arg, list) else [arg]
    if not items or not all(isinstance(i, str) and i for i in items):
        raise ValueError("expected a string or a list of strings")
    for i in items:
        try:
            re.compile(i)
        except re.error as err:
            raise ValueError(f"bad regex {i!r}: {err}") from err


def _as_list(arg: Any) -> list[str]:
    return arg if isinstance(arg, list) else [arg]


def safety_lines(arg: str, ev: Evidence) -> list[str]:
    turns = ev.turns()
    signals = [i for i, t in enumerate(turns) if t["safety_action"] == "speak_lines"]
    agent = [words(a) for a in ev.agent_lines()]
    said = [k for k in ev.safety["kinds"] if any(words(ev.lines_for(k)) in a for a in agent)]
    if arg == "not_said":
        problems = []
        if signals:
            problems.append("a safety signal fired on a call that should not have one")
        if said:
            problems.append(f"the agent said the {', '.join(said)} safety lines")
        return problems
    if not signals:
        return ["no safety signal fired"]
    kind = ev.call["safety_kind"]
    reply = ev.reply_after(signals[0])
    problems = []
    if reply is None or not words(reply).startswith(words(ev.lines_for(kind))):
        problems.append(f"the first reply after the {kind} signal did not open with the lines")
    if ev.call["safety_state"] != "lines_said":
        problems.append(f"safety state is {ev.call['safety_state']}, not lines_said")
    if not ev.call["safety_flag_id"]:
        problems.append("no person was flagged for the safety call")
    return problems


def safety_check(arg: str, ev: Evidence) -> list[str]:
    turns = ev.turns()
    asked = [i for i, t in enumerate(turns) if t["safety_action"] == "ask_check"]
    if arg == "asked":
        return [] if asked else ["the vague-smell check question was never asked"]
    if arg == "asked_once":
        return [] if len(asked) == 1 else [f"the check question was asked {len(asked)} times"]
    if arg == "not_asked":
        return [] if not asked else ["a check question was asked on a call that needed none"]
    state = ev.call["safety_state"]
    return [] if state == "cleared" else [f"safety state is {state}, not cleared"]


def _readback_zip(ev: Evidence) -> str | None:
    raw = ev.call.get("readback_json")
    return json.loads(raw)["zip"] if raw else None


def booking(arg: Any, ev: Evidence) -> list[str]:
    booked = ev.bookings()
    if arg == "none":
        return [f"{len(booked)} booking(s) made, expected none"] if booked else []
    want = {"count": 1} if arg == "one" else {"count": 1, **arg}
    if len(booked) != want["count"]:
        return [f"{len(booked)} booking(s) made, expected {want['count']}"]
    problems = []
    for b in booked:
        if "priority" in want and b["priority"] != want["priority"]:
            problems.append(f"booking priority is {b['priority']}, expected {want['priority']}")
        if "seek_earlier" in want and bool(b["seek_earlier"]) != want["seek_earlier"]:
            problems.append(f"seek_earlier is {bool(b['seek_earlier'])}, expected the opposite")
        if "safety_override" in want and bool(b["safety_override"]) != want["safety_override"]:
            problems.append("safety_override does not match")
    if "zip" in want and _readback_zip(ev) != str(want["zip"]):
        problems.append(f"booked at ZIP {_readback_zip(ev)}, expected {want['zip']}")
    booked_args = [e["args"] for e in ev.tools("book_appointment", ok=True)]
    if "job_type" in want:
        types = [a.get("job_type") for a in booked_args]
        if want["job_type"] not in types:
            problems.append(f"job type {types}, expected {want['job_type']}")
    if "window" in want:
        windows = [a.get("slot", {}).get("window") for a in booked_args]
        if want["window"] not in windows:
            problems.append(f"window {windows}, expected {want['window']}")
    if "street" in want:
        raw = ev.call.get("readback_json")
        street = json.loads(raw)["street"] if raw else ""
        if street_key(want["street"]) not in street_key(street):
            problems.append(f"booked at street {street!r}, expected {want['street']!r}")
    if "new_address_account" in want:
        accounts = [a.get("new_address_account") for a in booked_args]
        if want["new_address_account"] not in accounts:
            problems.append(
                f"new_address_account {accounts}, expected {want['new_address_account']}"
            )
    return problems


# Rules that raise without a caller line to quote: the server's own readings and the side model.
UNQUOTED_RULES = ("safety_signal", "side_model", "combined")
QUOTE = re.compile(r'caller said "(.*?)"$')


def _invented_at_home(who_home: str, caller: str) -> list[str]:
    rules = priority_config()["rules"]
    return [
        f"who_home {who_home!r} says {rule['reason']!r}, which the caller never said"
        for rule in rules
        if rule.get("phrases") or "age_at_least" in rule
        if prio.said(rule, who_home) and not prio.said(rule, caller)
    ]


def priority_raise(arg: dict, ev: Evidence) -> list[str]:
    """Where each booking's priority came from (DECISIONS.md 072). `from_caller`: every rule that
    raised it quotes a caller line, and each quote is in a caller turn on the record, so nothing
    the model wrote raised it. `who_home_from_caller`: no priority rule fires on the booking's
    `who_home` that doesn't also fire on the caller's lines, so the model invented no one at home.
    `raised_after_wait`: the booking's flag for a raise on a fact first given after the caller
    heard the wait."""
    booked = ev.bookings()
    if not booked:
        return ["no booking to check the priority of"]
    said = [" ".join(t["text"].split()) for t in ev.turns() if t["speaker"] == "caller"]
    problems = []
    for b in booked:
        if "raised_after_wait" in arg and bool(b.get("raised_after_wait")) != arg["raised_after_wait"]:
            problems.append(
                f"raised_after_wait is {bool(b.get('raised_after_wait'))}, "
                f"expected {arg['raised_after_wait']}"
            )
        if arg.get("who_home_from_caller"):
            problems += _invented_at_home(b.get("who_home") or "", "\n".join(said))
        if not arg.get("from_caller"):
            continue
        rules = json.loads(b.get("priority_rules") or "[]")
        reasons = json.loads(b.get("priority_reason_list") or "[]")
        quoted_before = False
        for rule, reason in zip(rules, reasons, strict=False):
            quote = QUOTE.search(reason)
            if quote:
                quoted_before = True
                text = quote[1].removesuffix("...")
                if not any(text in line for line in said):
                    problems.append(f"{rule} quotes {text!r}, which no caller line says")
            # A line two rules fire on is quoted only with the first (app/priority.py).
            elif not (rule.startswith("bracket:") or rule in UNQUOTED_RULES or quoted_before):
                problems.append(f"{rule} raised the priority with no caller line to quote")
    return problems


def read_back(arg: str, ev: Evidence) -> list[str]:
    """No booking before a confirmed read-back, and the read-back sentence was said out loud before
    the confirmation (the server gate checks the sentence was returned, not that it was spoken)."""
    confirmed = spoken = False
    for e in ev.trace["events"]:
        if e["type"] == "turn" and e["speaker"] == "agent" and _reads_back(e["text"], ev):
            spoken = True
        if e["type"] != "tool" or not e["ok"]:
            continue
        if e["tool"] == "read_back" and e["args"].get("confirmed"):
            if not spoken:
                return ["the read-back was confirmed before the agent said it"]
            confirmed = True
        if e["tool"] == "book_appointment" and not confirmed:
            return ["booked before a confirmed read-back"]
    return []


def _reads_back(text: str, ev: Evidence) -> bool:
    raw = ev.call.get("readback_json")
    if not raw:
        return False
    zip_code = json.loads(raw)["zip"]
    spoken = " ".join(DIGIT_WORDS[int(d)] for d in zip_code)
    return zip_code in text or words(spoken) in words(text.replace("zero", "oh"))


def flag(arg: str, ev: Evidence) -> list[str]:
    person = [f for f in ev.trace["flags"] if f["kind"] == "person"]
    if arg == "none":
        return [f"{len(person)} person flag(s) raised, expected none"] if person else []
    if arg == "person":
        return [] if person else ["no person was flagged"]
    kinds = {f["kind"] for f in ev.trace["flags"]}
    return [] if arg in kinds else [f"no {arg} flag, flags were {sorted(kinds)}"]


def handoff(arg: Any, ev: Evidence) -> list[str]:
    """The cold handoff record: `none`, or the role and status it must have."""
    rows = ev.trace.get("handoffs", [])
    if arg == "none":
        return [f"{len(rows)} handoff(s), expected none"] if rows else []
    if not rows:
        return ["no handoff was decided"]
    row = rows[0]
    return [
        f"handoff {key} {row[key]}, expected {want}"
        for key, want in arg.items()
        if row[key] != want
    ]


def _handoff_arg(arg: Any) -> None:
    if arg == "none":
        return
    if not isinstance(arg, dict) or not set(arg) <= {"role", "status"}:
        raise ValueError("expected none, or a mapping with role and status")
    if arg.get("role", "office") not in ("office", "on_call"):
        raise ValueError("role is office or on_call")
    if arg.get("status", "handed_off") not in ("dialing", "handed_off", "failed", "not_dialed"):
        raise ValueError("status is dialing, handed_off, failed or not_dialed")


def lead(arg: str, ev: Evidence) -> list[str]:
    kinds = {e["args"].get("kind") for e in ev.tools("record_lead", ok=True)}
    # Leads the server keeps itself, such as work the company doesn't do (app/asks.py).
    kinds |= {r["kind"] for r in ev.trace.get("leads", [])}
    return [] if arg in kinds else [f"no {arg} lead recorded"]


JOB_KEYS = {"job_type", "unit", "street", "note"}


def _job_matches(want: dict, job: dict) -> bool:
    if "job_type" in want and job.get("job_type") != want["job_type"]:
        return False
    if "unit" in want and unit_key(job.get("unit") or "") != unit_key(str(want["unit"])):
        return False
    if "street" in want and _house_joined(want["street"]) not in _house_joined(job.get("street") or ""):
        return False
    if "note" in want:
        notes = json.loads(job.get("job_notes") or "[]")
        if not any(re.search(want["note"], n, re.IGNORECASE) for n in notes):
            return False
    return True


def _house_joined(street: str) -> str:
    """The street key with a Queens house number joined: "196-11 Hillside" and "19611 Hillside
    Avenue" name the same house (eval run 20261104-231045)."""
    return re.sub(r"(?<=\d) (?=\d)", "", street_key(street))


def jobs(arg: list[dict], ev: Evidence) -> list[str]:
    """Each wanted job is a different booked job on the call, read from the booking record: its job
    type, the unit and street it was booked at, a note it carries. No other job was booked."""
    booked = ev.bookings()
    if len(booked) != len(arg):
        return [f"{len(booked)} job(s) booked, expected {len(arg)}"]
    left = list(booked)
    problems = []
    for want in arg:
        found = next((j for j in left if _job_matches(want, j)), None)
        if found is None:
            seen = [{k: j.get(k) for k in ("job_type", "street", "unit")} for j in left]
            problems.append(f"no booked job matches {want}; left: {seen}")
        else:
            left.remove(found)
    return problems


def _jobs_arg(arg: Any) -> None:
    if not isinstance(arg, list) or not arg:
        raise ValueError("expected a list of jobs")
    for item in arg:
        if not isinstance(item, dict) or not set(item) <= JOB_KEYS or not item:
            raise ValueError(f"each job is a mapping with keys from {sorted(JOB_KEYS)}")


def job_note(arg: str, ev: Evidence) -> list[str]:
    """A note for the job on this call matches: one the server or agent kept for the job, or one
    the booking carried."""
    notes = [n["text"] for n in ev.trace.get("notes", []) if n["attach_to"] == "job"]
    for b in ev.bookings():
        notes += json.loads(b.get("job_notes") or "[]")
    if any(re.search(arg, n, re.IGNORECASE) for n in notes):
        return []
    return [f"no job note matches {arg!r}"]


def out_of_area(arg: Any, ev: Evidence) -> list[str]:
    zips = {r["zip"] for r in ev.trace["out_of_area"]}
    if arg == "recorded":
        return [] if zips else ["no out-of-area ZIP recorded"]
    return [] if str(arg) in zips else [f"out-of-area ZIPs {sorted(zips)}, expected {arg}"]


def tool_ok(arg: Any, ev: Evidence) -> list[str]:
    return [f"{n} never succeeded" for n in _as_list(arg) if not ev.tools(n, ok=True)]


def tool_not_called(arg: Any, ev: Evidence) -> list[str]:
    return [f"{n} was called" for n in _as_list(arg) if ev.tools(n)]


WRITES = ("book_appointment", "reschedule_appointment", "cancel_appointment", "record_move",
          "mark_do_not_call")


def no_changes(arg: bool, ev: Evidence) -> list[str]:
    changed = [n for n in WRITES if ev.tools(n, ok=True)]
    changed += [f"{b['kind']} row" for b in ev.trace["bookings"]]
    return [f"records changed: {', '.join(changed)}"] if changed else []


def outcome(arg: Any, ev: Evidence) -> list[str]:
    got = ev.call["outcome"]
    return [] if got in _as_list(arg) else [f"call outcome {got}, expected {arg}"]


def ended_by(arg: str, ev: Evidence) -> list[str]:
    """Who ended the call. Hanging up after the caller said goodbye counts as the caller's end
    (journey 3: the call ends when the caller hangs up or says goodbye)."""
    reason = ev.call["end_reason"]
    ends = ev.tools("end_call", ok=True)
    after_goodbye = bool(ends) and ends[-1]["args"].get("reason") == "caller_goodbye"
    by_caller = reason == "caller_hangup" or (reason == "agent_end" and after_goodbye)
    if (arg == "caller") == by_caller:
        return []
    return [f"call ended by {reason}, expected the {arg}"]


def callback(arg: Any, ev: Evidence) -> list[str]:
    rows = ev.trace["callbacks"]
    if arg == "none":
        return [f"{len(rows)} callback(s) promised, expected none"] if rows else []
    if not rows:
        return ["no callback was promised"]
    if not isinstance(arg, dict):
        return []
    # {day: 1, not_before: "12:00"}: the callback is due that many days after the call, no earlier
    # than that local time ("tomorrow, but not before noon").
    started = datetime.fromisoformat(ev.call["started_at"]).astimezone(NEW_YORK)
    day = started.date() + timedelta(days=arg.get("day", 0))
    earliest = datetime.combine(day, time.fromisoformat(arg.get("not_before", "00:00")), NEW_YORK)
    problems = []
    for row in rows:
        due = datetime.fromisoformat(row["due_at"]).astimezone(NEW_YORK)
        if due.date() != day or due < earliest:
            problems.append(f"callback due {due:%a %-I:%M %p}, expected {day:%a} at or after "
                            f"{earliest:%-I:%M %p}")
    return problems


def _callback_arg(arg: Any) -> None:
    if arg in ("promised", "none"):
        return
    if not isinstance(arg, dict) or not set(arg) <= {"day", "not_before"}:
        raise ValueError("expected promised, none, or a mapping with day and not_before")
    time.fromisoformat(arg.get("not_before", "00:00"))


def agent_says(arg: Any, ev: Evidence) -> list[str]:
    text = "\n".join(ev.agent_lines())
    return [f"the agent never said /{p}/" for p in _as_list(arg) if not re.search(p, text, re.IGNORECASE)]


def agent_never_says(arg: Any, ev: Evidence) -> list[str]:
    problems = []
    for p in _as_list(arg):
        hit = next((line for line in ev.agent_lines() if re.search(p, line, re.IGNORECASE)), None)
        if hit:
            problems.append(f"the agent said /{p}/: {hit[:80]!r}")
    return problems


def agent_says_at_most(arg: dict, ev: Evidence) -> list[str]:
    """Each pattern turns up in at most that many agent lines: a recommendation said once and
    offered once more, not pressed on every turn (eval round 3)."""
    problems = []
    for pattern, most in arg.items():
        hits = [line for line in ev.agent_lines() if re.search(pattern, line, re.IGNORECASE)]
        if len(hits) > most:
            problems.append(f"the agent said /{pattern}/ in {len(hits)} lines, at most {most} allowed")
    return problems


def _counts(arg: Any) -> None:
    if not isinstance(arg, dict) or not arg:
        raise ValueError("expected a mapping of regex to the most lines allowed")
    for pattern, most in arg.items():
        _strings(pattern)
        if not isinstance(most, int) or isinstance(most, bool) or most < 0:
            raise ValueError(f"expected a whole number for {pattern!r}, got {most!r}")


def booking_lead_in(arg: bool, ev: Evidence) -> list[str]:
    """The agent's line with the booking confirmation opens with a short line of the model's own,
    from the same reply that booked, rather than a separate filler reply or silence (eval round
    4: the caller hears something at once on the slowest turn)."""
    lines = [line for line in ev.agent_lines() if "You're all set for" in line]
    if not lines:
        return ["no booking confirmation was said"]
    lead = lines[0].split("You're all set for")[0].strip()
    return [] if lead else ["the booking confirmation came with no line before it"]


# The agent telling a caller they're booked. Said before any booking went through, it is untrue.
CLAIMS_BOOKED = (
    r"(you'?re|you are) (all set|booked|scheduled|on the schedule)|i'?ve (got you|booked)"
    r"|(booked|got) you (in|down|for)|(that'?s|it'?s|visit is) (booked|confirmed|scheduled)"
)
# The agent saying a window someone else just took is gone.
SAYS_GONE = (
    r"\b(just|already) (went|gone|filled|been (taken|booked)|got (taken|booked))|filled up"
    r"|no longer (open|available)|(isn'?t|is not|not) (open|available) any ?more"
    r"|someone (else )?(just )?(took|booked|grabbed)|\b(was|been|got) taken\b"
)
# A time the agent offers instead: a day or a window.
NAMES_A_TIME = (
    r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|morning|afternoon|evening|\d{1,2} to \d{1,2}|noon)\b"
)


def said_booked(arg: str, ev: Evidence) -> list[str]:
    """The agent says the caller is booked only once a booking went through on this call. The record
    keeps one agent line per turn, after that turn's tools, so a claim spoken in the same reply as
    a booking that failed comes after the failure here and is caught; one bundled with a booking
    that succeeded turned out true."""
    for e in ev.trace["events"]:
        if e["type"] == "tool" and e["tool"] == "book_appointment" and e["ok"]:
            return []
        if e["type"] == "turn" and e["speaker"] == "agent" and re.search(
            CLAIMS_BOOKED, e["text"], re.IGNORECASE
        ):
            return [f"told the caller they were booked before any booking: {e['text'][:80]!r}"]
    return []


def slot_taken(arg: str, ev: Evidence) -> list[str]:
    """A booking that came back slot_taken is said plainly: the agent's next line says the window
    went and, after saying so, names another time ("Monday afternoon just filled up" names only
    the time that went). Passes on a call where no booking came back taken."""
    events = ev.trace["events"]
    for i, e in enumerate(events):
        if not (e["type"] == "tool" and e["tool"] == "book_appointment"
                and e.get("refused") == "slot_taken"):
            continue
        reply = next(
            (t["text"] for t in events[i + 1:] if t["type"] == "turn" and t["speaker"] == "agent"),
            None,
        )
        if reply is None:
            return ["the call ended before the agent said the window was taken"]
        problems = []
        gone = re.search(SAYS_GONE, reply, re.IGNORECASE)
        if not gone:
            problems.append(f"did not say the window just went: {reply[:80]!r}")
        offered = reply[gone.end():] if gone else reply
        if not re.search(NAMES_A_TIME, offered, re.IGNORECASE):
            problems.append(f"did not offer another time: {reply[:80]!r}")
        return problems
    return []


PACE_KEYS = {"held", "source", "remembered", "address"}
PACE_SOURCES = ("default", "record", "linked", "request", "repeats", "older_adult")


def _pace_arg(arg: Any) -> None:
    if not isinstance(arg, dict) or not arg or not set(arg) <= PACE_KEYS:
        raise ValueError(f"expected a mapping with some of {sorted(PACE_KEYS)}")
    for key in ("held", "address"):
        if key in arg and not isinstance(arg[key], (int, float)):
            raise ValueError(f"{key}: expected a speed such as 0.85")
    if "remembered" in arg and arg["remembered"] != "none" and not isinstance(
        arg["remembered"], (int, float)
    ):
        raise ValueError("remembered: expected a speed or none")
    if "source" in arg and arg["source"] not in PACE_SOURCES:
        raise ValueError(f"source: expected one of {PACE_SOURCES}")


def _speed(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


def pace(arg: dict, ev: Evidence) -> list[str]:
    """The pace the server recorded (app/pace.py), and that every agent line was spoken at it: the
    speed in each agent line's timings is what the voice layer applied."""
    state = (ev.trace.get("pace") or [None])[0]
    if state is None:
        return ["the server kept no pace for this call"]
    problems = []
    changes = ev.trace.get("pace_changes") or []
    if "held" in arg and _speed(state["speed"]) != _speed(arg["held"]):
        problems.append(f"the call ended at {state['speed']}, expected {arg['held']}")
    if "source" in arg and state["source"] != arg["source"]:
        problems.append(f"the pace was set by {state['source']}, expected {arg['source']}")
    if "remembered" in arg:
        kept = ev.trace.get("caller_pace") or []
        got = _speed(kept[0]["speed"]) if kept else "none"
        want = arg["remembered"] if arg["remembered"] == "none" else _speed(arg["remembered"])
        if got != want:
            problems.append(f"remembered for the caller: {got}, expected {want}")
    address = {_speed(c["speed"]) for c in changes if c["scope"] == "address"}
    if "address" in arg and _speed(arg["address"]) not in address:
        problems.append(f"no request for the address at {arg['address']} was recorded")
    readback = json.loads(ev.call.get("readback_json") or "{}")
    street = (readback.get("street") or "").strip().lower()
    number = phone_said(readback["phone"]).lower() if readback.get("phone") else ""
    cfg = load_agent(TENANT_DIR)[0]["pace"]
    spoke_address = False
    for turn in ev.turns():
        if turn["speaker"] != "agent":
            continue
        got = _speed(turn["timings"].get("speed"))
        if got is None:
            problems.append(f"agent line {turn['seq']} carries no speed")
            continue
        text = turn["text"].strip().lower()
        # The address part of a read-back the caller asked to hear slower, at its own speed.
        if street and text.startswith(street) and got in address:
            spoke_address = True
            continue
        expected = _speed(cfg["normal"])
        for change in changes:
            if change["scope"] == "call" and change["seq"] < turn["seq"]:
                expected = _speed(change["speed"])
        # The address and number in every read-back are a step slower than the call.
        detail = (street and text.startswith(street)) or (number and text.startswith(number))
        if detail and got == _speed(pace_rules.detail_speed(expected, cfg)):
            continue
        if got != expected:
            problems.append(
                f"agent line {turn['seq']} was spoken at {got}; the server's pace was {expected}"
            )
    if "address" in arg and not spoke_address:
        problems.append("the address was never spoken at its own pace")
    return problems


def preferred_name(arg: str, ev: Evidence) -> list[str]:
    """The form of address the server kept from the caller's words (app/address_as.py): this
    name, or `none` when nothing was kept."""
    kept = [r["name"] for r in ev.trace.get("names", [])]
    if arg == "none":
        return [f"kept {kept[0]!r}, expected none"] if kept else []
    if kept != [arg]:
        return [f"kept {kept[0] if kept else 'nothing'}, expected {arg!r}"]
    return []


def tool_refused(arg: Any, ev: Evidence) -> list[str]:
    """Each refusal code came back from some tool on the call, such as `zip_mismatch`."""
    codes = {e["refused"] for e in ev.tools() if e["refused"]}
    return [f"no tool was refused with {code}" for code in _as_list(arg) if code not in codes]


OPEN = ("Scheduled", "Dispatched", "InProgress", "Hold")


def moved(arg: dict, ev: Evidence) -> list[str]:
    """A caller who moved (tools.record_move), read from ServiceTitan itself. `in_area: true`: the
    customer has an active location at `street` and `zip`, the old ones are inactive, and every
    open job of theirs is at an active location. `in_area: false`: nothing at the new address,
    the old location still active, and no open job left at it. `jobs` is how many open jobs
    there were to move or cancel."""
    rows = ev.trace.get("address_moves") or []
    if not rows:
        return ["no move was recorded"]
    move = rows[-1]
    problems = []
    if bool(move["in_area"]) != arg["in_area"]:
        problems.append(f"move recorded in_area={bool(move['in_area'])}")
    if "zip" in arg and move["zip"] != str(arg["zip"]):
        problems.append(f"moved to ZIP {move['zip']}, expected {arg['zip']}")
    try:
        locations = ev.st("SELECT * FROM locations WHERE customer_id = ?", (move["customer_id"],))
        jobs = ev.st(
            f"SELECT * FROM jobs WHERE customer_id = ? AND status IN ({','.join('?' * len(OPEN))})",
            (move["customer_id"], *OPEN),
        )
        cancelled = ev.st(
            "SELECT * FROM jobs WHERE customer_id = ? AND status = 'Canceled'",
            (move["customer_id"],),
        )
    except LookupError as err:
        return [str(err)]
    active = {loc["id"]: loc for loc in locations if loc["active"]}
    old = json.loads(move["old_location_ids"])
    at_new = [
        loc for loc in active.values()
        if loc["zip"] == move["zip"] and street_key(loc["street"]) == street_key(move["street"])
    ]
    if "street" in arg and street_key(arg["street"]) not in street_key(move["street"]):
        problems.append(f"moved to {move['street']!r}, expected {arg['street']!r}")
    if arg["in_area"]:
        if not at_new:
            problems.append("no active location at the new address")
        if any(i in active for i in old):
            problems.append("the old location is still active")
        stale = [j["id"] for j in jobs if j["location_id"] not in active]
        if stale:
            problems.append(f"open job(s) {stale} still point at an inactive location")
        if "jobs" in arg and len([j for j in jobs if at_new and j["location_id"] == at_new[0]["id"]]) < arg["jobs"]:
            problems.append(f"expected {arg['jobs']} open job(s) at the new address")
    else:
        if at_new:
            problems.append("a location was added at an address outside the area")
        left = [j["id"] for j in jobs if j["location_id"] in old]
        if left:
            problems.append(f"open job(s) {left} left at the old address")
        if "jobs" in arg and len(cancelled) < arg["jobs"]:
            problems.append(f"expected {arg['jobs']} job(s) cancelled, found {len(cancelled)}")
    return problems


def identity(arg: str, ev: Evidence) -> list[str]:
    """The name and address check (tools._identity_gate): `verified`, `mismatch`, or `none` (it
    never ran, as for a caller whose number is on the account). A verified change leaves the
    office's note on the job in ServiceTitan."""
    rows = ev.trace.get("identity_checks") or []
    statuses = [r["status"] for r in rows]
    if arg == "none":
        return [f"identity check ran: {statuses}"] if rows else []
    if arg not in statuses:
        return [f"identity check {statuses or 'never ran'}, expected {arg}"]
    if arg == "verified":
        changed = [b["fsm_job_id"] for b in ev.trace["bookings"]
                   if b["kind"] in ("cancel", "reschedule") and b["status"] == "done"]
        try:
            for job_id in changed:
                notes = ev.st("SELECT text FROM notes WHERE job_id = ?", (job_id,))
                if not any("unknown number" in n["text"] for n in notes):
                    return [f"no unknown-number note on job {job_id}"]
        except LookupError as err:
            return [str(err)]
    return []


def job_contact(arg: dict, ev: Evidence) -> list[str]:
    """The booked job's contact is another person's number (`phone`, digits), named with `for`,
    and the customer's own numbers in ServiceTitan don't include it."""
    booked = [b for b in ev.bookings() if b["fsm_job_id"]]
    if not booked:
        return ["nothing was booked"]
    want = re.sub(r"\D", "", str(arg["phone"]))
    try:
        job = ev.st("SELECT * FROM jobs WHERE id = ?", (booked[-1]["fsm_job_id"],))[0]
        contacts = ev.st("SELECT * FROM contacts WHERE customer_id = ?", (job["customer_id"],))
    except LookupError as err:
        return [str(err)]
    problems = []
    line = next((ln for ln in job["summary"].splitlines()
                 if ln.startswith("Contact:") and want[-4:] in re.sub(r"\D", "", ln)), "")
    if not line:
        problems.append("the job's contact line doesn't have that number")
    elif arg.get("for") and arg["for"].lower() not in line.lower():
        problems.append(f"the contact line {line!r} doesn't say {arg['for']!r}")
    if not any(ln.startswith("Account phone:") for ln in job["summary"].splitlines()):
        problems.append("the job doesn't show the account's own number")
    if any(c["phone_key"] == want for c in contacts):
        problems.append("the contact number was saved as one of the customer's own")
    if not contacts:
        problems.append("the customer has no number of their own")
    return problems


def booked_under(arg: str, ev: Evidence) -> list[str]:
    """The booking went on the customer with this name in ServiceTitan."""
    booked = [b for b in ev.bookings() if b["fsm_job_id"]]
    if not booked:
        return ["nothing was booked"]
    try:
        rows = ev.st(
            "SELECT c.name FROM jobs j JOIN customers c ON c.id = j.customer_id WHERE j.id = ?",
            (booked[-1]["fsm_job_id"],),
        )
    except LookupError as err:
        return [str(err)]
    name = rows[0]["name"] if rows else ""
    return [] if name == arg else [f"booked under {name!r}, expected {arg!r}"]


def heads_up(arg: str, ev: Evidence) -> list[str]:
    """A heads-up to the number on file was stored (app/heads_up.py): `recorded` for one that was
    sent, queued or recorded as would-have-called (the eval world's allowlist is empty, so never
    dialed), `none` for no heads-up at all."""
    rows = ev.trace.get("heads_ups") or []
    if arg == "none":
        return [f"a heads-up was stored: {[r['status'] for r in rows]}"] if rows else []
    live = [r for r in rows if r["status"] in ("queued", "dialing", "sent", "would_call")]
    return [] if live else [f"no heads-up recorded ({[r['status'] for r in rows]})"]


def _moved_arg(arg: Any) -> None:
    if not isinstance(arg, dict) or "in_area" not in arg:
        raise ValueError("expected {in_area, zip, street, jobs}")
    _keys({"in_area", "zip", "street", "jobs"})(arg)


def _contact_arg(arg: Any) -> None:
    if not isinstance(arg, dict) or "phone" not in arg:
        raise ValueError("expected {phone, for}")
    _keys({"phone", "for"})(arg)

def _strings_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    return []


def card_digits(arg: dict, ev: Evidence) -> list[str]:
    """No card digits anywhere the server stored (DECISIONS.md 080): no turn holds a number the
    redaction would still take out, and the card the caller read (`absent`, digits only) shows up
    nowhere in the record, not even its first eight digits run together."""
    problems = [
        f"turn {t.get('seq')} still holds card digits: {t['text']!r}"
        for t in ev.turns()
        if cards.redact(t["text"]) != t["text"]
    ]
    start = re.sub(r"\D", "", arg["absent"])[:8]
    for text in _strings_in(ev.trace):
        if start in re.sub(r"\D", "", text):
            problems.append(f"the card's digits are on the record: {text[:80]!r}")
            break
    return problems


def _card_arg(arg: Any) -> None:
    if not isinstance(arg, dict) or set(arg) != {"absent"}:
        raise ValueError("expected {absent: <the card number the caller reads>}")
    if len(re.sub(r"\D", "", str(arg["absent"]))) < 13:
        raise ValueError("absent must be a whole card number")


def end_reason(arg: str, ev: Evidence) -> list[str]:
    got = ev.call["end_reason"]
    return [] if got == arg else [f"call ended as {got}, expected {arg}"]


def safety_state(arg: Any, ev: Evidence) -> list[str]:
    """The gas and carbon monoxide state on the call record: the electrical route never sets it."""
    got = ev.call["safety_state"]
    return [] if got in _as_list(arg) else [f"safety state {got}, expected {arg}"]


def electrical(arg: str, ev: Evidence) -> list[str]:
    """The electrical lines (app/safety.py): `said` once, word for word, or `not_said`."""
    rows = ev.trace.get("electrical", [])
    said = bool(rows) and rows[0]["said_at"] is not None
    if arg == "not_said":
        return ["the electrical lines were said"] if said else []
    if not said:
        return ["the electrical lines were never said"]
    lines = " ".join(ev.safety["electrical"]["lines"].split())
    spoken = [t for t in ev.agent_lines() if lines in " ".join(t.split())]
    return [] if len(spoken) == 1 else [f"the electrical lines were spoken {len(spoken)} times"]


def _recorded_or_zip(arg: Any) -> None:
    if arg != "recorded" and not re.fullmatch(r"\d{5}", str(arg)):
        raise ValueError(f"expected recorded or a five-digit ZIP, got {arg!r}")


def _any(arg: Any) -> None:
    if not isinstance(arg, (str, list)):
        # ValueError, not TypeError: pydantic reports only ValueError as a scenario error.
        raise ValueError("expected a tool name or a list of tool names")  # noqa: TRY004


CHECKS: dict[str, Check] = {
    "safety_lines": Check(_one_of("said_first", "not_said"), safety_lines, lambda arg: True),
    "safety_check": Check(
        _one_of("asked", "asked_once", "not_asked", "cleared"),
        safety_check,
        lambda arg: arg == "not_asked",
    ),
    "booking": Check(
        _keys({"count", "priority", "zip", "job_type", "seek_earlier", "safety_override", "window",
               "street", "new_address_account"}, ("none", "one")),
        booking,
        lambda arg: arg == "none",
    ),
    "read_back": Check(_one_of("before_booking"), read_back, lambda arg: True),
    "priority_raise": Check(
        _keys({"from_caller", "raised_after_wait", "who_home_from_caller"}), priority_raise
    ),
    "flag": Check(
        _one_of("none", "person", "safety", "safety_check_cleared", "approval_check"),
        flag,
        lambda arg: arg == "none",
    ),
    "lead": Check(_one_of(*LEAD_KINDS), lead),
    "jobs": Check(_jobs_arg, jobs),
    "job_note": Check(_strings, job_note),
    "handoff": Check(_handoff_arg, handoff, lambda arg: arg == "none"),
    "out_of_area": Check(_recorded_or_zip, out_of_area),
    "tool_ok": Check(_any, tool_ok),
    "tool_not_called": Check(_any, tool_not_called, lambda arg: True),
    "no_changes": Check(_one_of(True), no_changes, lambda arg: True),
    "outcome": Check(_any, outcome),
    "ended_by": Check(_one_of("caller", "agent"), ended_by),
    "callback": Check(_callback_arg, callback, lambda arg: arg == "none"),
    "agent_says": Check(_strings, agent_says),
    "agent_never_says": Check(_strings, agent_never_says, lambda arg: True),
    "agent_says_at_most": Check(_counts, agent_says_at_most, lambda arg: True),
    "booking_lead_in": Check(_one_of(True), booking_lead_in),
    "said_booked": Check(_one_of("only_after_booking"), said_booked, lambda arg: True),
    "slot_taken": Check(_one_of("told_plainly"), slot_taken),
    "pace": Check(_pace_arg, pace),
    "preferred_name": Check(_any, preferred_name, lambda arg: arg == "none"),
    "tool_refused": Check(_any, tool_refused),
    "moved": Check(_moved_arg, moved),
    "identity": Check(_one_of("verified", "mismatch", "none"), identity),
    "job_contact": Check(_contact_arg, job_contact),
    "booked_under": Check(_strings, booked_under),
    "heads_up": Check(_one_of("recorded", "none"), heads_up, lambda arg: arg == "none"),
    "card_digits": Check(_card_arg, card_digits, lambda arg: True),
    "end_reason": Check(_one_of("stepped_away", "agent_end", "caller_hangup"), end_reason),
    "safety_state": Check(_any, safety_state),
    "electrical": Check(_one_of("said", "not_said"), electrical, lambda arg: arg == "not_said"),
}


def run_checks(must: list[dict], ev: Evidence, prohibitions_only: bool = False) -> list[str]:
    """Every failure across the scenario's checks, each prefixed with its check name."""
    failures = []
    for item in must:
        (name, arg), = item.items()
        if prohibitions_only and not CHECKS[name].prohibits(arg):
            continue
        failures += [f"{name}: {p}" for p in CHECKS[name].run(arg, ev)]
    return failures


def check_names(must: list[dict], prohibitions_only: bool = False) -> list[str]:
    """The names of the checks run_checks evaluates for these `must` entries, once each, in order."""
    names: list[str] = []
    for item in must:
        (name, arg), = item.items()
        if prohibitions_only and not CHECKS[name].prohibits(arg):
            continue
        if name not in names:
            names.append(name)
    return names
