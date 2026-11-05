"""One rig call as the caller lived it: when each side spoke, what was said, and the silences
between. The rig builds it as the call runs (evals/audio_rig/rig.py) and the live-like checks
read it (evals/audio_rig/live_checks.py). Times are seconds on the rig's clock
(time.perf_counter), the same clock the reply delays are measured on.
"""
from dataclasses import dataclass, field
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
