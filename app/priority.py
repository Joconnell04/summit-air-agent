"""The priority engine: a starting level from the job type and the temperature brackets, raised by
rules from tenant config (docs/explorations/007-priority-brackets.md).

The brackets set where a no-heat or no-cooling call starts, from the inside temperature the caller
gives and the outside temperature now and tonight. Rules and the side model then can only raise
it (spec S-21). Rules fire only on the caller's own lines and the server's readings (the weather,
the safety state), never on words the model wrote: an inside temperature counts only when a caller
line carries it (`heard_inside_temp`), and the model's notes on who is home go to the job, not here
(DECISIONS.md 072). A priority the agent asks for is logged but never used.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from .text import has_phrase, numbers_said, spoken_numbers, words

LEVELS = ["Low", "Normal", "High", "Urgent"]
# How callers say someone's age: "84-year-old", "84 years old", "I'm 84", "she's 90", "my dad is
# 78", "age 84" (as the agent passes it), "in her 80s". Only ages over the rule's floor count, so
# "it's 2 years old" (usually the system) never fires.
AGE = re.compile(
    r"\b(\d{2,3})(?:-|\s)(?:year|yr)s?(?:-|\s)old\b"
    # Each pronoun with its own verb, so "it is 48" or "the house is 48" is never an age (PR #126
    # re-check). The apostrophe is optional: speech-to-text writes "im 84" as often as "I'm 84".
    r"|\b(?:i['’]?m|i am|they['’]?re|they are"
    r"|(?:she|he|who|mom|dad|mother|father|grandma|grandpa)(?:['’]?s| is))\s+(\d{2,3})\b"
    r"|\bage[d]?\s+(\d{2,3})\b"
    r"|\bin (?:his|her|their|my|our) (\d)0s\b"
)
DECADES = {"seventies": 70, "eighties": 80, "nineties": 90}
AMOUNT = re.compile(
    r"\b\d+\s*(?:%|percent\b|per cent\b|minutes?\b|mins?\b|hours?\b|hrs?\b|blocks?\b"
    r"|dollars?\b|bucks\b|miles?\b|seconds?\b|degrees?\b)"
)


class SideModel(Protocol):
    """A model that answers one question about the caller's lines with the probability of yes
    (app/side_model.py, DECISIONS.md 006). None means no answer in time: the rules decide alone."""

    def score(self, question: str, text: str) -> float | None: ...


@dataclass
class Facts:
    job_type: str
    text: str = ""  # the caller's lines on this call and any linked to it, one per line
    inside_temp_f: float | None = None  # only a temperature the caller said (heard_inside_temp)
    safety_signal: bool = False
    outside_now_f: float | None = None
    outside_coldest_f: float | None = None  # the lower of now and the next 12 hours
    heating_indoors: bool = False  # the safety check heard fuel-burning heat indoors
    caller_text: str = ""  # what the side model reads; the same lines as `text`
    inside_temp_line: str = ""  # the caller's line the inside temperature came from
    electrical_line: str = ""  # the caller's line the electrical route heard (app/safety.py)


@dataclass
class Priority:
    level: str
    reasons: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    # The level from the job type and the temperatures alone, before anything the caller said
    # about the people or the home raised it.
    start: str = ""

    @property
    def raised_by_caller(self) -> bool:
        """The caller's own facts (someone at risk, pipes, water) moved it above where it started."""
        return bool(self.start) and LEVELS.index(self.level) > LEVELS.index(self.start)

    def raise_to(self, level: str, rule: str, reason: str) -> None:
        if LEVELS.index(level) > LEVELS.index(self.level):
            self.level = level
        self.rules.append(rule)
        self.reasons.append(reason)


def _ages(text: str) -> list[int]:
    # "eighty-four" is 84: a line roughened by speech-to-text, or a caller who spells it out. A
    # number with a unit is never an age: "I'm ninety percent sure", "I'm sixty five minutes away"
    # (PR #126 review).
    text = AMOUNT.sub(" ", spoken_numbers(text))
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


# A phrase right after one of these, in the same part of the sentence, was said not to apply: "no
# babies or older people", "nobody on any medical equipment" (eval round 2, seed 9). The look back is
# short and stops at a word that starts a new thought, so "no heat and grandma is here" still
# raises: a missed raise is worse than an extra one, and rules can only raise.
# "nobodys" and "nothings" are "nobody's" and "nothing's" once the apostrophe is dropped.
NEGATIONS = {"no", "not", "nobody", "nobodys", "noone", "nothing", "nothings", "none", "without", "never",
             "neither", "nor", "dont", "doesnt", "isnt", "arent", "havent", "hasnt", "aint",
             # "I didn't mention a baby" (eval run 20261104-171428, asks-agent-to-say-baby).
             "didnt", "wasnt", "werent", "hadnt"}
CLAUSE_BREAKS = {"and", "but", "so", "because", "though", "although", "except", "plus", "also",
                 "just", "only", "well"}
NEGATION_REACH = 4
POSSESSIVES = {"my", "our", "his", "her", "their"}
PEOPLE_STARTS = {"she", "shes", "he", "hes", "they", "theyre", "theres", "there", "i", "im", "we"}
# The people and things a priority rule looks for, as a caller lists them after "no".
RISK_NOUNS = {"baby", "babies", "infant", "infants", "newborn", "newborns", "kids", "children",
              "toddler", "toddlers", "elderly", "seniors", "older", "people", "medical",
              "equipment", "oxygen", "one", "anyone", "nobody"}
# Words that say something about a person rather than list one: "elderly mother lives with me".
SAYS_SOMETHING = {"lives", "live", "living", "is", "are", "has", "have", "stays", "staying",
                  "here", "home", "house", "upstairs", "downstairs", "with", "running", "on"}
# Small words that don't use up the reach: "nobody who's a baby or elderly" negates "elderly"
# (eval run 20261104-052332, mild-after-hours-no-heat: it sent a mild night to the on-call path).
REACH_FILLER = {"a", "an", "or", "who", "whos", "is", "any", "on"}


def _negated_at(tokens: list[str], i: int) -> bool:
    """A negation just before the word at i, in the same part of the sentence."""
    reached = 0
    for j in range(i - 1, -1, -1):
        if tokens[j] in CLAUSE_BREAKS:
            return False
        # "no my grandmother lives with us" is an answer, then a person: speech-to-text drops
        # the comma, and nobody says "no my" to mean there is none.
        if tokens[j] in NEGATIONS and tokens[j + 1] not in POSSESSIVES:
            return True
        if tokens[j] not in REACH_FILLER:
            reached += 1
            if reached >= NEGATION_REACH:
                return False
    return False


def _said_not_negated(text_words: str, phrase: str) -> bool:
    """The phrase occurs at least once without a negation just before it."""
    tokens, target = text_words.split(), words(phrase).split()
    return any(
        tokens[i : i + len(target)] == target and not _negated_at(tokens, i)
        for i in range(len(tokens) - len(target) + 1)
    )


def _positions(tokens: list[str], phrases: list[str]) -> list[int]:
    found = []
    for phrase in phrases:
        target = words(phrase).split()
        found += [
            i for i in range(len(tokens) - len(target) + 1) if tokens[i : i + len(target)] == target
        ]
    return found


def _together(text_words: str, spec: dict) -> bool:
    """A word from each list within `within` words of each other, the first of the two not
    negated, and no `unless_near` word close to the first: "water coming through my living room
    ceiling", "the AC is dripping water" (a rule's `together`, DECISIONS.md 082)."""
    tokens = text_words.split()
    firsts, seconds = _positions(tokens, spec["first"]), _positions(tokens, spec["second"])
    # A word in `unless_near` close to the first word says it is something else ("the kitchen
    # faucet is dripping").
    others = _positions(tokens, spec.get("unless_near", []))
    return any(
        abs(a - b) <= spec["within"]
        and not _negated_at(tokens, min(a, b))
        and not any(abs(a - o) <= spec["within"] for o in others)
        for a in firsts
        for b in seconds
    )


def _parts(text: str) -> list[str]:
    """The text split at punctuation. A negated list of the people or things a rule looks for goes
    on past a comma: in "no babies, older people or medical equipment" the "no" covers the whole
    list (eval run 20261104-154616, mild-after-hours-no-heat). It carries only from a part that
    negates a risk noun ("no babies"), into a part that is still a list ("... or ...") with nothing
    said about someone, so "No heat, baby in the house" and "no kids, no pets, elderly mother lives
    here" still raise. A missed raise is worse than an extra one (PR #102 review)."""
    parts = [words(part) for part in re.split(r"[.,;:!?\n]", text)]
    carried = []
    for i, part in enumerate(parts):
        before = parts[i - 1].split() if i else []
        tokens = part.split()
        listed = (
            len(before) >= 2
            and before[0] in NEGATIONS
            and any(t in RISK_NOUNS for t in before[1:])
            and tokens
            and tokens[0] not in POSSESSIVES | CLAUSE_BREAKS | PEOPLE_STARTS
            and ("or" in tokens or "nor" in tokens)
            and not any(t in SAYS_SOMETHING for t in tokens)
        )
        carried.append(f" no {part.strip()} " if listed else part)
    return carried


def _always_followed(text: str, phrase: str, followers: list[str]) -> bool:
    """Every mention of the phrase runs straight on into one of these words, across a comma too:
    "it's just me and my partner" names someone else, so it isn't "just me" (a rule's
    `unless_followed_by`, which names the phrases it covers)."""
    tokens, target = words(text).split(), words(phrase).split()
    found = [
        i for i in range(len(tokens) - len(target) + 1) if tokens[i : i + len(target)] == target
    ]
    after = [tokens[i + len(target)] if i + len(target) < len(tokens) else "" for i in found]
    return bool(found) and all(a in followers for a in after)


def _said(rule: dict, text: str) -> bool:
    """The rule's phrases or ages turn up in these lines, not negated. A fact the caller states
    always counts, even one they ask the agent to pass on ("tell them there's a baby in the house"):
    a false raise is the safe direction, and raised_after_wait tells the dispatcher when it came."""
    unless = rule.get("unless_followed_by") or {}
    phrases = [
        p
        for p in rule.get("phrases", [])
        if p not in unless.get("phrases", []) or not _always_followed(text, p, unless["words"])
    ]
    # Split at punctuation first, so "No, my mother lives with us" keeps its raise.
    parts = _parts(text)
    # "A stroke of luck" isn't a stroke: a rule's not_phrases are blanked out before matching.
    for unrelated in rule.get("not_phrases", []):
        parts = [part.replace(words(unrelated), " , ") for part in parts]
    if any(_said_not_negated(part, p) for part in parts for p in phrases):
        return True
    # Two words near each other, read across each sentence: callers put commas between them.
    if "together" in rule:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
            said_words = words(sentence)
            for unrelated in rule.get("not_phrases", []):
                said_words = said_words.replace(words(unrelated), " , ")
            if _together(said_words, rule["together"]):
                return True
    return "age_at_least" in rule and any(a >= rule["age_at_least"] for a in _ages(text))


def said(rule: dict, text: str) -> bool:
    """Whether a rule's phrases or ages turn up in this text, as the server reads a caller's lines;
    the eval checks use it to test the model's notes against what the caller said."""
    return _said(rule, text)


def _rule_fires(rule: dict, facts: Facts) -> bool:
    if "job_types" in rule and facts.job_type not in rule["job_types"]:
        return False
    if rule.get("safety_signal"):
        return facts.safety_signal
    if rule.get("electrical_signal"):
        return bool(facts.electrical_line)
    if rule.get("heating_indoors") and facts.heating_indoors:
        return True
    if _said(rule, facts.text):
        return True
    temp = facts.inside_temp_f
    if temp is not None:
        if "inside_temp_below_f" in rule and temp < rule["inside_temp_below_f"]:
            return True
        if "inside_temp_above_f" in rule and temp > rule["inside_temp_above_f"]:
            return True
    return False


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
QUOTE_CHARS = 80


def quoted(line: str) -> str:
    """A caller's line as the priority reason quotes it, cut at a word near QUOTE_CHARS."""
    # Without its closing punctuation, so the reason reads as one sentence wherever it's shown.
    line = " ".join(line.split()).strip().rstrip(" .!?,;")
    if len(line) > QUOTE_CHARS:
        line = line[: QUOTE_CHARS - 3].rsplit(" ", 1)[0].rstrip(" ,;.") + "..."
    return f'caller said "{line}"'


def _caller_line(rule: dict, facts: Facts) -> str:
    """The first of the caller's sentences that fires the rule on its own, so the reason shows the
    dispatcher what the caller said rather than only the rule's name."""
    if rule.get("safety_signal"):
        return ""
    if rule.get("electrical_signal"):
        return facts.electrical_line
    by_temp = "inside_temp_below_f" in rule or "inside_temp_above_f" in rule
    if by_temp and facts.inside_temp_line and not _said(rule, facts.text):
        return facts.inside_temp_line
    for line in facts.text.splitlines():
        for sentence in SENTENCE_END.split(line):
            if sentence.strip() and _said(rule, sentence):
                return sentence
    return ""


def _reason(reason: str, line: str) -> str:
    return f"{reason}, {quoted(line)}" if line else reason


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
        line = facts.inside_temp_line if any(k.startswith("inside_") for k in bracket) else ""
        result = Priority(
            bracket["level"], [_reason(bracket["reason"], line)], [f"bracket:{bracket['id']}"]
        )
    else:
        result = Priority(cfg["base"].get(facts.job_type, "Normal"))
    result.start = result.level
    mild = result.level == "Normal"
    groups = 0
    for rule in cfg["rules"]:
        if _rule_fires(rule, facts):
            # On a mild day a household at risk is High, not Urgent: the house isn't getting
            # dangerous, but they still go ahead of routine work.
            level = rule.get("raise_to_when_mild", rule["raise_to"]) if mild else rule["raise_to"]
            line = _caller_line(rule, facts)
            # A line two rules fire on is quoted once, with the first.
            quote = line if line and not any(quoted(line) in r for r in result.reasons) else ""
            result.raise_to(level, rule["id"], _reason(rule["reason"], quote))
            groups += rule.get("group", False)
    combine = cfg.get("combine")
    if combine and not mild and groups >= combine["groups"]:
        result.raise_to(combine["raise_to"], "combined", combine["reason"])
    slot = cfg.get("side_model")
    applies = slot and facts.job_type in slot.get("job_types", [facts.job_type])
    if side_model is not None and applies:
        score = side_model.score(slot["question"], facts.caller_text or facts.text)
        if score is not None and score >= slot["threshold"]:
            level = slot.get("raise_to_when_mild", slot["raise_to"]) if mild else slot["raise_to"]
            # The side model reads every caller line, so there is no one line to quote.
            result.raise_to(level, "side_model", f"{slot['reason']}, from the side model")
    return result


def heard_inside_temp(
    turns: list[tuple[str, str]], value: float | None, cfg: dict
) -> str | None:
    """The caller's line that gives this inside temperature, or None, on the pattern of the ZIP
    rule (DECISIONS.md 058): the model's number counts only if the caller said it. `turns` are
    (speaker, text) in order. The number has to sit within a few words of a temperature word in the
    same sentence ("it's 52 in here", "fifty two degrees", "the thermostat says 58"), or be the
    caller's answer after the agent asked about the temperature ("about 58"). The question stays
    open across an agent's "Sure" while the caller goes to look, until a caller line gives a number
    or `asked_lines` caller lines go by. A number the line gives as someone's age never counts ("my
    mother is 84"). "The fifties" counts for any value from 50 to 59. `cfg` is the trade's
    priority.inside_temp block."""
    low_f, high_f = cfg.get("plausible_f", [20, 110])
    if value is None or not low_f <= value <= high_f:
        return None
    reach = cfg.get("reach_words", 4)
    open_for = 0
    for speaker, text in turns:
        if speaker == "agent":
            if any(has_phrase(words(text), a) for a in cfg.get("asked", [])):
                open_for = cfg.get("asked_lines", 3)
            continue
        asked = open_for > 0
        open_for = 0 if numbers_said(text) else max(0, open_for - 1)
        ages = set(_ages(text))
        for sentence in SENTENCE_END.split(text):
            tokens = words(spoken_numbers(sentence)).split()
            cues = [
                i
                for cue in cfg.get("cues", [])
                for target in [words(cue).split()]
                for i in range(len(tokens) - len(target) + 1)
                if tokens[i : i + len(target)] == target
            ]
            for at, low, high in numbers_said(sentence):
                if not low <= value <= high or (low == high and low in ages):
                    continue
                if asked or any(abs(at - i) <= reach for i in cues):
                    return text
    return None


def raising_push(text: str, cfg: dict) -> bool:
    """The caller asks for a higher priority with no new fact in the line: "it's an emergency",
    "put me first", "I'll pay extra". A line that also gives a fact a rule raises on ("it's an
    emergency, my mother is 84") is left to the rules. A negated ask ("it's not an emergency") and
    talk about the equipment ("the furnace is urgent business") don't match. The phrases are the
    trade's priority.pushes."""
    parts = [words(part) for part in re.split(r"[.,;:!?\n]", text)]
    if not any(_said_not_negated(part, p) for part in parts for p in cfg.get("pushes", [])):
        return False
    return not any(_said(rule, text) for rule in cfg["rules"])


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
