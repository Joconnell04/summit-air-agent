"""Audio arithmetic for the rig, with no network: loudness, the voice-activity threshold, trimming
the synthesized caller's silence, resampling, and the playout clock that turns "when a chunk of
agent audio arrived" into "when the caller would hear its first sound".

Audio is 16-bit signed little-endian mono PCM throughout, as every entrant takes and gives it.
"""
import math
from dataclasses import dataclass, field

import numpy as np

FRAME_MS = 20
SAMPLE_BYTES = 2


def samples(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) - len(pcm) % SAMPLE_BYTES], dtype="<i2")


def frame_bytes(rate: int, ms: int = FRAME_MS) -> int:
    return rate * ms // 1000 * SAMPLE_BYTES


def frames(pcm: bytes, rate: int, ms: int = FRAME_MS) -> list[bytes]:
    """Fixed-size frames; the last one is padded with silence."""
    size = frame_bytes(rate, ms)
    out = [pcm[i : i + size] for i in range(0, len(pcm), size)]
    if out and len(out[-1]) < size:
        out[-1] = out[-1] + b"\0" * (size - len(out[-1]))
    return out


def dbfs(pcm: bytes) -> float:
    """Loudness of a frame against full scale; digital silence is -inf."""
    x = samples(pcm).astype(np.float64)
    if not len(x):
        return -math.inf
    rms = math.sqrt(float(np.mean(x * x)))
    return 20 * math.log10(rms / 32768) if rms > 0 else -math.inf


@dataclass(frozen=True)
class Vad:
    """A frame is voiced at or above `threshold_dbfs`; speech starts at the first run of voiced
    frames lasting `min_voiced_ms`, so a click or a codec blip is not counted as the agent
    talking."""

    threshold_dbfs: float = -40.0
    min_voiced_ms: int = 40

    def voiced(self, frame: bytes) -> bool:
        return dbfs(frame) >= self.threshold_dbfs

    def onset(self, pcm: bytes, rate: int) -> int | None:
        """Sample index where speech starts, or None."""
        need = max(1, math.ceil(self.min_voiced_ms / FRAME_MS))
        size = frame_bytes(rate) // SAMPLE_BYTES
        run = 0
        for i, frame in enumerate(frames(pcm, rate)):
            run = run + 1 if self.voiced(frame) else 0
            if run == need:
                return (i - need + 1) * size
        return None

    def trim(self, pcm: bytes, rate: int) -> bytes:
        """The voiced part of a clip, from its first voiced frame to its last, so the rig knows
        exactly when the caller's speech ends."""
        voiced = [i for i, f in enumerate(frames(pcm, rate)) if self.voiced(f)]
        if not voiced:
            return b""
        size = frame_bytes(rate)
        return pcm[voiced[0] * size : (voiced[-1] + 1) * size]


def resample(pcm: bytes, rate_from: int, rate_to: int) -> bytes:
    """Linear resampling; good enough for speech going into a recognizer."""
    if rate_from == rate_to or not pcm:
        return pcm
    x = samples(pcm).astype(np.float64)
    n = round(len(x) * rate_to / rate_from)
    if n == 0:
        return b""
    t = np.linspace(0, len(x) - 1, n)
    return np.interp(t, np.arange(len(x)), x).round().astype("<i2").tobytes()


@dataclass
class Playout:
    """The caller's loudspeaker. A platform may send a second of audio in one burst; the caller
    hears it at real speed from when it arrived, after anything still playing. `heard_at` gives
    the time a given sample of the turn plays."""

    rate: int
    end: float = 0.0
    # (first sample index of the chunk in this turn, time the chunk starts playing)
    chunks: list[tuple[int, float]] = field(default_factory=list)
    pcm: bytearray = field(default_factory=bytearray)

    def add(self, pcm: bytes, arrived: float, live: bool = False) -> None:
        """`live` audio comes from a stream that already plays at real speed (a LiveKit track),
        so it plays when it arrives; such a stream can also carry two tracks at once."""
        start = arrived if live else max(arrived, self.end)
        self.chunks.append((len(self.pcm) // SAMPLE_BYTES, start))
        self.pcm.extend(pcm)
        self.end = max(self.end, start + len(pcm) / SAMPLE_BYTES / self.rate)

    def heard_at(self, sample: int) -> float:
        offset, start = self.chunks[0]
        for first, t in self.chunks:
            if first > sample:
                break
            offset, start = first, t
        return start + (sample - offset) / self.rate

    def sample_at(self, t: float) -> int:
        """Index of the first sample that plays at or after time t (the end if none does)."""
        total = len(self.pcm) // SAMPLE_BYTES
        for i, (first, start) in enumerate(self.chunks):
            last = self.chunks[i + 1][0] if i + 1 < len(self.chunks) else total
            if start + (last - first) / self.rate > t:
                return first + max(0, round((t - start) * self.rate))
        return total

    def new_turn(self) -> None:
        """Keep the clock, drop the audio: anything still playing delays the next turn too."""
        self.chunks = []
        self.pcm = bytearray()


def reply_delay_ms(caller_end: float, first_sound: float) -> float:
    """From the end of the caller's speech to the agent's first sound. An agent that talks over the
    end of the caller is counted as zero, not as negative time."""
    return max(0.0, (first_sound - caller_end) * 1000)
