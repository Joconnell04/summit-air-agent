"""The bridge line: a short pre-recorded clip played while a slow tool runs, so the caller hears
something instead of silence (docs/latency.md, spec S-37a).

Rules, all enforced here:
- Only when the tool hasn't answered after `after_s`; fast tools never trigger it.
- Only while the agent and the caller are both silent.
- At most once every `min_turns_between` caller turns, never the same clip twice in a call.
- Never overlaps the agent: the reply's audio waits for the clip to finish (`wait_clear`), while the
  model's next step runs alongside it.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

# Longest a bridge clip may be. Longer clips are refused at load (voice/livekit/clips.py).
MAX_CLIP_SECONDS = 1.1


class Bridge:
    def __init__(
        self,
        clips: list[str],
        play: Callable[[str], Awaitable[None]],
        silent: Callable[[], bool],
        after_s: float = 0.7,
        min_turns_between: int = 3,
    ):
        self._unused = list(clips)
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

    def _pick(self) -> str | None:
        if not self._unused or not self._silent():
            return None
        if self._playing and not self._playing.done():
            return None
        if self._last_turn is not None and self._turn - self._last_turn < self.min_turns_between:
            return None
        self._last_turn = self._turn
        clip = self._unused.pop(0)
        self.played.append(clip)
        return clip

    async def run(self, work: Awaitable):
        """Run a tool call; start a clip if it is slow and the rules allow one."""
        task = asyncio.ensure_future(work)
        done, _ = await asyncio.wait({task}, timeout=self.after_s)
        if not done:
            clip = self._pick()
            if clip:
                self.clip_started_at = time.time()
                self._playing = asyncio.create_task(self._play(clip))
        return await task

    def playing(self) -> bool:
        return self._playing is not None and not self._playing.done()

    async def wait_clear(self) -> None:
        """Returns once no clip is playing. The agent's audio waits on this before its first frame."""
        if self._playing:
            # A clip that fails to play must not hold the reply back.
            await asyncio.gather(self._playing, return_exceptions=True)
