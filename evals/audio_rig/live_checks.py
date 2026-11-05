"""Checks on how a rig call sounded, read from its timeline (evals/audio_rig/timeline.py).

The text suite scored 91% while Jackson's live calls 1 and 2 went badly, because a transcript has
no silences, no half replies and no repeated lines (evals/rounds/live-like-01.md). These checks
fail on exactly those. Each returns failure lines, `name: what happened`, quoting the line it is
about. Thresholds come from config/models.yaml `audio_rig.live_checks`; every one is ASSUMED
except the S-36 medians.

The text heuristics (what counts as a question, a repeat request, a time) are deliberately plain
and are pinned by tests/evals/test_live_checks.py.
"""
import re
from datetime import timedelta
from difflib import SequenceMatcher

from evals.audio_rig.timeline import AgentPart, AgentTurn, CallerLine, Timeline
from evals.compare import percentile

NAMES = ("silence", "half_reply", "repeat", "first_sound", "talk_over", "llm_requests",
         "readback_rate", "hangup_gap", "time_as_asked")

NUMBER_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
DIGIT_WORDS = {w for w, n in NUMBER_WORDS.items() if n < 10}
# A last sentence that opens like this is a question even when the ears dropped the "?".
QUESTION_START = re.compile(
    r"^(would|should|shall|can|could|do|does|did|is|are|was|were|will|what|when|which|how|who|"
    r"where|may|want|anything)\s", re.IGNORECASE)
# The caller asking to hear something again: an exact repeat then is the right answer.
REPEAT_ASK = re.compile(
    r"\b(again|repeat|pardon|come again|didn't (catch|hear)|what was that|hello)\b|^\W*(what|sorry|"
    r"huh)\W*$", re.IGNORECASE)
# Questions that close a call rather than ask the caller to agree to something.
CLOSERS = re.compile(r"\b(anything else|something else|else i can help|help with anything|"
                     r"anything i can help|is there anything)\b", re.IGNORECASE)
# A goodbye ends the call; the silence after one is the hang-up check's, not a half reply.
GOODBYE = re.compile(r"\b(thanks for calling|thank you for calling|goodbye|bye now|"
                     r"have a (great|good|nice))\b", re.IGNORECASE)
NEGATION = re.compile(r"\bnot\b|n't\b|\bcannot\b|\bno\b", re.IGNORECASE)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def cfg_of(config: dict) -> dict:
    return config["audio_rig"]["live_checks"]


def quote(text: str, words: int = 12) -> str:
    parts = text.split()
    short = " ".join(parts[:words]) + (" ..." if len(parts) > words else "")
    return f'"{short}"'


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def normal(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", text.lower()).split())


def is_question(sentence: str) -> bool:
    s = sentence.strip()
    return s.endswith("?") or bool(QUESTION_START.match(s))


def ends_with_question(text: str) -> bool:
    """The last sentence asks something, or the one before it does and the last only explains
    ("Would another day work? I can check Monday.")."""
    return any(is_question(s) for s in sentences(text)[-2:])


def after_line(timeline: Timeline, index: int) -> str:
    line = timeline.caller_before(index)
    return f"after the caller said {quote(line.text)}" if line else "after the call connected"


# silence ----------------------------------------------------------------------------------------

def silence(timeline: Timeline, cfg: dict) -> list[str]:
    """A wait over `silence_after_caller_s` from the caller stopping to the agent's first sound
    (or no sound at all), and a gap inside the agent's turn over `silence_in_lookup_s` on a turn
    with a lookup, or over `silence_after_caller_s` on one without."""
    out = []
    for i, e in enumerate(timeline.events):
        if not isinstance(e, AgentTurn) or timeline.caller_before(i) is None:
            continue
        first = e.first_sound
        if first is None:
            out.append(f"silence: no reply at all {after_line(timeline, i)}")
            continue
        wait = first - e.after
        if wait > cfg["silence_after_caller_s"]:
            out.append(f"silence: {wait:.1f} s before the agent spoke {after_line(timeline, i)}")
        limit = cfg["silence_in_lookup_s"] if e.lookup else cfg["silence_after_caller_s"]
        for before, gap in gaps_in_turn(e):
            if gap > limit:
                what = "a lookup" if e.lookup else "the agent's turn"
                out.append(f"silence: {gap:.1f} s with no sound in {what} after "
                           f"{quote(before)}")
    return out


def gaps_in_turn(turn: AgentTurn) -> list[tuple[str, float]]:
    """Silences inside a turn, each with the text spoken before it: between the voiced stretches
    of a part, and between one part and the next."""
    out = []
    parts = [p for p in turn.parts if p.first_sound is not None]
    for n, part in enumerate(parts):
        for (_, end), (start, _) in zip(part.sounds, part.sounds[1:]):
            out.append((words_before(part, end), start - end))
        if n + 1 < len(parts):
            out.append((part.text, parts[n + 1].first_sound - part.last_sound))
    return out


def words_before(part: AgentPart, t: float) -> str:
    said = [w.text for w in part.words if w.end <= t + 0.05]
    return " ".join(said[-12:]) if said else part.text


# half replies -----------------------------------------------------------------------------------

def half_reply(timeline: Timeline, cfg: dict) -> list[str]:
    """An agent part that ends without a question and is followed by silence: the caller is left
    waiting to be asked something. A part the caller cut into, or the last words of the call, is
    not one."""
    out = []
    for i, e in enumerate(timeline.events):
        if not isinstance(e, AgentTurn):
            continue
        parts = [p for p in e.parts if p.first_sound is not None]
        for n, part in enumerate(parts):
            if (part.cut or not part.text or ends_with_question(part.text)
                    or GOODBYE.search(part.text)):
                continue
            last = n + 1 == len(parts)
            nxt = parts[n + 1].first_sound if not last else timeline.next_start(i)
            # Nothing after it: the caller gave up on the silence and hung up.
            nxt = nxt if nxt is not None else timeline.ended_at
            if nxt is None:
                continue
            gap = nxt - part.last_sound
            if gap >= cfg["half_reply_silence_s"]:
                who = ("the agent went on" if not last else
                       "the caller spoke" if timeline.next_start(i) is not None else
                       "the caller hung up")
                out.append(f"half_reply: {quote(part.text)} asked nothing, then {gap:.1f} s of "
                           f"silence before {who}")
    return out


# repeats ----------------------------------------------------------------------------------------

def repeat(timeline: Timeline, cfg: dict) -> list[str]:
    """The same agent line twice in a row: a sentence said twice within a turn, or a turn that
    says the last one again when the caller hadn't asked to hear it again."""
    out = []
    min_words, ratio = cfg["repeat_min_words"], cfg["repeat_similarity"]
    previous: AgentTurn | None = None
    asked_again = False
    for e in timeline.events:
        if isinstance(e, CallerLine):
            asked_again = bool(REPEAT_ASK.search(e.text))
            continue
        said = [normal(s) for s in sentences(e.text)]
        seen = set()
        for s, raw in zip(said, sentences(e.text)):
            if len(s.split()) >= min_words and s in seen:
                out.append(f"repeat: {quote(raw)} said twice in one turn")
                break
            seen.add(s)
        if previous is not None and not asked_again:
            a, b = normal(previous.text), normal(e.text)
            if len(b.split()) >= min_words and SequenceMatcher(None, a, b).ratio() >= ratio:
                out.append(f"repeat: {quote(e.text)} said again in the next turn")
        if e.text:
            previous = e
        asked_again = False
    return out


# time to first sound ----------------------------------------------------------------------------

def reply_delays(timeline: Timeline) -> dict[str, list[float]]:
    """Milliseconds from the caller stopping to the agent's first sound, by turn kind."""
    out: dict[str, list[float]] = {"plain": [], "lookup": []}
    for i, e in enumerate(timeline.events):
        if isinstance(e, AgentTurn) and timeline.caller_before(i) is not None and e.first_sound:
            out["lookup" if e.lookup else "plain"].append(max(0.0, e.first_sound - e.after) * 1000)
    return out


def first_sound(timeline: Timeline, cfg: dict) -> list[str]:
    """The median and slowest 10% of the call's reply delays against the targets, by turn kind."""
    out = []
    for kind, values in reply_delays(timeline).items():
        if not values:
            continue
        target = cfg["first_sound_ms"][kind]
        for label, share in (("median", 0.5), ("p90", 0.9)):
            got = percentile(values, share)
            if got > target[label]:
                out.append(f"first_sound: {kind} {label} {got:.0f} ms over {target[label]} ms "
                           f"({len(values)} turns)")
    return out


# talking over the caller -----------------------------------------------------------------------

def talk_over(timeline: Timeline, cfg: dict) -> list[str]:
    """The agent making a sound while the caller is still speaking, or starting its reply within
    `talk_over_ms` of the caller's end, which means it was already playing. A turn after the
    caller cut in is left out: there the caller talked over the agent."""
    out = []
    events = timeline.events
    for i, e in enumerate(events):
        line = timeline.caller_before(i)
        if not isinstance(e, AgentTurn) or line is None:
            continue
        before = next((x for x in reversed(events[:i]) if isinstance(x, AgentTurn)), None)
        if before is not None and before.parts and before.parts[-1].cut:
            continue
        if e.over_caller is not None:
            early = (line.end - e.over_caller) * 1000
            out.append(f"talk_over: the agent made a sound {early:.0f} ms before the caller "
                       f"finished {quote(line.text)}")
        elif e.first_sound is not None and (e.first_sound - e.after) * 1000 < cfg["talk_over_ms"]:
            out.append(f"talk_over: the reply was already playing when the caller finished "
                       f"{quote(line.text)}")
    return out


# model requests ---------------------------------------------------------------------------------

def llm_requests(timeline: Timeline, cfg: dict) -> list[str]:
    """More model requests in a turn than its kind needs: each one is a wait the caller hears."""
    out = []
    for i, e in enumerate(timeline.events):
        if not isinstance(e, AgentTurn) or e.llm_requests is None:
            continue
        kind = "lookup" if e.lookup else "plain"
        limit = cfg["max_llm_requests"][kind]
        if e.llm_requests > limit:
            out.append(f"llm_requests: {e.llm_requests} model requests on a {kind} turn "
                       f"(limit {limit}) {after_line(timeline, i)}")
    return out


# read-back speed --------------------------------------------------------------------------------

def digits_in(word: str) -> int:
    w = normal(word)
    return sum(c.isdigit() for c in w) or (1 if w in DIGIT_WORDS else 0)


def readback_rate(timeline: Timeline, cfg: dict) -> list[str]:
    """A run of spoken digits (a phone number, a ZIP) read faster than `readback_digits_per_s`,
    timed from the first digit word's start to the last one's end."""
    out = []
    for e in timeline.agent_turns():
        for part in e.parts:
            run: list = []
            for word in part.words + [None]:
                if word is not None and digits_in(word.text):
                    run.append(word)
                    continue
                digits = sum(digits_in(w.text) for w in run)
                seconds = run[-1].end - run[0].start if run else 0
                if digits >= cfg["readback_min_digits"] and seconds > 0:
                    rate = digits / seconds
                    if rate > cfg["readback_digits_per_s"]:
                        said = " ".join(w.text for w in run)
                        out.append(f"readback_rate: {quote(said)} at {rate:.1f} digits/s, over "
                                   f"{cfg['readback_digits_per_s']}")
                run = []
    return out


# hang-up ----------------------------------------------------------------------------------------

def hangup_gap(timeline: Timeline, cfg: dict) -> list[str]:
    """The agent dropping the line less than `hangup_after_goodbye_s` after its last sound."""
    turns = [t for t in timeline.agent_turns() if t.last_sound is not None]
    if timeline.ended_by == "agent" and timeline.hung_up_at is None:
        return ["hangup_gap: the agent ended the call but the rig recorded no hang-up time"]
    if timeline.hung_up_at is None or not turns:
        return []
    gap = timeline.hung_up_at - turns[-1].last_sound
    if gap < cfg["hangup_after_goodbye_s"]:
        return [f"hangup_gap: the line dropped {gap:.1f} s after {quote(turns[-1].text)}"]
    return []


# a requested time said back ---------------------------------------------------------------------

def words_to_numbers(text: str) -> str:
    return re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b",
                  lambda m: str(NUMBER_WORDS[m.group(1).lower()]), text, flags=re.IGNORECASE)


CLOCK = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|o'clock)",
                   re.IGNORECASE)
RANGE = re.compile(r"\b(between\s+)?(1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(a\.?m\.?|p\.?m\.?)?\s*"
                   r"(to|and|-|until)\s*(1[0-2]|0?[1-9])\b", re.IGNORECASE)


def clock_times(text: str) -> list[tuple[int, int, int]]:
    """(hour on a 12-hour clock, minute, position) for each clock time in the text."""
    return [(int(m.group(1)) % 12, int(m.group(2) or 0), m.start())
            for m in CLOCK.finditer(words_to_numbers(text))]


def days_named(text: str, started) -> set[str]:
    low = text.lower()
    days = {d for d in DAYS if re.search(rf"\b{d}\b", low)}
    if started is not None:
        if re.search(r"\btomorrow\b", low):
            days.add(DAYS[(started + timedelta(days=1)).weekday()])
        if re.search(r"\b(today|tonight)\b", low):
            days.add(DAYS[started.weekday()])
    return days


def in_window(plain: str, at: int) -> bool:
    """The time at `at` opens or closes a window ("between 2 PM and 3 PM", "2 to 3")."""
    return any(m.start() <= at < m.end() for m in RANGE.finditer(plain))


def said_back(asked: set[tuple[int, int]], text: str) -> bool:
    """The asked time said as a time: not negated ("not at 2 PM") and not as one end of a
    window ("between 2 and 3")."""
    plain = words_to_numbers(text)
    for hour, minute, at in clock_times(text):
        before = plain[max(0, at - 10):at]
        if (hour, minute) in asked and not NEGATION.search(before) and not in_window(plain, at):
            return True
    return False


def time_as_asked(timeline: Timeline, cfg: dict) -> list[str]:
    """A caller asks for a clock time ("Friday at 2 PM"); the agent's next turn that names a time
    has to say that time back, on that day, or ask whether another works. Telling the caller a
    different time or a window around it ("tomorrow between 1 and 3. Anything else?") fails."""
    out = []
    events = timeline.events
    for i, e in enumerate(events):
        if not isinstance(e, CallerLine):
            continue
        asked = {(h, m) for h, m, _ in clock_times(e.text)}
        # A caller naming a window ("twelve to four is fine, two works") is taking the window.
        if not asked or RANGE.search(words_to_numbers(e.text)):
            continue
        asked_days = days_named(e.text, timeline.started)
        answered = False
        for later in events[i + 1:]:
            if isinstance(later, CallerLine):
                if clock_times(later.text):
                    break
                continue
            text = later.text
            if not (clock_times(text) or RANGE.search(words_to_numbers(text))):
                continue
            answered = True
            offers = [s for s in sentences(text) if is_question(s) and not CLOSERS.search(s)]
            told_days = days_named(text, timeline.started)
            wrong_day = bool(asked_days and told_days and not asked_days & told_days)
            if (not said_back(asked, text) or wrong_day) and not offers:
                out.append(f"time_as_asked: the caller asked {quote(e.text)}; the agent said "
                           f"{quote(text, 20)}")
            break
        if not answered:
            out.append(f"time_as_asked: the caller asked {quote(e.text)}; the agent never said "
                       f"a time back")
    return out


CHECKS = {
    "silence": silence, "half_reply": half_reply, "repeat": repeat, "first_sound": first_sound,
    "talk_over": talk_over, "llm_requests": llm_requests, "readback_rate": readback_rate, "hangup_gap": hangup_gap, "time_as_asked": time_as_asked,
}


def exchange(timeline: Timeline) -> list[str]:
    """A call with no caller line can't pass: there was nothing to time. (A caller line with no
    reply is the `silence` check's.)"""
    if any(isinstance(e, CallerLine) for e in timeline.events):
        return []
    return ["silence: the call never got past the greeting, so nothing could be checked"]


def run(timeline: Timeline, config: dict) -> list[str]:
    """Every live-like check on one call, as failure lines."""
    cfg = cfg_of(config)
    return exchange(timeline) + [line for name in NAMES for line in CHECKS[name](timeline, cfg)]


# the live calls themselves ----------------------------------------------------------------------

# ASSUMED speaking rates to place each logged line in time: the server logs a line to the second,
# not its length.
AGENT_WORDS_PER_S = 2.7
CALLER_WORDS_PER_S = 3.0


def timeline_from_live_call(doc: dict, started=None) -> Timeline:
    """A rough timeline of a live call from the server's turns (evals/live_calls/), so the checks
    can be run on the calls that prompted them. The server logs a caller line once its turn ends
    and an agent line once it finishes playing, to the second; the worker's own `end_of_turn_ms`
    and `reply_ms` place the caller's last word and the agent's first sound, and word counts at
    the rates above give the rest."""
    timeline = Timeline(started=started)
    last_end = 0.0
    for turn in doc["turns"]:
        t, text = float(turn["t"]), turn["text"]
        length = len(text.split()) / AGENT_WORDS_PER_S
        if turn["speaker"] == "caller":
            end = max(last_end, t - turn.get("end_of_turn_ms", 0) / 1000)
            start = max(last_end, end - len(text.split()) / CALLER_WORDS_PER_S)
            timeline.events.append(CallerLine(text, start, end))
            last_end = end
            continue
        previous = timeline.events[-1] if timeline.events else None
        if isinstance(previous, CallerLine) and turn.get("reply_ms"):
            start = previous.end + turn["reply_ms"] / 1000
        else:
            start = max(last_end, t - length)
        end = max(t, start + length)
        part = AgentPart(text, start, end, [(start, end)])
        if not isinstance(previous, AgentTurn):
            previous = AgentTurn(after=last_end)
            timeline.events.append(previous)
        previous.parts.append(part)
        previous.lookup = previous.lookup or bool(turn.get("tool_ms"))
        if turn.get("llm_requests"):
            previous.llm_requests = (previous.llm_requests or 0) + int(turn["llm_requests"])
        last_end = end
    return timeline
