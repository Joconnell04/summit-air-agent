"""The priority engine: a job type's base priority, raised by rules from tenant config.

Rules and the side model can only raise priority (spec S-21). The agent passes facts (the inside
temperature, who is home) and the server also reads every caller line on the call, so a priority
the agent asks for is logged but never used on its own: the model asks, the server decides.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from .text import has_phrase, words

LEVELS = ["Low", "Normal", "High", "Urgent"]
# "84-year-old", "84 years old". Only older ages count: "it's 2 years old" is usually the system.
AGE = re.compile(r"\b(\d{1,3})(?:-|\s)(?:year|yr)s?(?:-|\s)old\b")


class SideModel(Protocol):
    """A model that scores a call's caller lines for urgency (DECISIONS.md 006). Off at launch."""

    def score(self, text: str) -> float | None: ...


@dataclass
class Facts:
    job_type: str
    text: str = ""  # every caller line on the call, plus what the agent passed
    inside_temp_f: float | None = None
    safety_signal: bool = False


@dataclass
class Priority:
    level: str
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def raise_to(self, level: str, rule: str, reason: str) -> None:
        if LEVELS.index(level) > LEVELS.index(self.level):
            self.level = level
        self.rules.append(rule)
        self.reasons.append(reason)


def _ages(text: str) -> list[int]:
    return [int(m[1]) for m in AGE.finditer(text.lower())]


def _rule_fires(rule: dict, facts: Facts) -> bool:
    if "job_types" in rule and facts.job_type not in rule["job_types"]:
        return False
    if rule.get("safety_signal"):
        return facts.safety_signal
    text_words = words(facts.text)
    if any(has_phrase(text_words, p) for p in rule.get("phrases", [])):
        return True
    if "age_at_least" in rule and any(a >= rule["age_at_least"] for a in _ages(facts.text)):
        return True
    temp = facts.inside_temp_f
    if temp is not None:
        if "inside_temp_below_f" in rule and temp < rule["inside_temp_below_f"]:
            return True
        if "inside_temp_above_f" in rule and temp > rule["inside_temp_above_f"]:
            return True
    return False


def decide(facts: Facts, cfg: dict, side_model: SideModel | None = None) -> Priority:
    result = Priority(cfg["base"].get(facts.job_type, "Normal"))
    for rule in cfg["rules"]:
        if _rule_fires(rule, facts):
            result.raise_to(rule["raise_to"], rule["id"], rule["reason"])
    slot = cfg.get("side_model")
    if side_model is not None and slot:
        score = side_model.score(facts.text)
        if score is not None and score >= slot["threshold"]:
            result.raise_to(slot["raise_to"], "side_model", slot["reason"])
    return result


def after_hours_outcome(facts: Facts, priority: Priority, cfg: dict, falling_fast: bool) -> str:
    """Journey 11: how a no-heat or no-cooling call ends after hours, by tenant cutoffs. The job
    types and cutoffs are in the tenant's after_hours block.

    tonight_if_on_call_agrees: the on-call technician is asked and calls back to confirm.
    first_morning_slot: booked now.
    callback: someone calls back at a named time.
    """
    at_risk = any(r in priority.rules for r in cfg["at_risk_rules"])
    temp = facts.inside_temp_f
    if facts.job_type in cfg["cooling_job_types"]:
        if at_risk or (temp is not None and temp >= cfg["cooling_tonight_above_f"]):
            return "tonight_if_on_call_agrees"
        if temp is not None and temp < cfg["cooling_morning_below_f"]:
            return "first_morning_slot"
        return "callback"
    if at_risk or falling_fast or (temp is not None and temp < cfg["tonight_below_f"]):
        return "tonight_if_on_call_agrees"
    if temp is not None and temp > cfg["morning_above_f"]:
        return "first_morning_slot"
    return "callback"
