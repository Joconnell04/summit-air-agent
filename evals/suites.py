"""Pick scenarios by suite name or by a TAGS filter such as flow:safety,difficulty:rigorous."""

from pathlib import Path

import yaml

from evals.scenario import Scenario

SUITES = Path(__file__).resolve().parent / "suites.yaml"


class SuiteError(ValueError):
    pass


def load_suites(path: Path = SUITES) -> dict:
    return yaml.safe_load(path.read_text())


def parse_tags(text: str | None) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def matches(scenario_tags: set[str], wanted: list[str]) -> bool:
    """Tags sharing a prefix are alternatives; each prefix present must match at least once."""
    groups: dict[str, set[str]] = {}
    for tag in wanted:
        prefix = tag.split(":", 1)[0] if ":" in tag else tag
        groups.setdefault(prefix, set()).add(tag)
    return all(scenario_tags & options for options in groups.values())


def select(
    scenarios: list[Scenario],
    suite: str | None = None,
    tags: list[str] | None = None,
    suites: dict | None = None,
) -> tuple[list[Scenario], int]:
    """The scenarios a run covers and how many samples of each."""
    wanted, samples = [], 1
    if suite:
        suites = suites if suites is not None else load_suites()
        if suite not in suites:
            raise SuiteError(f"no suite {suite!r}; known: {', '.join(suites)}")
        wanted, samples = list(suites[suite]["tags"]), suites[suite].get("samples", 1)
    wanted += tags or []
    chosen = [s for s in scenarios if matches(s.all_tags(), wanted)]
    if not chosen:
        raise SuiteError(f"no scenario matches {', '.join(wanted) or 'the filter'}")
    return chosen, samples
