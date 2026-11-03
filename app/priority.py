"""The priority engine: a starting level from the job type and the temperature brackets, raised by
rules from tenant config (docs/explorations/007-priority-brackets.md).

The brackets set where a no-heat or no-cooling call starts, from the inside temperature the caller
gives and the outside temperature now and tonight. Rules and the side model then can only raise
it (spec S-21). The agent passes facts (the inside
temperature, who is home) and the server also reads every caller line on the call, so a priority
the agent asks for is logged but never used on its own: the model asks, the server decides.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from .text import has_phrase, words

LEVELS = ["Low", "Normal", "High", "Urgent"]
# How callers say someone's age: "84-year-old", "84 years old", "I'm 84", "she's 90", "my dad is
# 78", "age 84" (as the agent passes it), "in her 80s". Only ages over the rule's floor count, so
# "it's 2 years old" (usually the system) never fires.
AGE = re.compile(
    r"\b(\d{2,3})(?:-|\s)(?:year|yr)s?(?:-|\s)old\b"
    r"|\b(?:i|she|he|they|who|mom|dad|mother|father|grandma|grandpa)(?:['’]s|['’]m| is| am| are|['’]re)\s+(\d{2,3})\b"
    r"|\bage[d]?\s+(\d{2,3})\b"
    r"|\bin (?:his|her|their|my|our) (\d)0s\b"
)
DECADES = {"seventies": 70, "eighties": 80, "nineties": 90}


class SideModel(Protocol):
    """A model that scores a call's caller lines for urgency (DECISIONS.md 006). Off at launch."""

    def score(self, text: str) -> float | None: ...


@dataclass
class Facts:
    job_type: str
    text: str = ""  # every caller line on the call, plus what the agent passed
    inside_temp_f: float | None = None
    safety_signal: bool = False
    outside_now_f: float | None = None
    outside_coldest_f: float | None = None  # the lower of now and the next 12 hours
    heating_indoors: bool = False  # the safety check heard fuel-burning heat indoors


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
    text = text.lower()
    ages = []
    for m in AGE.finditer(text):
        if m[4]:
            ages.append(int(m[4]) * 10)
        else:
            ages.append(int(next(g for g in m.groups()[:3] if g)))
    ages += [
        age
        for word, age in DECADES.items()
        if re.search(rf"\bin (?:his|her|their|my|our) {word}\b", text)
    ]
    return ages


def _rule_fires(rule: dict, facts: Facts) -> bool:
    if "job_types" in rule and facts.job_type not in rule["job_types"]:
        return False
    if rule.get("safety_signal"):
        return facts.safety_signal
    if rule.get("heating_indoors") and facts.heating_indoors:
        return True
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


def _bracket(facts: Facts, brackets: list[dict]) -> dict:
    """The first bracket whose conditions all hold. Each condition is skipped when its temperature
    is unknown only if the bracket says so (`if_unknown: match`); the last bracket has none."""
    temps = {
        "inside_below_f": facts.inside_temp_f,
        "inside_at_least_f": facts.inside_temp_f,
        "inside_above_f": facts.inside_temp_f,
        "outside_coldest_below_f": facts.outside_coldest_f,
        "outside_now_at_least_f": facts.outside_now_f,
    }
    for bracket in brackets:
        conditions = {k: v for k, v in bracket.items() if k in temps}
        if all(_holds(k, v, temps[k], bracket.get("if_unknown")) for k, v in conditions.items()):
            return bracket
    raise ValueError("the last bracket must have no conditions")


def _holds(key: str, limit: float, value: float | None, if_unknown: str | None) -> bool:
    if value is None:
        return if_unknown == "match"
    if key.endswith("_below_f"):
        return value < limit
    if key.endswith("_above_f"):
        return value > limit
    return value >= limit


def decide(facts: Facts, cfg: dict, side_model: SideModel | None = None) -> Priority:
    brackets = cfg.get("brackets", {}).get(facts.job_type)
    if brackets:
        bracket = _bracket(facts, brackets)
        result = Priority(bracket["level"], [bracket["reason"]], [f"bracket:{bracket['id']}"])
    else:
        result = Priority(cfg["base"].get(facts.job_type, "Normal"))
    mild = result.level == "Normal"
    groups = 0
    for rule in cfg["rules"]:
        if _rule_fires(rule, facts):
            # On a mild day a household at risk is High, not Urgent: the house isn't getting
            # dangerous, but they still go ahead of routine work.
            level = rule.get("raise_to_when_mild", rule["raise_to"]) if mild else rule["raise_to"]
            result.raise_to(level, rule["id"], rule["reason"])
            groups += rule.get("group", False)
    combine = cfg.get("combine")
    if combine and not mild and groups >= combine["groups"]:
        result.raise_to(combine["raise_to"], "combined", combine["reason"])
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
