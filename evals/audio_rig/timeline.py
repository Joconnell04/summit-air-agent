"""One rig call as the caller lived it: when each side spoke, what was said, and the silences
between. The rig builds it as the call runs (evals/audio_rig/rig.py) and the live-like checks
read it (evals/audio_rig/live_checks.py). Times are seconds on the rig's clock
(time.perf_counter), the same clock the reply delays are measured on.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Word:
    """A word of the agent's audio as the rig's ears heard it, with when it played."""

    text: str
    start: float
    end: float


@dataclass
class AgentPart:
    """One stretch of agent speech. A turn has a second part when the agent went quiet, the
    caller waited, and the agent spoke again before the caller did."""

    text: str
    first_sound: float | None
    last_sound: float | None
    # Voiced stretches, start and end, so a silence inside the part shows.
    sounds: list[tuple[float, float]] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    # The caller cut in before the agent finished this part.
    cut: bool = False


@dataclass
class AgentTurn:
    """Everything the agent said between two caller lines. `after` is when the caller stopped,
    or when the call connected for the greeting."""

    after: float
    parts: list[AgentPart] = field(default_factory=list)
    lookup: bool = False
    # When the agent made a sound while the caller was still speaking, if it did.
    over_caller: float | None = None
    # Model requests the voice program logged for this turn's lines, when it logs them.
    llm_requests: int | None = None

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.parts if p.text).strip()

    @property
    def first_sound(self) -> float | None:
        return next((p.first_sound for p in self.parts if p.first_sound is not None), None)

    @property
    def last_sound(self) -> float | None:
        ends = [p.last_sound for p in self.parts if p.last_sound is not None]
        return max(ends) if ends else None


@dataclass
class CallerLine:
    text: str
    start: float
    end: float


@dataclass
class Timeline:
    events: list[AgentTurn | CallerLine] = field(default_factory=list)
    # The scenario's local start time, so "tomorrow" in a line can be named as a weekday.
    started: datetime | None = None
    # When the agent's side of the line dropped, if the agent ended the call.
    hung_up_at: float | None = None
    # Who ended the call ("agent" or "caller"), and when the rig closed the line.
    ended_by: str | None = None
    ended_at: float | None = None

    def agent_turns(self) -> list[AgentTurn]:
        return [e for e in self.events if isinstance(e, AgentTurn)]

    def next_start(self, index: int) -> float | None:
        """When the next sound after event `index` began: the caller's next line or the agent's
        next turn."""
        for e in self.events[index + 1:]:
            if isinstance(e, CallerLine):
                return e.start
            if e.first_sound is not None:
                return e.first_sound
        return None

    def caller_before(self, index: int) -> CallerLine | None:
        for e in reversed(self.events[:index]):
            if isinstance(e, CallerLine):
                return e
        return None


def to_dict(timeline: Timeline) -> dict:
    """The timeline as JSON-ready data, kept beside each recording so a call can be re-scored
    when a check changes (evals/audio_rig/live_report.py --rescore)."""
    events = [{"kind": "caller" if isinstance(e, CallerLine) else "agent", **asdict(e)}
              for e in timeline.events]
    return {"events": events,
            "started": timeline.started.isoformat() if timeline.started else None,
            "hung_up_at": timeline.hung_up_at, "ended_by": timeline.ended_by,
            "ended_at": timeline.ended_at}


def from_dict(data: dict) -> Timeline:
    events: list[AgentTurn | CallerLine] = []
    for e in data["events"]:
        e = dict(e)
        if e.pop("kind") == "caller":
            events.append(CallerLine(**e))
            continue
        parts = []
        for p in e.pop("parts"):
            words = [Word(**w) for w in p.pop("words")]
            parts.append(AgentPart(**{**p, "sounds": [tuple(x) for x in p["sounds"]],
                                      "words": words}))
        events.append(AgentTurn(**e, parts=parts))
    started = datetime.fromisoformat(data["started"]) if data.get("started") else None
    return Timeline(events, started, data.get("hung_up_at"), data.get("ended_by"),
                    data.get("ended_at"))
