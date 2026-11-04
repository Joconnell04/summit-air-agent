"""What the rig asks of an entrant, and how it listens to one.

An entrant is a voice platform under test. Its adapter lives in its own voice folder,
`voice/<name>/rig_adapter.py`, loaded by name (evals/audio_rig/rig.py), so shared code never
imports a voice layer. The adapter offers `entrant(ctx)` returning an object with:

- `name`, `profile`: the run's stack and model profile labels.
- `in_rate`: the sample rate of caller audio it takes.
- `signals_done`: True when it calls `done()` once the agent is listening again; otherwise the
  rig waits for `QUIET_S` of silence after the agent's last sound.
- `local_world`: True when the calls go to a scratch world the rig builds per scenario (the rig's
  router, evals/audio_rig/router.py); False when the platform calls a tool server of its own.
- `final_record`: True when the call is closed on that tool server by webhook, a moment after the
  line drops, so the checks wait for the record (evals/world.py `final_trace`).
- `async start()` and `async stop()`: once per run.
- `line()`: a fresh line for one call, with `async open(call_id, caller_id, world, listener)`
  returning the ToolServer whose record the checks read, `async send(pcm)` taking 20 ms of caller
  audio, `async close(reason)`, and `async cost()` returning the platform's cost in USD by part.
  A line that buffers caller audio before it goes out also offers `queued_s()`, the seconds
  still buffered, so the rig dates the end of the caller's speech when it actually leaves.

The adapter tells the listener what it hears: `audio(pcm, rate, live)` for agent audio, `done()` when
the platform says the agent is listening again, `hung_up()` when the line drops, and `said(text)`
for the platform's own text of what the agent says, when it reports one.
"""
import asyncio
import time
from dataclasses import dataclass, field

from evals.audio_rig.audio import Playout, Vad, resample

# Timeouts for one agent turn (ASSUMED): a reply that hasn't started this long after the caller
# stops is counted as silence, and a turn is cut off at the cap.
FIRST_SOUND_TIMEOUT_S = 15.0
TURN_CAP_S = 60.0
# Without a "listening again" signal from the platform, the agent is done once this much silence
# has followed its last sound. Longer than the bridge clip gap on a slow lookup (0.7 s, config).
QUIET_S = 2.0
# With the signal, wait this long after playout for a trailing chunk; and if the signal never
# comes, give up on it after this much silence (a lookup without a bridge line can be quiet for
# a few seconds mid-turn).
SETTLE_S = 0.3
QUIET_SIGNALLED_S = 6.0
POLL_S = 0.02


@dataclass
class HeardTurn:
    """One agent turn as the caller heard it."""

    pcm: bytes
    rate: int
    first_sound: float | None
    said: list[str] = field(default_factory=list)
    hung_up: bool = False
    # When the agent made a sound while the caller was still speaking: a reply started in a pause
    # mid-line, or a backchannel on a platform that makes them. None if it stayed quiet.
    over_caller: float | None = None


class Listener:
    def __init__(self, vad: Vad, clock=time.perf_counter, rate: int = 16000, recorder=None):
        self.vad = vad
        # A CallRecorder (evals/audio_rig/recording.py) that keeps the agent's audio as it plays.
        self.recorder = recorder
        self.clock = clock
        self.rate = rate
        self.playout = Playout(rate)
        self._said: list[str] = []
        self._done = False
        self._hung_up = False
        self._last_audio: float | None = None
        self._rate_set = False
        self._onset: float | None = None
        self._after: float | None = None

    # Called by the adapter.
    def audio(self, pcm: bytes, rate: int, live: bool = False) -> None:
        if not self._rate_set:
            self.rate, self.playout.rate, self._rate_set = rate, rate, True
        if rate != self.rate:
            pcm = resample(pcm, rate, self.rate)
        now = self.clock()
        start = self.playout.add(pcm, now, live)
        if self.recorder is not None:
            self.recorder.agent(pcm, self.rate, start)
        self._last_audio = now
        self._done = False

    def done(self) -> None:
        self._done = True

    def hung_up(self) -> None:
        self._hung_up = True

    def said(self, text: str) -> None:
        if text.strip():
            self._said.append(text.strip())

    @property
    def is_hung_up(self) -> bool:
        return self._hung_up

    # Called by the rig.
    def new_turn(self) -> None:
        """Start collecting the agent's next turn. Called when the caller starts speaking, so audio
        the agent sends over the caller still counts toward its reply."""
        self.playout.new_turn()
        self._said = []
        self._done = False
        self._last_audio = None
        self._onset = None
        self._after = None

    def first_sound(self) -> float | None:
        """When the reply's first sound plays. Only sound after the caller stopped counts: a
        platform that says "mm-hmm" while the caller talks (Retell's backchannel) has not started
        its reply."""
        if self._onset is None and self.playout.pcm:
            skip = self.playout.sample_at(self._after) if self._after is not None else 0
            onset = self.vad.onset(bytes(self.playout.pcm[skip * 2:]), self.playout.rate)
            if onset is not None:
                self._onset = self.playout.heard_at(skip + onset)
        return self._onset

    def over_caller(self) -> float | None:
        """When the agent's first sound played while the caller was still speaking (between the
        start of the caller's line and `since`), if it made one."""
        if self._after is None or not self.playout.pcm:
            return None
        end = self.playout.sample_at(self._after)
        onset = self.vad.onset(bytes(self.playout.pcm[: end * 2]), self.playout.rate)
        return None if onset is None else self.playout.heard_at(onset)

    def finished(self, now: float, signals_done: bool) -> bool:
        """Has the agent finished the turn it started? It has to have made a sound first."""
        if self._hung_up:
            return now >= self.playout.end
        if self.first_sound() is None:
            return False
        if signals_done:
            settle = SETTLE_S if self._done else QUIET_SIGNALLED_S
            return now >= self.playout.end + settle
        return now >= self.playout.end + QUIET_S

    async def turn(self, signals_done: bool, since: float) -> HeardTurn:
        """Wait for the agent's whole turn. `since` is when the caller stopped (or the call
        connected); no sound by FIRST_SOUND_TIMEOUT_S after it is a silent turn."""
        self._after = since
        self._onset = None
        while True:
            now = self.clock()
            if self.finished(now, signals_done):
                break
            if self.first_sound() is None and now - since > FIRST_SOUND_TIMEOUT_S:
                break
            if now - since > TURN_CAP_S or (self._hung_up and self.first_sound() is None):
                break
            await asyncio.sleep(POLL_S)
        return HeardTurn(bytes(self.playout.pcm), self.playout.rate, self.first_sound(),
                         list(self._said), self._hung_up, self.over_caller())
