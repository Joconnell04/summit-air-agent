"""The bridge line: a short pre-recorded clip ("Okay, just give me one second.") so the caller hears
something instead of silence while work runs (docs/latency.md, spec S-37a).

Two triggers:
- A tool that hasn't answered after `after_s`.
- A look-up the model's reply calls when the caller heard nothing from that reply first (`lead_in`):
  the wait there is the model's second step after the tool, about a second, not the tool itself
  (live call 1, issue #149, where every tool answered inside 0.3 s and the caller heard silence).

Rules, all enforced here:
- Only while the agent and the caller are both silent.
- At most once every `min_turns_between` caller turns, and never the same clip twice in a row.
- Never overlaps the agent: the reply's audio waits for the clip to finish (`wait_clear`), while the
  model's next step runs alongside it.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

# Longest a bridge clip may be. Longer clips are refused at load (voice/livekit/clips.py). A clip
# fills the model's second step, about a second (docs/latency.md), so a much longer one only delays
# the answer.
MAX_CLIP_SECONDS = 1.6


class Bridge:
    def __init__(
        self,
        clips: list[str],
        play: Callable[[str], Awaitable[None]],
        silent: Callable[[], bool],
        after_s: float = 0.7,
        min_turns_between: int = 3,
    ):
        self._clips = list(clips)
        self._last_clip: str | None = None
        self._play = play
        self._silent = silent
        self.after_s = after_s
        self.min_turns_between = min_turns_between
        self._turn = 0
        self._last_turn: int | None = None
        self._playing: asyncio.Task | None = None
        self.played: list[str] = []
        # When this turn's clip started (wall clock, as LiveKit stamps its lines), if one did.
        self.clip_started_at: float | None = None

    def caller_turn(self) -> None:
        self._turn += 1
        self.clip_started_at = None

    def _next_clip(self) -> str | None:
        """The clips in turn, so the same one is never heard twice in a row."""
        if not self._clips:
            return None
        if self._last_clip is None:
            return self._clips[0]
        clip = self._clips[(self._clips.index(self._last_clip) + 1) % len(self._clips)]
        return None if clip == self._last_clip else clip

    def _pick(self) -> str | None:
        if not self._silent():
            return None
        if self._playing and not self._playing.done():
            return None
        if self._last_turn is not None and self._turn - self._last_turn < self.min_turns_between:
            return None
        clip = self._next_clip()
        if clip is None:
            return None
        self._last_turn = self._turn
        self._last_clip = clip
        self.played.append(clip)
        return clip

    def _start(self, clip: str) -> None:
        self.clip_started_at = time.time()
        self._playing = asyncio.create_task(self._play(clip))

    def lead_in(self) -> bool:
        """The model's reply calls a look-up and the caller heard nothing from it first: a clip
        now, if the rules allow one, rather than silence until the model's next step speaks."""
        clip = self._pick()
        if clip:
            self._start(clip)
        return clip is not None

    async def run(self, work: Awaitable):
        """Run a tool call; start a clip if it is slow and the rules allow one."""
        task = asyncio.ensure_future(work)
        done, _ = await asyncio.wait({task}, timeout=self.after_s)
        if not done:
            clip = self._pick()
            if clip:
                self._start(clip)
        return await task

    def playing(self) -> bool:
        return self._playing is not None and not self._playing.done()

    async def wait_clear(self) -> None:
        """Returns once no clip is playing. The agent's audio waits on this before its first frame."""
        if self._playing:
            # A clip that fails to play must not hold the reply back.
            await asyncio.gather(self._playing, return_exceptions=True)
