"""Dead air on a live call (DECISIONS.md 081): how long the caller has been quiet, a check-in line
every `check_in_s`, and the goodbye at `hang_up_s`, after which the worker hangs up with the end
reason `stepped_away`.

The count restarts only on a caller line the tool server calls a reply (app/dead_air.py: on a hold,
a TV or someone else in the room is an aside) and on an agent reply. Sound alone never restarts it:
Silero's voice activity says only that someone is speaking, so while it does, a check-in waits
rather than talking over them, and the count goes on. The interruption settings
(`voice.interruption`, at least two recognized words) keep a TV from cutting off a check-in.

Shared by the voice layers and with no LiveKit imports, so the text harness (evals/stacks.py) runs
the same timer on a short clock. The LiveKit worker drives it (voice/livekit/worker.py).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

CHECK_IN = "check_in"
GOODBYE = "goodbye"


class DeadAir:
    """The silence count for one call. `clock` returns seconds; tests and evals pass their own."""

    def __init__(self, check_in_s: float, hang_up_s: float, clock: Callable[[], float] = time.monotonic):
        if not 0 < check_in_s < hang_up_s:
            raise ValueError("check_in_s must be above 0 and below hang_up_s")
        self.check_in_s = check_in_s
        self.hang_up_s = hang_up_s
        self.clock = clock
        self.done = False
        self.check_ins = 0
        self.reply()

    def reply(self) -> None:
        """Someone replied: the caller is back, or the agent answered."""
        self.quiet_since = self.clock()
        self.next_check_in = self.quiet_since + self.check_in_s
        self.check_ins = 0

    def quiet_for(self) -> float:
        return self.clock() - self.quiet_since

    def due(self) -> str | None:
        """What to say now, if anything: the goodbye once the limit is reached, else a check-in
        `check_in_s` after the silence began or after the last check-in (one held back while the
        caller's side had sound is said once it stops, and the next counts from there)."""
        if self.done:
            return None
        if self.quiet_for() >= self.hang_up_s:
            return GOODBYE
        if self.clock() >= self.next_check_in:
            return CHECK_IN
        return None

    def said(self, action: str) -> None:
        if action == CHECK_IN:
            self.check_ins += 1
            self.next_check_in = self.clock() + self.check_in_s
        elif action == GOODBYE:
            self.done = True


async def watch(
    dead_air: DeadAir,
    busy: Callable[[], bool],
    sound: Callable[[], bool],
    check_in: Callable[[], Awaitable[None]],
    goodbye: Callable[[], Awaitable[None]],
    poll_s: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Runs for the whole call. Nothing is said while `busy` (the agent is speaking, or the caller
    is on hold music for a transfer). While `sound` (voice activity on the caller's side) a check-in
    waits, but the goodbye doesn't: a TV left on would otherwise keep the line open for good.
    Returns after the goodbye."""
    while True:
        await sleep(poll_s)
        if busy():
            continue
        action = dead_air.due()
        if action is None or (action == CHECK_IN and sound()):
            continue
        dead_air.said(action)
        if action == GOODBYE:
            await goodbye()
            return
        await check_in()
