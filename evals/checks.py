"""Code checks: pass or fail from the tool server's record of the call, never from a model.

Each check reads the call's timeline (app/trace.py, the same shape /debug/calls/{id} returns), so the
in-process stack and the Retell baseline are judged by the same evidence. A scenario lists checks
under `must:` as `- <check>: <argument>`; a check returns the reasons it failed, or nothing.
"""
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.text import words

TENANT_AGENT = Path(__file__).resolve().parent.parent / "tenants" / "summit-air" / "agent.yaml"
BOOKED = ("booked", "queued")
PRIORITIES = ("Low", "Normal", "High", "Urgent")
LEAD_KINDS = ("commercial", "membership", "install")


def safety_config(path: Path = TENANT_AGENT) -> dict:
    return yaml.safe_load(path.read_text())["safety"]


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
    if reply is None or words(ev.lines_for(kind)) not in words(reply):
        problems.append(f"the first reply after the {kind} signal was not the safety lines")
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
    if "job_type" in want:
        types = [e["args"].get("job_type") for e in ev.tools("book_appointment", ok=True)]
        if want["job_type"] not in types:
            problems.append(f"job type {types}, expected {want['job_type']}")
    return problems


def read_back(arg: str, ev: Evidence) -> list[str]:
    seen_confirmed = False
    for e in ev.tools(ok=True):
        if e["tool"] == "read_back" and e["args"].get("confirmed"):
            seen_confirmed = True
        if e["tool"] == "book_appointment" and not seen_confirmed:
            return ["booked before a confirmed read-back"]
    return []


def flag(arg: str, ev: Evidence) -> list[str]:
    person = [f for f in ev.trace["flags"] if f["kind"] == "person"]
    if arg == "none":
        return [f"{len(person)} person flag(s) raised, expected none"] if person else []
    if arg == "person":
        return [] if person else ["no person was flagged"]
    kinds = {f["kind"] for f in ev.trace["flags"]}
    return [] if arg in kinds else [f"no {arg} flag, flags were {sorted(kinds)}"]


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
    reason = ev.call["end_reason"]
    expected = {"caller": ("caller_hangup",), "agent": ("agent_end",)}[arg]
    return [] if reason in expected else [f"call ended by {reason}, expected the {arg}"]


def callback(arg: str, ev: Evidence) -> list[str]:
    rows = ev.trace["callbacks"]
    if arg == "none":
        return [f"{len(rows)} callback(s) promised, expected none"] if rows else []
    return [] if rows else ["no callback was promised"]


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


def _recorded_or_zip(arg: Any) -> None:
    if arg != "recorded" and not re.fullmatch(r"\d{5}", str(arg)):
        raise ValueError(f"expected recorded or a five-digit ZIP, got {arg!r}")


def _any(arg: Any) -> None:
    if not isinstance(arg, (str, list)):
        # ValueError, not TypeError: pydantic reports only ValueError as a scenario error.
        raise ValueError("expected a tool name or a list of tool names")  # noqa: TRY004


CHECKS: dict[str, Check] = {
    "safety_lines": Check(_one_of("said_first", "not_said"), safety_lines),
    "safety_check": Check(_one_of("asked", "cleared"), safety_check),
    "booking": Check(
        _keys({"count", "priority", "zip", "job_type", "seek_earlier", "safety_override"},
              ("none", "one")),
        booking,
    ),
    "read_back": Check(_one_of("before_booking"), read_back),
    "flag": Check(_one_of("none", "person", "safety", "safety_check_cleared"), flag),
    "lead": Check(_one_of(*LEAD_KINDS), lead),
    "out_of_area": Check(_recorded_or_zip, out_of_area),
    "tool_ok": Check(_any, tool_ok),
    "tool_not_called": Check(_any, tool_not_called),
    "no_changes": Check(_one_of(True), no_changes),
    "outcome": Check(_any, outcome),
    "ended_by": Check(_one_of("caller", "agent"), ended_by),
    "callback": Check(_one_of("promised", "none"), callback),
    "agent_says": Check(_strings, agent_says),
    "agent_never_says": Check(_strings, agent_never_says),
}


def run_checks(must: list[dict], ev: Evidence) -> list[str]:
    """Every failure across the scenario's checks, each prefixed with its check name."""
    failures = []
    for item in must:
        (name, arg), = item.items()
        failures += [f"{name}: {p}" for p in CHECKS[name].run(arg, ev)]
    return failures
