"""The safety check run on every caller turn (docs/call-journeys.md, spine step 4 and journey 3).

A fixed list of phrases, no model: a clear signal ("I smell gas", "the CO alarm is going off")
switches the call to the safety lines at once. "Gas" alone, a gas bill or a gas furnace triggers
nothing, because only phrases describing a smell, leak, alarm or symptom are listed. A vague
description gets one check question; a yes is a clear signal and a clear no clears the call.

A worry with nothing described ("I don't want to go down there if I smell gas", "is it a gas leak?")
is not a report, so it gets the check question rather than the lines (eval round 3). After the lines,
a caller who says they never smelled anything ("I didn't say I smelled gas") stands the call down.

A smell that is usually harmless but sometimes hides gas (burning dust the first time the heat runs)
gets a lighter confirm question once per call. It is asked the negative way ("it doesn't smell like
sulfur or rotten eggs, right?"), so "yeah", "right" or "no" clear it, and the smell named again,
"it does" or an unsure answer bring the lines.

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
    "nothing like",
)
# Words that can sit between a negator and the mention: "it's not a gas leak", "no sign of a gas
# leak" is still a denial (eval round 3, gas-worry-no-smell).
ARTICLES = {"a", "an", "any", "the"}
# In the answer to the check question only: the carbon monoxide question asks whether anyone feels
# sick or dizzy, so "nobody's feeling sick or dizzy" is a no for "dizzy" as "nobody's dizzy" is.
PERSON_NEGATORS = (
    "nobody",
    "nobodys",
    "nobody is",
    "nobody feels",
    "nobodys feeling",
    "nobody is feeling",
    "no one",
    "no ones",
    "no one is",
    "no one feels",
    "no ones feeling",
    "no one is feeling",
    "not feeling",
)
# Words that can sit between a person negator and a symptom: "nobody's feeling sick or dizzy". A
# plain "no" before them negates nothing ("no, sick and dizzy").
LIST_WORDS = {"sick", "or", "and", "feeling", "feel", "feels"}
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
# "A little" and the like are a yes ("yes, a little", "no, just a bit"), except right before a word
# that says how the caller feels about the cold: "I just feel a little chilled".
NOT_A_SIGN = {"chilled", "chilly", "cold", "cool", "annoyed", "frustrated", "worried", "nervous"}
UNSURE = ("dont know", "not sure", "no idea", "cant tell")
# The answer to the confirm question, which asks the negative way: these say it does smell.
CONFIRM_YES = (
    "it does",
    "does smell",
    "smell something",
    "something strong",
    "strong smell",
    "actually",
    "kind of",
    "sort of",
    "a little",
    "a bit",
    "maybe",
    "i think so",
    "now that you mention it",
)
# Words that can sit around the agreement without adding anything: "yeah, it was just like that
# for a minute". The trade's own signal words and its `confirm_filler` are added at run time, so a
# negated "no gas smell" is filler too. Anything else in the answer ("right, but the dog is acting
# strange") makes it unclear, so the plain check question is asked.
CONFIRM_FILLER = {
    "a", "after", "all", "and", "any", "anymore", "anything", "at", "away", "came", "did", "didnt",
    "dont", "else", "never", "few", "fine", "first", "for", "going", "gone", "good", "i", "im", "it", "its", "just", "kicked",
    "know", "like", "minute", "minutes", "normal", "not", "now", "of", "off", "oh", "ok", "okay",
    "on", "only", "or", "really", "season", "seconds", "so", "summer", "sure", "that", "thats",
    "thanks", "the", "there", "think", "this", "time", "totally", "uh", "um", "up", "was", "went",
    "when", "while", "year", "you",
}
# And these agree that it doesn't.
CONFIRM_NO = (
    "yes",
    "yeah",
    "yep",
    "yup",
    "right",
    "correct",
    "exactly",
    "no",
    "nope",
    "nah",
    "not really",
    "nothing",
    "doesnt",
    "does not",
)
NO = (
    "no",
    "nope",
    "nah",
    "not really",
    "nothing",
    "i dont",
    "dont smell",
    "doesnt smell",
    "does not smell",
    "not smelling",
)
# In the answer to a check question only: a caller who echoes the question ("no rotten eggs or
# sulfur", "it doesn't smell like sulfur") is saying no. These words can sit between the negator
# and the mention, and these endings negate what follows them.
ECHO_WORDS = {"or", "and", "any", "rotten", "egg", "eggs", "sulfur", "sulphur"}
ECHO_NEGATORS = (
    "doesnt smell",
    "does not smell",
    "dont smell",
    "didnt smell",
    "did not smell",
    "never smelled",
    "not like",
    "nothing like",
    "doesnt smell like",
    "does not smell like",
    "dont smell like",
    "not smelling like",
    "no smell of",
    # "no gas smell or anything like eggs": a hedge at the end of a denial (eval round 3 run
    # 20261104-031113, burning-dust-first-heat).
    "anything",
)
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


# Words that make a mention a worry rather than a report: "if I smell gas", "is it a gas leak?".
# Up to HYPOTHETICAL_GAP of the HYPOTHETICAL_FILLER words may sit between one and the mention.
HYPOTHETICAL = (
    "if",
    "in case",
    "what if",
    "whether",
    "could it be",
    "could that be",
    "could this be",
    "is it",
    "is that",
    "is this",
    "would it be",
    "might it be",
    "worried about",
    "afraid of",
)
HYPOTHETICAL_FILLER = {
    "i", "we", "you", "they", "ever", "there", "theres", "is", "was", "a", "an", "any", "some",
    "like", "start", "started", "to", "get", "it", "its", "were", "maybe", "even", "do", "did",
    "might", "may", "could", "would", "be", "possibly",
}
HYPOTHETICAL_GAP = 3
# After the lines: the caller says the signal was a misunderstanding. Words saying the smell has
# only faded ("I don't smell it anymore", "not out here") never stand a call down.
CORRECTIONS = (
    "didnt say",
    "did not say",
    "never said",
    "i didnt mean",
    "i meant if",
    "i said if",
    "didnt smell",
    "did not smell",
    "never smelled",
    "theres no smell",
    "there is no smell",
    "no smell at all",
)
FADED = (
    "anymore",
    "any more",
    "now",
    "outside",
    "out here",
    "stopped",
    "went away",
    "gone",
    "faded",
    "not as",
    "less",
)


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


def _person_negated(before: str) -> bool:
    """Check answers only: a person negator before the mention, past any list words, so "nobody's
    feeling sick or dizzy" negates "dizzy"."""
    kept = before.split()
    while kept and kept[-1] in LIST_WORDS:
        kept.pop()
    trimmed = " " + " ".join(kept) + " "
    return any(trimmed.endswith(words(n)) for n in PERSON_NEGATORS)


def _echo_negated(before: str) -> bool:
    """Check answers only: a negator before the mention, past any echoed question words, so "no
    rotten eggs or sulfur" negates "sulfur". "No, it smells like rotten eggs" is not negated."""
    kept = before.split()
    while kept and kept[-1] in ECHO_WORDS:
        kept.pop()
    trimmed = " " + " ".join(kept) + " "
    return any(trimmed.endswith(words(n)) for n in NEGATORS + ECHO_NEGATORS)


def _negated(text_words: str, phrase: str, answering: bool = False) -> bool:
    """True only when every mention of the phrase comes straight after a negating word, as in
    "I don't smell gas" or "no gas smell". "I don't know, I smell gas" is not negated. In the answer
    to the check question, a person negator ("nobody's feeling sick or dizzy") and a mention followed
    by words like "is quiet" also count as negated."""
    target = words(phrase)
    start = text_words.find(target)
    if start < 0:
        return False
    while start >= 0:
        before = text_words[: start + 1]
        after = text_words[start + len(target) - 1 :]
        kept = before.split()
        while kept and kept[-1] in ARTICLES:
            kept.pop()
        bare = " " + " ".join(kept) + " "
        negated = any(bare.endswith(words(n)) for n in NEGATORS) or (
            answering and (_person_negated(before) or _echo_negated(before))
        )
        if not negated and not (answering and _quiet_after(after)):
            return False
        start = text_words.find(target, start + 1)
    return True


def _hypothetical(text_words: str, phrase: str) -> bool:
    """True only when every mention of the phrase is a worry: a word like "if" or "is it" just
    before it, past at most a few filler words. "If it's on, I smell gas" is not, since the mention
    comes after other words."""
    target = words(phrase)
    start = text_words.find(target)
    if start < 0:
        return False
    while start >= 0:
        kept = text_words[: start + 1].split()
        for _ in range(HYPOTHETICAL_GAP + 1):
            trimmed = " " + " ".join(kept) + " "
            if any(trimmed.endswith(words(h)) for h in HYPOTHETICAL):
                break
            if not kept or kept[-1] not in HYPOTHETICAL_FILLER:
                return False
            kept.pop()
        else:
            return False
        start = text_words.find(target, start + 1)
    return True


def _mentions(text: str, cfg: dict, answering_check: bool) -> list[tuple[str, bool]]:
    """Every clear phrase in the text that isn't negated, as (kind, only a worry)."""
    text_words = words(text)
    found = []
    for kind, spec in cfg["kinds"].items():
        for phrase in spec["clear"]:
            if has_phrase(text_words, phrase) and not _negated(
                text_words, phrase, answering_check
            ):
                found.append((kind, _hypothetical(text_words, phrase)))
    return found


def clear_signal(text: str, cfg: dict, answering_check: bool = False) -> str | None:
    """The kind of the first clear, not negated signal in the text, or None. answering_check is
    True for the caller's answer to the check question, where "the CO alarm is quiet" is a no. A
    mention that is only a worry ("if I smell gas") is not a signal."""
    return next((kind for kind, worry in _mentions(text, cfg, answering_check) if not worry), None)


def worry_signal(text: str, cfg: dict) -> str | None:
    """The kind of a mention that is only a worry, when the text has no clear signal."""
    mentions = _mentions(text, cfg, False)
    if any(not worry for _, worry in mentions):
        return None
    return mentions[0][0] if mentions else None


def stands_down(text: str, kind: str | None, cfg: dict) -> bool:
    """After the lines, the caller says there was never a smell or alarm: "I didn't say I smelled
    gas", "there's no gas smell". Never when they say it has only faded or they've gone out."""
    if not kind:
        return False
    text_words = words(text)
    if any(has_phrase(text_words, f) for f in FADED):
        return False
    corrected = [text_words.find(words(c)) for c in CORRECTIONS if has_phrase(text_words, c)]
    if corrected:
        # Every mention comes after the correction, so it is what's being taken back: "I didn't
        # say I smelled gas". "I smell gas, I didn't say it was bad" is still a report.
        first = min(corrected)
        mentions = [
            text_words.find(words(p))
            for spec in cfg["kinds"].values()
            for p in spec["clear"]
            if has_phrase(text_words, p)
        ]
        return all(m > first for m in mentions)
    return any(
        has_phrase(text_words, p) and _negated(text_words, p) for p in cfg["kinds"][kind]["clear"]
    ) and not clear_signal(text, cfg)


def vague_signal(text: str, cfg: dict, answering_check: bool = False) -> str | None:
    text_words = words(text)
    for kind, spec in cfg["vague"].items():
        if any(
            has_phrase(text_words, p) and not _negated(text_words, p, answering_check)
            for p in spec["phrases"]
        ):
            return kind
    return None


def confirm_signal(text: str, cfg: dict) -> str | None:
    """The kind whose confirm phrases ("burning dust") the caller used, not negated, or None."""
    text_words = words(text)
    for kind, spec in cfg["vague"].items():
        if any(
            has_phrase(text_words, p) and not _negated(text_words, p)
            for p in spec.get("confirm_phrases", [])
        ):
            return kind
    return None


# Words that end the thought about a source: "my oven works fine, to keep warm we use blankets".
GAP_STOPS = ("works", "work", "working", "worked", "fine", "but", "because", "though")
USE_WORDS = r"(?:use|using|used|run|running|have|had|turn on|turned on|put|leave|leaving)"


def _use_negated(text_words: str, start: int) -> bool:
    """A negator right on the use: "I'd never use the oven for heat", "we don't have the stove on".
    A negator elsewhere, as in "we have no heat, so the oven's on to keep warm", doesn't count."""
    before = text_words[:start]
    negators = "|".join(re.escape(words(n).strip()) for n in NEGATORS)
    return bool(
        re.search(rf" (?:{negators}) (?:{USE_WORDS} )?(?:the |a |an |my |our )?$", before)
    )


def heating_signal(text: str, cfg: dict) -> bool:
    """The caller says something that burns fuel is heating the home indoors."""
    spec = cfg.get("heating_check")
    if not spec:
        return False
    text_words = words(text)
    sources = "|".join(re.escape(words(s).strip()) for s in spec["sources"])
    used = "|".join(re.escape(words(p).strip()) for p in spec["used_for_heat"])
    heat_with = r"(?:heat|heating|warm|warming) (?:\w+ ){0,2}with (?:the |a |an |my |our )?"
    # Up to three words between a source and its use, none of which starts a new thought.
    gap = rf"(?:(?!(?:{'|'.join(GAP_STOPS)}) )\w+ ){{0,3}}"
    patterns = [
        # "s?" for "the oven's on", which reads as "ovens on" once the apostrophe is dropped.
        rf" (?:{sources})s? {gap}(?:{used}) ",
        rf" {heat_with}(?:{sources}) ",
        *(re.escape(words(p)) for p in spec["phrases"]),
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text_words):
            if not _use_negated(text_words, match.start() + 1):
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
    if clear_signal(text, cfg, answering_check=True) or vague_signal(
        text, cfg, answering_check=True
    ):
        return "yes"
    if _negated_clear(text, cfg):
        return "no"
    if any(has_phrase(text_words, w) for w in UNSURE):
        return "yes"
    # A plain denial from the tenant's list ("definitely not gas") is a no, though "definitely"
    # alone sounds like a yes.
    if any(has_phrase(text_words, w) for w in cfg.get("denials", [])):
        return "no"
    if any(_says_yes(text_words, w) for w in YES):
        return "yes"
    if any(has_phrase(text_words, w) for w in NO):
        return "no"
    return "unclear"


def answer_to_confirm(text: str, cfg: dict) -> str:
    """yes, no or unclear, for the confirm question asked the negative way. A yes still wins on any
    doubt: the smell named again, "it does", or an unsure answer."""
    text_words = words(text)
    if clear_signal(text, cfg, answering_check=True) or vague_signal(
        text, cfg, answering_check=True
    ):
        return "yes"
    if any(_says_yes(text_words, w) for w in CONFIRM_YES):
        return "yes"
    if any(has_phrase(text_words, w) for w in UNSURE):
        return "yes"
    allowed = {w for phrase in CONFIRM_NO for w in words(phrase).split()} | CONFIRM_FILLER
    for spec in cfg["kinds"].values():
        allowed |= {w for p in spec["clear"] for w in words(p).split()}
    for spec in cfg["vague"].values():
        phrases = spec["phrases"] + spec.get("confirm_phrases", []) + spec.get("confirm_filler", [])
        allowed |= {w for p in phrases for w in words(p).split()}
    if any(has_phrase(text_words, w) for w in CONFIRM_NO) and all(
        w in allowed for w in text_words.split()
    ):
        return "no"
    return "unclear"


def _says_yes(text_words: str, word: str) -> bool:
    """The yes word is there, and isn't "a little" describing the cold ("a little chilled")."""
    target = words(word)
    start = text_words.find(target)
    while start >= 0:
        following = text_words[start + len(target) :].split()[:1]
        if not following or following[0] not in NOT_A_SIGN:
            return True
        start = text_words.find(target, start + 1)
    return False


def lines_for(cfg: dict, kind: str) -> str:
    return " ".join(cfg["kinds"][kind]["lines"].split())


def check_caller_turn(
    text: str,
    state: str,
    kind: str | None,
    variant: int,
    cfg: dict,
    heating_asked: bool = False,
    confirm_asked: bool = False,
    answering_confirm: bool = False,
) -> SafetyAction:
    """What the agent must do after this caller line, given the call's safety state. heating_asked
    and confirm_asked are True once that question has been asked on this call; answering_confirm
    when the question being answered is the confirm question."""
    if state == "lines_said" and stands_down(text, kind, cfg):
        return SafetyAction("cleared", kind)
    signal = clear_signal(text, cfg, answering_check=state == "checking")
    if signal:
        if state == "lines_said" and signal == kind:
            # The caller stays in control: past the tenant's repeat_limit, a smell raised again
            # gets no more lines, and the agent helps with what they asked for (eval round 3).
            limit = cfg.get("repeat_limit")
            if limit is not None and variant >= limit:
                return SafetyAction("none", kind, variant=variant)
            variants = cfg["kinds"][kind]["variants"]
            return SafetyAction(
                "speak_variant", kind, variants[variant % len(variants)], variant + 1
            )
        return SafetyAction("speak_lines", signal, lines_for(cfg, signal))
    if state == "signal":
        # The lines haven't been said yet, so they still come before anything else.
        return SafetyAction("speak_lines", kind, lines_for(cfg, kind))
    if state == "checking":
        # Symptoms in the answer to the gas question are a carbon monoxide signal.
        other = vague_signal(text, cfg, answering_check=True)
        if other and other != kind:
            return SafetyAction("speak_lines", other, lines_for(cfg, other))
        answer = answer_to_confirm(text, cfg) if answering_confirm else answer_to_check(text, cfg)
        if answer == "yes":
            return SafetyAction("speak_lines", kind, lines_for(cfg, kind))
        if answer == "no":
            return SafetyAction("cleared", kind)
        # An unclear answer to the lighter question gets the plain check question, once.
        if answering_confirm:
            return SafetyAction("ask_check", kind, cfg["vague"][kind]["question"])
        return SafetyAction("none", kind)
    vague = vague_signal(text, cfg) or worry_signal(text, cfg)
    # A check the caller already answered no to isn't asked again for the same kind: "nobody's
    # dizzy" later on is the same answer (eval round 3, oven-heat-co-check).
    if vague and (state == "none" or (state == "cleared" and vague != kind)):
        return SafetyAction("ask_check", vague, cfg["vague"][vague]["question"])
    confirm = confirm_signal(text, cfg)
    if confirm and not confirm_asked and state in ("none", "cleared"):
        return SafetyAction("ask_check", confirm, cfg["vague"][confirm]["confirm_question"])
    if not heating_asked and heating_signal(text, cfg):
        check = cfg["heating_check"]["kind"]
        # A call that already cleared this same question isn't asked it again.
        if state == "none" or (state == "cleared" and kind != check):
            return SafetyAction("ask_check", check, cfg["vague"][check]["question"])
    return SafetyAction("none", kind)


def agent_said_lines(text: str, kind: str | None, cfg: dict) -> bool:
    """The agent line contains the full safety lines for the call's kind, word for word."""
    return bool(kind) and words(lines_for(cfg, kind)) in words(text)
