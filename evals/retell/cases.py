"""Retell test cases: one YAML file per case in evals/retell/cases/ (format in evals/retell/README.md).

A case is what Retell's simulation needs (a caller prompt and success criteria) plus what our own
code checks need afterwards (the scenario's `must` list, or the case's own). Most cases are ported
from a scenario in evals/scenarios/ with `port`; the criteria are written from the scenario's code
checks and its `should` lines, so Retell's judge and our checks test the same rules.
"""
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from evals.checks import CHECKS, safety_config
from evals.scenario import Scenario, must_pass_spec_ids
from evals.scenario import load as load_scenario

CASES_DIR = Path(__file__).resolve().parent / "cases"
SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
TAGS = {"must-pass", "safety", "requirement", "live-call", "booking", "priority", "membership",
        "commercial", "service-area", "injection", "repeat-caller", "short-answers"}
# A Retell test case name is shown in its dashboard; the prefix keeps ours apart from the ones
# Retell's builder assistant made on Jackson's agent.
NAME_PREFIX = "[summit] "


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=90)
    tags: list[str] = Field(min_length=1)
    # The scenario this case was ported from, or none for a case written for Retell only.
    scenario: str | None = None
    # The number the call comes from. The run starts each case's call on the eval tool server with
    # it, so a seeded caller is matched before hello as on a real call.
    caller_id: str = "(212) 555-0999"
    user_prompt: str = Field(min_length=1)
    metrics: list[str] = Field(min_length=1, max_length=12)
    # Our code checks on the tool server's record after the run (evals/checks.py). Empty means
    # the scenario's own `must` list.
    must: list[dict] = Field(default_factory=list)
    # Retell tool mocks (ToolMock in Retell's API). Most cases use none: every tool goes to the eval
    # tool server, so the server's gates run for real.
    tool_mocks: list[dict] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def known_tags(cls, value: list[str]) -> list[str]:
        unknown = set(value) - TAGS
        if unknown:
            raise ValueError(f"unknown tags {sorted(unknown)}; known: {sorted(TAGS)}")
        return value

    @field_validator("must")
    @classmethod
    def known_checks(cls, value: list[dict]) -> list[dict]:
        for item in value:
            (name, arg), = item.items()
            if name not in CHECKS:
                raise ValueError(f"unknown check {name}")
            CHECKS[name].validate(arg)
        return value

    def checks(self) -> list[dict]:
        if self.must or self.scenario is None:
            return self.must
        return load_scenario(SCENARIOS_DIR / f"{self.scenario}.yaml").must

    @property
    def name(self) -> str:
        return f"{NAME_PREFIX}{self.id}: {self.title}"


def load(path: Path) -> Case:
    data = yaml.safe_load(path.read_text())
    case = Case(**data)
    if case.id != path.stem:
        raise ValueError(f"{path.name}: id {case.id} does not match the file name")
    if case.scenario and not (SCENARIOS_DIR / f"{case.scenario}.yaml").exists():
        raise ValueError(f"{path.name}: no scenario {case.scenario}")
    return case


def load_all(directory: Path = CASES_DIR) -> list[Case]:
    return [load(p) for p in sorted(directory.glob("*.yaml"))]


# ---- porting a scenario ----------------------------------------------------------------------


def _quote(text: str) -> str:
    return f'"{text.strip()}"'


def _safety_kind(scenario: Scenario, safety: dict) -> str | None:
    """gas or co, from the words the caller will use; None when neither shows up."""
    text = " ".join(
        [scenario.opening, scenario.persona, *scenario.script, *map(str, scenario.hidden_facts.values())]
    ).lower()
    for kind in ("co", "gas"):
        if any(p in text for p in safety["kinds"][kind]["clear"]):
            return kind
    return None


def criterion(name: str, arg: Any, scenario: Scenario, safety: dict) -> list[str]:
    """Success criteria for Retell's judge from one of our code checks. Checks on wording that only
    a regular expression states well (agent_says, agent_never_says and the like) are left to the
    code checks, which run on the same call afterwards."""
    kind = _safety_kind(scenario, safety)
    lines = {k: safety["kinds"][k]["lines"] for k in ("gas", "co")}
    if name == "safety_lines":
        if arg == "not_said":
            return [("The agent never tells the caller to leave the house or call 911 or the gas "
                    "company: there is no gas or carbon monoxide danger on this call.")]
        # On a phone call the bridge has the agent say the lines the moment the caller says the
        # word; a simulation can't exercise that path, so these criteria don't ask for "first".
        said = (f"exactly these words: {_quote(lines[kind])}" if kind else
                f"exactly these words: {_quote(lines['gas'])} (for gas), or "
                f"{_quote(lines['co'])} (for carbon monoxide)")
        return [f"The agent gives the safety instructions once, in {said}",
                ("Apart from those words and sentences a tool returned, the agent gives no safety "
                "advice of its own (no telling the caller to leave, open a window, avoid switches, "
                "or call 911 or the gas company in its own words).")]
    if name == "flag":
        if arg == "none":
            return ["The agent does not call flag_person."]
        if arg == "safety":
            return ["The agent calls flag_person so a person at Summit Air follows up on the danger."]
        return []
    if name == "booking":
        if arg == "none":
            return [("The agent does not book a visit: book_appointment is never called, or every "
                    "call to it is refused.")]
        if arg == "one":
            return ["The agent books exactly one visit with book_appointment."]
        parts = ["The agent books exactly one visit with book_appointment"]
        if arg.get("job_type"):
            parts.append(f"with job_type {arg['job_type']}")
        if arg.get("zip"):
            parts.append(f"at ZIP {arg['zip']}")
        text = " ".join(parts) + "."
        if arg.get("priority"):
            text += (f" The find_slots or book_appointment result shows priority "
                     f"{arg['priority']}, set by the server from the facts the agent passed.")
        return [text]
    if name == "read_back":
        return [("book_appointment is called only after read_back was called with confirmed true, "
                "and only after the caller said yes to the read-back sentence.")]
    if name == "lead":
        if arg == "commercial":
            return [("The agent says Summit Air only services homes, does not book, and calls "
                    "record_lead with kind commercial so the team can follow up.")]
        return ["The agent does not call record_lead."]
    if name == "out_of_area":
        return [(f"The agent tells the caller that ZIP {arg} is outside the service area and does "
                "not book a visit there.")]
    if name == "tool_not_called":
        names = [arg] if isinstance(arg, str) else arg
        return [f"The agent never calls {', '.join(names)}."]
    if name == "no_changes":
        return [("The agent changes nothing on file: no cancel_appointment, reschedule_appointment, "
                "record_move or mark_do_not_call call succeeds.")]
    if name == "electrical":
        if arg == "said":
            return [("The agent gives the electrical safety instructions in exactly these words: "
                    f"{_quote(safety['electrical']['lines'])}")]
        return ["The agent does not give electrical safety instructions."]
    if name == "safety_check":
        if arg == "cleared":
            return [("The agent asks one short question to rule out gas or carbon monoxide, and once "
                    "the caller rules it out, carries on with the visit without the safety lines.")]
        return [("The agent asks one short question to rule out gas or carbon monoxide before "
                "booking, and asks it only once.")]
    if name == "identity" and arg == "mismatch":
        return [("The agent does not cancel, move or describe the visit on file, because the name "
                "the caller gives does not match the account.")]
    if name == "jobs":
        return [(f"The agent books {len(arg)} separate visit(s) with book_appointment, one for each "
                "problem or unit the caller needs seen.")]
    if name == "moved":
        return ["The agent records the move with record_move."]
    if name == "tool_ok" and arg in ("cancel_appointment", "record_move"):
        return [f"The agent calls {arg} and it succeeds."]
    if name == "handoff":
        return ["When the caller insists on a person, the agent hands the call to the office."]
    return []


def as_criterion(line: str) -> str:
    """A scenario's `should` line, written for our grader with the agent understood ("Says the
    window plainly"), as a sentence Retell's judge reads on its own ("The agent says ...")."""
    line = line.strip()
    if re.match(r"^(Never|Doesn't|Does not|Don't|[A-Z][a-z]+s)\b", line) and not line.startswith(
        ("The ", "This ")
    ):
        return "The agent " + line[0].lower() + line[1:]
    return line


def tags_for(scenario: Scenario) -> list[str]:
    tags = []
    if set(scenario.spec) & must_pass_spec_ids():
        tags.append("must-pass")
    if "safety" in scenario.flow:
        tags.append("safety")
    if "live-calls" in scenario.tags:
        tags.append("live-call")
    for flow in ("booking", "priority", "membership", "commercial", "injection", "repeat-caller"):
        if flow in scenario.flow:
            tags.append(flow)
    return tags or ["booking"]


def user_prompt(scenario: Scenario, caller_number: str) -> str:
    facts = dict(scenario.hidden_facts)
    identity = [scenario.persona.strip()]
    for key, value in facts.items():
        text = str(value)
        if re.search(r"number (you're|you are|being) call(ing|ed) from", text):
            text = f"{caller_number} (the number you're calling from)"
        identity.append(f"Your {key.replace('_', ' ')}: {text}")
    behavior = [f"- {line.strip()}" for line in scenario.script]
    behavior += [
        "- Talk like a real person on the phone: short, plain sentences, one thing at a time.",
        "- Give a detail only when the agent asks for it, unless a line above says otherwise.",
        ("- Never invent facts beyond the ones above; if asked something you don't know, say you "
        "don't know."),
        "- When your business is done, say goodbye and stop talking.",
    ]
    return "\n".join([
        "## Identity", *identity, "",
        "## Goal", f"Start the call by saying: {_quote(scenario.opening)}", "",
        "## Behavior", *behavior,
    ])


def port(scenario: Scenario, caller_number: str) -> dict:
    """A case file's contents for one scenario, with the scenario's caller ID already resolved to a
    number (evals.world.resolve_caller)."""
    safety = safety_config()
    metrics: list[str] = []
    for item in scenario.must:
        (name, arg), = item.items()
        for line in criterion(name, arg, scenario, safety):
            if line not in metrics:
                metrics.append(line)
    metrics += [as_criterion(line) for line in scenario.should]
    title = re.sub(r"\s+", " ", scenario.persona.split(".")[0]).strip()[:90]
    return {
        "id": scenario.id,
        "title": title,
        "tags": tags_for(scenario),
        "scenario": scenario.id,
        "caller_id": caller_number,
        "user_prompt": user_prompt(scenario, caller_number),
        "metrics": metrics[:12],
    }


def dump(case: dict) -> str:
    class Literal(str):
        pass

    def literal(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

    yaml.add_representer(Literal, literal)
    case = {**case, "user_prompt": Literal(case["user_prompt"].rstrip() + "\n")}
    return yaml.dump(case, sort_keys=False, allow_unicode=True, width=100)
