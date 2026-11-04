"""Scenario files: one YAML file per simulated call in evals/scenarios/ (format in evals/README.md).

A scenario is a persona, hidden facts, a behavior script and the checks that decide pass or fail.
Every scenario carries tags (flow:, difficulty:, trait:, journey:, spec:, must-pass) that suites
filter on.
"""
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evals.checks import CHECKS
from evals.results import SLUG, Difficulty, Flow, Trait

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"
SPEC = ROOT / "docs" / "spec.md"
NEW_YORK = ZoneInfo("America/New_York")
# A Monday at 9 a.m. in New York, inside business hours, unless a scenario sets its own time.
DEFAULT_TIME = "2026-11-02T09:00"


class ScenarioError(ValueError):
    """A scenario file is unreadable or invalid."""


def must_pass_spec_ids(spec_text: str | None = None) -> set[str]:
    """The spec lines marked **M** in docs/spec.md. One miss on these fails a run."""
    text = spec_text if spec_text is not None else SPEC.read_text()
    return set(re.findall(r"^\| (S-\d+[a-z]?) \*\*M\*\*", text, flags=re.MULTILINE))


class Weather(BaseModel):
    """The outside temperature the server reads on the call, stored as a fresh reading at the
    scenario's start (app/weather.py). Unset, the server has none, as when the weather service is
    down, and a no-heat call starts as if it were cold out."""

    model_config = ConfigDict(extra="forbid")

    now_f: float
    low_f: float


class EarlierCall(BaseModel):
    """A call from the same number that dropped `minutes_ago` before the scenario starts, already on
    the record (journey 7, a dropped call that resumes). Each turn is `{caller: ...}` or
    `{agent: ...}`, in order."""

    model_config = ConfigDict(extra="forbid")

    minutes_ago: int = Field(ge=1, le=600)
    turns: list[dict[Literal["caller", "agent"], str]] = Field(min_length=1)
    # How it ended: the line dropped, or the agent hung up after the caller stepped away and never
    # came back (DECISIONS.md 081).
    end_reason: Literal["dropped", "stepped_away"] = "dropped"

    @field_validator("turns")
    @classmethod
    def one_speaker(cls, value: list[dict]) -> list[dict]:
        if any(len(t) != 1 for t in value):
            raise ValueError("each turn is one {caller: ...} or {agent: ...}")
        return value


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=SLUG)
    flow: list[Flow] = Field(min_length=1)
    difficulty: Difficulty
    traits: list[Trait] = Field(default_factory=list)
    journeys: list[str] = Field(min_length=1)
    spec: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # "unknown" for a number not on file, "seed:<key>" for a caller the mock seeds
    # (servicetitan_mock/README.md), or a phone number.
    caller_id: str = "unknown"
    time: str = DEFAULT_TIME
    persona: str = Field(min_length=1)
    hidden_facts: dict[str, Any] = Field(default_factory=dict)
    opening: str = Field(min_length=1)
    script: list[str] = Field(default_factory=list)
    max_turns: int = Field(16, ge=2, le=40)
    earlier_call: EarlierCall | None = None
    # The scheduling system failing: "booking" fails the writes that book (new jobs, customers,
    # locations, leads) while lookups and availability still work, as when it goes down mid-booking
    # (spec S-48); "all" fails every request.
    fsm_down: Literal["booking", "all"] | None = None
    # Words the simulated speech-to-text writes wrong, applied to the caller's line before it is
    # roughened: {"Houston": "Hughes Tin"}. Spelled letters are left alone.
    misheard: dict[str, str] = Field(default_factory=dict)
    # The office and on-call transfer numbers set in the scratch world (made-up numbers; nothing is
    # dialed), and what the phone company does with a cold handoff's SIP transfer: "sent" ends the
    # call as handed off, "fails" brings the agent back. Unset, no number is set, as on a tenant
    # with none.
    transfer: Literal["sent", "fails"] | None = None
    weather: Weather | None = None
    # The caller asks the agent to wait and never comes back: once a caller line puts the call on
    # hold, the worker's silence timer runs on a short clock (DECISIONS.md 081).
    steps_away: bool = False
    must: list[dict[str, Any]] = Field(min_length=1)
    should: list[str] = Field(default_factory=list)

    @field_validator("journeys")
    @classmethod
    def journey_ids(cls, value: list[str]) -> list[str]:
        for j in value:
            if not re.fullmatch(r"J([1-9]|1[0-4])|spine|tool-failure", j):
                raise ValueError(f"unknown journey {j!r}")
        return value

    @field_validator("spec")
    @classmethod
    def spec_ids(cls, value: list[str]) -> list[str]:
        for s in value:
            if not re.fullmatch(r"S-\d+[a-z]?", s):
                raise ValueError(f"not a spec id: {s!r}")
        return value

    @field_validator("must")
    @classmethod
    def known_checks(cls, value: list[dict]) -> list[dict]:
        for item in value:
            if len(item) != 1:
                raise ValueError(f"each must item has one check name, got {sorted(item)}")
            (name, arg), = item.items()
            if name not in CHECKS:
                raise ValueError(f"unknown check {name!r}")
            CHECKS[name].validate(arg)
        return value

    @field_validator("time")
    @classmethod
    def local_time(cls, value: str) -> str:
        datetime.fromisoformat(value)
        return value

    @property
    def start_time(self) -> datetime:
        """The scenario's clock start, as an aware UTC datetime."""
        return datetime.fromisoformat(self.time).replace(tzinfo=NEW_YORK)

    @property
    def must_pass(self) -> bool:
        return bool(set(self.spec) & must_pass_spec_ids())

    def all_tags(self) -> set[str]:
        tags = {f"flow:{f}" for f in self.flow}
        tags.add(f"difficulty:{self.difficulty}")
        tags |= {f"trait:{t}" for t in self.traits}
        tags |= {f"journey:{j}" for j in self.journeys}
        tags |= {f"spec:{s}" for s in self.spec}
        tags |= {f"id:{self.id}"}
        tags |= set(self.tags)
        if self.must_pass:
            tags.add("must-pass")
        return tags


def load(path: Path) -> Scenario:
    try:
        data = yaml.safe_load(path.read_text())
        scenario = Scenario.model_validate(data)
    except (OSError, yaml.YAMLError) as err:
        raise ScenarioError(f"cannot read {path.name}: {err}") from err
    except ValidationError as err:
        raise ScenarioError(f"{path.name} is not a valid scenario: {err}") from err
    if scenario.id != path.stem:
        raise ScenarioError(f"{path.name}: id {scenario.id!r} does not match the file name")
    return scenario


def load_all(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    return [load(p) for p in sorted(directory.glob("*.yaml"))]


def scenario_path(scenario_id: str, directory: Path = SCENARIOS_DIR) -> Path:
    return directory / f"{scenario_id}.yaml"
