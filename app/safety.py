"""The safety check run on every caller turn (docs/call-journeys.md, spine step 4 and journey 3).

A fixed list of phrases, no model: a clear signal ("I smell gas", "the CO alarm is going off")
switches the call to the safety lines at once. "Gas" alone, a gas bill or a gas furnace triggers
nothing, because only phrases describing a smell, leak, alarm or symptom are listed. A vague
description gets one check question; a yes is a clear signal and a clear no clears the call.

States, kept on the call: none, checking, signal (lines not said yet), lines_said, cleared.
"""

from dataclasses import dataclass

from .text import has_phrase, words

NEGATORS = (
    "dont",
    "do not",
    "doesnt",
    "does not",
    "didnt",
    "did not",
    "not",
    "no",
    "never",
    "cant",
    "cannot",
    "isnt",
    "no longer",
)
YES = (
    "yes",
    "yeah",
    "yep",
    "yup",
    "i do",
    "it does",
    "i think so",
    "maybe",
    "kind of",
    "sort of",
    "a little",
    "a bit",
    "definitely",
)
UNSURE = ("dont know", "not sure", "no idea", "cant tell")
NO = ("no", "nope", "nah", "not really", "nothing", "i dont", "dont smell", "not smelling")


@dataclass
class SafetyAction:
    action: str  # none, ask_check, speak_lines, speak_variant, cleared
    kind: str | None = None
    text: str = ""
    variant: int = 0


def _negated(text_words: str, phrase: str) -> bool:
    """True only when every mention of the phrase comes straight after a negating word, as in
    "I don't smell gas" or "no gas smell". "I don't know, I smell gas" is not negated."""
    target = words(phrase)
    start = text_words.find(target)
    if start < 0:
        return False
    while start >= 0:
        before = text_words[: start + 1]
        if not any(before.endswith(words(n)) for n in NEGATORS):
            return False
        start = text_words.find(target, start + 1)
    return True


def clear_signal(text: str, cfg: dict) -> str | None:
    """The kind of the first clear, not negated signal in the text, or None."""
    text_words = words(text)
    for kind, spec in cfg["kinds"].items():
        for phrase in spec["clear"]:
            if has_phrase(text_words, phrase) and not _negated(text_words, phrase):
                return kind
    return None


def vague_signal(text: str, cfg: dict) -> str | None:
    text_words = words(text)
    for kind, spec in cfg["vague"].items():
        if any(has_phrase(text_words, p) and not _negated(text_words, p) for p in spec["phrases"]):
            return kind
    return None


def _negated_clear(text: str, cfg: dict) -> bool:
    text_words = words(text)
    return any(
        has_phrase(text_words, p) and _negated(text_words, p)
        for spec in cfg["kinds"].values()
        for p in spec["clear"]
    )


def answer_to_check(text: str, cfg: dict) -> str:
    """yes, no or unclear. Anything that sounds like a yes, an unsure answer, or a mention of the
    smell again counts as a yes: a false alarm costs a few seconds, a missed leak costs much more."""
    text_words = words(text)
    if clear_signal(text, cfg) or vague_signal(text, cfg):
        return "yes"
    if _negated_clear(text, cfg):
        return "no"
    if any(has_phrase(text_words, w) for w in UNSURE + YES):
        return "yes"
    if any(has_phrase(text_words, w) for w in NO):
        return "no"
    return "unclear"


def lines_for(cfg: dict, kind: str) -> str:
    return " ".join(cfg["kinds"][kind]["lines"].split())


def check_caller_turn(
    text: str, state: str, kind: str | None, variant: int, cfg: dict
) -> SafetyAction:
    """What the agent must do after this caller line, given the call's safety state."""
    signal = clear_signal(text, cfg)
    if signal:
        if state == "lines_said" and signal == kind:
            variants = cfg["kinds"][kind]["variants"]
            return SafetyAction(
                "speak_variant", kind, variants[variant % len(variants)], variant + 1
            )
        return SafetyAction("speak_lines", signal, lines_for(cfg, signal))
    if state == "signal":
        # The lines haven't been said yet, so they still come before anything else.
        return SafetyAction("speak_lines", kind, lines_for(cfg, kind))
    if state == "checking":
        answer = answer_to_check(text, cfg)
        if answer == "yes":
            return SafetyAction("speak_lines", kind, lines_for(cfg, kind))
        if answer == "no":
            return SafetyAction("cleared", kind)
        return SafetyAction("none", kind)
    vague = vague_signal(text, cfg)
    if vague and state in ("none", "cleared"):
        return SafetyAction("ask_check", vague, cfg["vague"][vague]["question"])
    return SafetyAction("none", kind)


def agent_said_lines(text: str, kind: str | None, cfg: dict) -> bool:
    """The agent line contains the full safety lines for the call's kind, word for word."""
    return bool(kind) and words(lines_for(cfg, kind)) in words(text)
