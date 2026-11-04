"""Code checks: pass or fail from the tool server's record of the call, never from a model.

Each check reads the call's timeline (app/trace.py, the same shape /debug/calls/{id} returns), so the
in-process stack and the Retell baseline are judged by the same evidence. A scenario lists checks
under `must:` as `- <check>: <argument>`; a check returns the reasons it failed, or nothing.
"""
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.tenant import load_agent
from app.text import street_key, words

TENANT_DIR = Path(__file__).resolve().parent.parent / "tenants" / "summit-air"
BOOKED = ("booked", "queued")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
LEAD_KINDS = ("commercial", "membership", "install")
NEW_YORK = ZoneInfo("America/New_York")
DIGIT_WORDS = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def safety_config(folder: Path = TENANT_DIR) -> dict:
    """The tenant's safety config with its trade's phrases, as the server reads it."""
    return load_agent(folder)[0]["safety"]


@dataclass
class Evidence:
    trace: dict
    safety: dict

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
    return [] if arg in kinds else [f"no {arg} lead recorded"]


def out_of_area(arg: Any, ev: Evidence) -> list[str]:
    zips = {r["zip"] for r in ev.trace["out_of_area"]}
    if arg == "recorded":
        return [] if zips else ["no out-of-area ZIP recorded"]
    return [] if str(arg) in zips else [f"out-of-area ZIPs {sorted(zips)}, expected {arg}"]


def tool_ok(arg: Any, ev: Evidence) -> list[str]:
    return [f"{n} never succeeded" for n in _as_list(arg) if not ev.tools(n, ok=True)]


def tool_not_called(arg: Any, ev: Evidence) -> list[str]:
    return [f"{n} was called" for n in _as_list(arg) if ev.tools(n)]


WRITES = ("book_appointment", "reschedule_appointment", "cancel_appointment", "mark_do_not_call")


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
    normal = load_agent(TENANT_DIR)[0]["pace"]["normal"]
    spoke_address = False
    for turn in ev.turns():
        if turn["speaker"] != "agent":
            continue
        got = _speed(turn["timings"].get("speed"))
        if got is None:
            problems.append(f"agent line {turn['seq']} carries no speed")
            continue
        # The address part of a slower read-back, spoken at its own speed.
        if street and turn["text"].strip().lower().startswith(street) and got in address:
            spoke_address = True
            continue
        expected = _speed(normal)
        for change in changes:
            if change["scope"] == "call" and change["seq"] < turn["seq"]:
                expected = _speed(change["speed"])
        if got != expected:
            problems.append(
                f"agent line {turn['seq']} was spoken at {got}; the server's pace was {expected}"
            )
    if "address" in arg and not spoke_address:
        problems.append("the address was never spoken at its own pace")
    return problems


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
    "flag": Check(
        _one_of("none", "person", "safety", "safety_check_cleared", "approval_check"),
        flag,
        lambda arg: arg == "none",
    ),
    "lead": Check(_one_of(*LEAD_KINDS), lead),
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
