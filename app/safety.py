"""The safety check run on every caller turn (docs/call-journeys.md, spine step 4 and journey 3).

A fixed list of phrases, no model: a clear signal ("I smell gas", "the CO alarm is going off")
switches the call to the safety lines at once. "Gas" alone, a gas bill or a gas furnace triggers
nothing, because only phrases describing a smell, leak, alarm or symptom are listed. A vague
description gets one check question; a yes is a clear signal and a clear no clears the call.

Heating the home with an oven, stove, generator or grill indoors gets the carbon monoxide check
question once per call. Only wording that says it is used for heat counts ("the oven on to keep
warm"), never a plain mention ("my oven works", "the generator is outside").

States, kept on the call: none, checking, signal (lines not said yet), lines_said, cleared.
"""

import re
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
    # The carbon monoxide question asks whether anyone feels sick or dizzy: "nobody's dizzy".
    "nobody",
    "nobodys",
    "nobody is",
    "nobodys feeling",
    "nobody is feeling",
    "no one",
    "no ones",
    "no one is",
    "no ones feeling",
    "no one is feeling",
    "not feeling",
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
# Words that can sit between a negator and a symptom in a list: "not feeling sick or dizzy".
LIST_WORDS = {"sick", "or", "and", "feeling", "feel", "feels"}
UNSURE = ("dont know", "not sure", "no idea", "cant tell")
# A clear yes, even after an opening "no" ("no, wait, yes").
STRONG_YES = ("yes", "yeah", "yep", "yup", "maybe", "i think so", "actually")
FILLERS = {"um", "uh", "er", "well", "oh", "so"}
NO = ("no", "nope", "nah", "not really", "nothing", "i dont", "dont smell", "not smelling")
# Words after a mention that say it is not happening: "the CO alarm is quiet", "the carbon monoxide
# detector in the hallway has been quiet". Counted only in the answer to the check question, so the
# same words outside that answer still fire, and never after words saying the alarm is going off.
QUIET_AFTER = (
    "is quiet",
    "is silent",
    "its quiet",
    "its silent",
    "been quiet",
    "stayed quiet",
    "isnt going off",
    "is not going off",
    "not going off",
    "hasnt gone off",
    "has not gone off",
    "didnt go off",
    "never went off",
)
ACTIVE = ("going off", "went off", "beeping", "sounding", "ringing", "chirping", "keeps", "started")
QUIET_WINDOW = 8  # words after the mention


@dataclass
class SafetyAction:
    action: str  # none, ask_check, speak_lines, speak_variant, cleared
    kind: str | None = None
    text: str = ""
    variant: int = 0


def _quiet_after(rest: str) -> bool:
    """The words just after a mention say it is quiet, with nothing before that saying it is going
    off: "alarm is quiet" is, "alarm is going off, it was quiet before" is not."""
    tail = " " + " ".join(rest.split()[:QUIET_WINDOW]) + " "
    for quiet in QUIET_AFTER:
        at = tail.find(words(quiet))
        if at >= 0 and not any(words(a) in tail[: at + 1] for a in ACTIVE):
            return True
    return False


def _drop_list_words(before: str) -> str:
    """The text before a mention without the list words that sit between a negator and it, so
    "nobody's feeling sick or dizzy" negates "dizzy" as "nobody's dizzy" does."""
    kept = before.split()
    while kept and kept[-1] in LIST_WORDS:
        kept.pop()
    return " " + " ".join(kept) + " "


def _negated(text_words: str, phrase: str, quiet: bool = False) -> bool:
    """True only when every mention of the phrase comes straight after a negating word, as in
    "I don't smell gas" or "no gas smell". "I don't know, I smell gas" is not negated. With quiet,
    a mention followed by words like "is quiet" also counts as negated."""
    target = words(phrase)
    start = text_words.find(target)
    if start < 0:
        return False
    while start >= 0:
        before = _drop_list_words(text_words[: start + 1])
        after = text_words[start + len(target) - 1 :]
        negated = any(before.endswith(words(n)) for n in NEGATORS)
        if not negated and not (quiet and _quiet_after(after)):
            return False
        start = text_words.find(target, start + 1)
    return True


def clear_signal(text: str, cfg: dict, answering_check: bool = False) -> str | None:
    """The kind of the first clear, not negated signal in the text, or None. answering_check is
    True for the caller's answer to the check question, where "the CO alarm is quiet" is a no."""
    text_words = words(text)
    for kind, spec in cfg["kinds"].items():
        for phrase in spec["clear"]:
            if has_phrase(text_words, phrase) and not _negated(
                text_words, phrase, answering_check
            ):
                return kind
    return None


def vague_signal(text: str, cfg: dict) -> str | None:
    text_words = words(text)
    for kind, spec in cfg["vague"].items():
        if any(has_phrase(text_words, p) and not _negated(text_words, p) for p in spec["phrases"]):
            return kind
    return None


def _near_negator(text_words: str, start: int) -> bool:
    """A negating word in the three words before a match: "I'd never use the oven for heat"."""
    before = text_words[:start].split()[-3:]
    window = " " + " ".join(before) + " "
    return any(words(n) in window for n in NEGATORS)


def heating_signal(text: str, cfg: dict) -> bool:
    """The caller says something that burns fuel is heating the home indoors."""
    spec = cfg.get("heating_check")
    if not spec:
        return False
    text_words = words(text)
    sources = "|".join(re.escape(words(s).strip()) for s in spec["sources"])
    used = "|".join(re.escape(words(p).strip()) for p in spec["used_for_heat"])
    heat_with = r"(?:heat|heating|warm|warming) (?:\w+ ){0,2}with (?:the |a |an |my |our )?"
    patterns = [
        rf" (?:{sources}) (?:\w+ ){{0,3}}(?:{used}) ",
        rf" {heat_with}(?:{sources}) ",
        *(re.escape(words(p)) for p in spec["phrases"]),
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text_words):
            if not _near_negator(text_words, match.start() + 1):
                return True
    return False


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
    if clear_signal(text, cfg, answering_check=True) or vague_signal(text, cfg):
        return "yes"
    if _negated_clear(text, cfg):
        return "no"
    if any(has_phrase(text_words, w) for w in UNSURE):
        return "yes"
    # A plain denial from the tenant's list ("definitely not gas") is a no, though "definitely"
    # alone sounds like a yes.
    if any(has_phrase(text_words, w) for w in cfg.get("denials", [])):
        return "no"
    # An answer that opens with "no" is a no, so "no, I just feel a little chilled" isn't heard as
    # "a little". A clear yes later in the answer still counts.
    spoken = [w for w in text_words.split() if w not in FILLERS]
    if (
        spoken
        and spoken[0] in ("no", "nope", "nah")
        and not any(has_phrase(text_words, w) for w in STRONG_YES)
    ):
        return "no"
    if any(has_phrase(text_words, w) for w in YES):
        return "yes"
    if any(has_phrase(text_words, w) for w in NO):
        return "no"
    return "unclear"


def lines_for(cfg: dict, kind: str) -> str:
    return " ".join(cfg["kinds"][kind]["lines"].split())


def check_caller_turn(
    text: str,
    state: str,
    kind: str | None,
    variant: int,
    cfg: dict,
    heating_asked: bool = False,
) -> SafetyAction:
    """What the agent must do after this caller line, given the call's safety state. heating_asked
    is True once the heating check question has been asked on this call."""
    signal = clear_signal(text, cfg, answering_check=state == "checking")
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
    if not heating_asked and heating_signal(text, cfg):
        check = cfg["heating_check"]["kind"]
        # A call that already cleared this same question isn't asked it again.
        if state == "none" or (state == "cleared" and kind != check):
            return SafetyAction("ask_check", check, cfg["vague"][check]["question"])
    return SafetyAction("none", kind)


def agent_said_lines(text: str, kind: str | None, cfg: dict) -> bool:
    """The agent line contains the full safety lines for the call's kind, word for word."""
    return bool(kind) and words(lines_for(cfg, kind)) in words(text)
