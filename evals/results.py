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


class ToolCall(Strict):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ScenarioResult(Strict):
    scenario_id: str = Field(pattern=SLUG)
    persona: str = Field(min_length=1)
    flow: list[Flow] = Field(min_length=1)
    difficulty: Difficulty
    traits: list[Trait] = Field(default_factory=list)
    outcome: Outcome
    must_pass_failures: list[str] = Field(default_factory=list)
    grader_score: int = Field(ge=1, le=5)
    reply_delay_p50_ms: float = Field(ge=0)
    reply_delay_p90_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)
    transcript: list[Turn] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    call_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_consistency(self) -> "ScenarioResult":
        if self.must_pass_failures and self.outcome == "pass":
            raise ValueError(f"{self.scenario_id}: outcome is pass but must_pass_failures is not empty")
        if self.reply_delay_p90_ms < self.reply_delay_p50_ms:
            raise ValueError(f"{self.scenario_id}: reply_delay_p90_ms is below reply_delay_p50_ms")
        return self


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
    scenarios: list[ScenarioResult] = Field(min_length=1)

    @model_validator(mode="after")
    def check_unique_scenarios(self) -> "Run":
        ids = [s.scenario_id for s in self.scenarios]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate scenario ids: {', '.join(dupes)}")
        return self


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

