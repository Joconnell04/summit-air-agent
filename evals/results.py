"""The results file the eval harness writes, one JSON file per run.

This file is the source of truth for a run. LangSmith (evals/langsmith_sync.py) only mirrors it.
"""
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Run ids become file names and LangSmith experiment names, so keep them plain.
SLUG = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class ResultsError(ValueError):
    """A results file is missing, unreadable or fails validation."""


# The labels in evals/README.md. A typo here would silently split one tag into two in LangSmith.
Flow = Literal[
    "booking", "safety", "priority", "membership", "commercial", "renter",
    "repeat-caller", "after-hours", "off-topic", "injection", "spam",
]
Difficulty = Literal["easy", "standard", "rigorous", "adversarial"]
Trait = Literal[
    "stutter", "filler", "self-correction", "late-correction", "split-digits",
    "transcription-errors", "interruption", "vague", "side-question", "two-speakers",
    "mind-change", "rude", "hard-of-hearing",
]
# "error" means the run produced no verdict, for example the simulated caller looped (decision 06).
# It is not a failure and is left out of pass rates.
Outcome = Literal["pass", "fail", "error"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turn(Strict):
    role: Literal["caller", "agent"]
    text: str
    # On the audio rig an agent line's `text` is what the caller heard (the rig's transcription of
    # the agent audio); `said` is the platform's own text of that line, when it reports one.
    said: str | None = None


class ToolCall(Strict):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class TurnTiming(Strict):
    """One agent turn, timed from the caller finishing to the agent's first word (docs/latency.md).
    A lookup turn is one where the agent called a tool."""

    kind: Literal["plain", "lookup"]
    reply_delay_ms: float = Field(ge=0)
    model_first_token_ms: float = Field(ge=0)
    tool_ms: float = Field(0, ge=0)
    second_hop_ms: float | None = Field(None, ge=0)
    # Audio rig only (evals/audio_rig): how long the caller's line and the agent's reply lasted.
    # There `reply_delay_ms` runs from the end of the caller's speech to the agent's first sound.
    caller_speech_ms: float | None = Field(None, ge=0)
    agent_speech_ms: float | None = Field(None, ge=0)
    # The voice program's own measure of the same gap, when it logs one with its line (our
    # worker's `reply_ms`, voice/livekit/turns.py), to calibrate the rig against it.
    platform_reply_ms: float | None = Field(None, ge=0)


class ScenarioResult(Strict):
    scenario_id: str = Field(pattern=SLUG)
    # Which sample of the scenario this is; the full suite runs each scenario three times.
    sample: int = Field(1, ge=1)
    persona: str = Field(min_length=1)
    flow: list[Flow] = Field(min_length=1)
    difficulty: Difficulty
    traits: list[Trait] = Field(default_factory=list)
    outcome: Outcome
    must_pass_failures: list[str] = Field(default_factory=list)
    # Every failed code check, must-pass or not, and why an error run produced no verdict.
    check_failures: list[str] = Field(default_factory=list)
    error: str = ""
    # The code checks that ran on this call, by name (`must:` in the scenario). A check failed when
    # a line in check_failures starts with its name and a colon; the rest passed. Empty on an error
    # run, which had no verdict, and on files written before this field existed.
    checks: list[str] = Field(default_factory=list)
    # Missing on an error run, which the grader never sees, and on a fail it couldn't score.
    grader_score: int | None = Field(None, ge=1, le=5)
    grader_notes: str = ""
    reply_delay_p50_ms: float = Field(ge=0)
    reply_delay_p90_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)
    # How long the scenario ran. Optional; without it LangSmith gets no end time rather than a
    # zero-length run.
    duration_ms: float | None = Field(default=None, ge=0)
    transcript: list[Turn] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    turn_timings: list[TurnTiming] = Field(default_factory=list)
    call_id: str = Field(min_length=1)
    # Audio rig only: agent turns with no sound before the rig's timeout (left out of the delays),
    # and the cost split by part (caller voice, agent ears, platform, models), in USD.
    silent_turns: int = Field(0, ge=0)
    cost_parts: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_consistency(self) -> "ScenarioResult":
        if self.must_pass_failures and self.outcome == "pass":
            raise ValueError(f"{self.scenario_id}: outcome is pass but must_pass_failures is not empty")
        if self.reply_delay_p90_ms < self.reply_delay_p50_ms:
            raise ValueError(f"{self.scenario_id}: reply_delay_p90_ms is below reply_delay_p50_ms")
        # Only a run with no verdict, or a fail the grader couldn't score, has no grade.
        ungraded_ok = self.outcome == "error" or (self.outcome == "fail" and self.check_failures)
        if self.grader_score is None and not ungraded_ok:
            raise ValueError(f"{self.scenario_id}: grader_score is missing on a {self.outcome}")
        return self

    @property
    def key(self) -> str:
        """The scenario id, with the sample number after the first sample."""
        return self.scenario_id if self.sample == 1 else f"{self.scenario_id}#{self.sample}"


class Run(Strict):
    run_id: str = Field(pattern=SLUG)
    created: AwareDatetime
    # A named suite from evals/suites.yaml, or "custom" when the run used a TAGS filter.
    suite: str = Field(pattern=SLUG)
    tags: list[str] = Field(default_factory=list)
    stack: str = Field(pattern=SLUG)
    profile: str = Field(pattern=SLUG)
    # Hash of the prompts, scenarios and model profile, as the ratchet checks it.
    hash: str = Field(min_length=1)
    # Hash of the agent's prompt files alone, so two runs can be told apart by prompt change.
    prompt_hash: str | None = None
    # The agent model id behind the profile, for reading a profile comparison.
    model: str | None = None
    # "text" for the text harness, "audio" for the simulated audio rig, where every delay is
    # measured on the audio itself.
    transport: Literal["text", "audio"] = "text"
    scenarios: list[ScenarioResult] = Field(min_length=1)

    @model_validator(mode="after")
    def check_unique_scenarios(self) -> "Run":
        ids = [s.key for s in self.scenarios]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate scenario ids: {', '.join(dupes)}")
        return self

    def pass_rate(self) -> float | None:
        """Passes over passes and fails; error runs have no verdict and are left out."""
        judged = [s for s in self.scenarios if s.outcome != "error"]
        return sum(s.outcome == "pass" for s in judged) / len(judged) if judged else None


def result_path(run_id: str, directory: Path = RESULTS_DIR) -> Path:
    return directory / f"{run_id}.json"


def save(run: Run, directory: Path = RESULTS_DIR) -> Path:
    path = result_path(run.run_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2) + "\n")
    return path


def load(path: str | Path) -> Run:
    path = Path(path)
    try:
        return Run.model_validate_json(path.read_text())
    except OSError as err:
        raise ResultsError(f"cannot read {path}: {err.strerror}") from err
    except ValidationError as err:
        raise ResultsError(f"{path} is not a valid results file: {_summary(err)}") from err


def _summary(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors())

