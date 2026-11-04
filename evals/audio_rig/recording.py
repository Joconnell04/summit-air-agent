"""Each simulated call as one stereo file and a timed transcript, so a person can listen to it the
way the caller heard it instead of phoning the agent.

Left is the caller, right is the agent, at the rig's own rate with no phone-line filter. Every
clip is placed on the clock the rig times the call with (time.perf_counter), so the pause before
each reply in the file is the `reply_delay_ms` the results file reports for that turn.

A batch is one rig run: a folder under data/sim (gitignored) holding S1.flac, S1.json, S2.flac and
so on, and manifest.json listing them in the order they ran. The short codes never change once
written, so "S3, line 7" means the same thing a week later.
"""
import json
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from evals.audio_rig.audio import resample, samples

ROOT = Path(__file__).resolve().parents[2]
SIM_DIR = ROOT / "data" / "sim"
MANIFEST = "manifest.json"
# The lowest rate a recording is kept at; a lower-rate side is resampled up to the other's.
RECORD_RATE = 24000
# Where `make listen` serves the batches (evals/audio_rig/listen.py).
LISTEN_URL = "http://127.0.0.1:8130"
# Silence kept before the first sound of the call, so the player doesn't start mid-word.
LEAD_S = 0.5
CALLER, AGENT = 0, 1


@dataclass
class Clip:
    channel: int
    start: float
    pcm: np.ndarray


@dataclass
class Line:
    speaker: str
    at: float
    text: str
    reply_delay_ms: float | None = None


@dataclass
class CallRecorder:
    """Collects one call's audio and lines as they happen; `render` lays them out."""

    rate: int = 24000
    clips: list[Clip] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    _agent_end: float | None = None

    def _at_rate(self, pcm: bytes, rate: int) -> np.ndarray:
        return samples(resample(pcm, rate, self.rate)).copy()

    def caller(self, pcm: bytes, rate: int, end: float) -> float:
        """A caller line whose last frame left the rig at `end` (Sender.speak's second value).
        Returns when its first frame went out."""
        x = self._at_rate(pcm, rate)
        start = end - len(x) / self.rate
        if len(x):
            self.clips.append(Clip(CALLER, start, x))
        return start

    def agent(self, pcm: bytes, rate: int, start: float) -> None:
        """A chunk of agent audio that starts playing at `start` (Playout.add's value). A chunk
        that would overlap the one before is played after it, as a loudspeaker would."""
        x = self._at_rate(pcm, rate)
        if not len(x):
            return
        if self._agent_end is not None:
            start = max(start, self._agent_end)
        self.clips.append(Clip(AGENT, start, x))
        self._agent_end = start + len(x) / self.rate

    def line(self, speaker: str, text: str, at: float | None,
             reply_delay_ms: float | None = None) -> None:
        if text and at is not None:
            self.lines.append(Line(speaker, at, text, reply_delay_ms))

    def origin(self) -> float | None:
        starts = [c.start for c in self.clips] + [ln.at for ln in self.lines]
        return min(starts) - LEAD_S if starts else None

    def render(self) -> tuple[np.ndarray, list[dict]]:
        """Interleaved stereo int16 samples, and the lines with seconds from the file's start."""
        origin = self.origin()
        if origin is None:
            return np.zeros(0, dtype="<i2"), []
        length = max((round((c.start - origin) * self.rate) + len(c.pcm) for c in self.clips),
                     default=0)
        mix = np.zeros((length, 2), dtype=np.int32)
        for c in self.clips:
            i = round((c.start - origin) * self.rate)
            mix[i : i + len(c.pcm), c.channel] += c.pcm
        stereo = np.clip(mix, -32768, 32767).astype("<i2").reshape(-1)
        lines = []
        for n, ln in enumerate(sorted(self.lines, key=lambda x: x.at), start=1):
            entry = {"n": n, "speaker": ln.speaker, "t": round(ln.at - origin, 2), "text": ln.text}
            if ln.reply_delay_ms is not None:
                entry["reply_delay_ms"] = round(ln.reply_delay_ms)
            lines.append(entry)
        return stereo, lines

    def write_wav(self, path: Path) -> float:
        """Write the stereo file; returns its length in seconds."""
        stereo, _ = self.render()
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(self.rate)
            out.writeframes(stereo.tobytes())
        return len(stereo) / 2 / self.rate


def to_flac(wav: Path) -> Path:
    """FLAC is lossless and about a seventh the size of WAV for a call, where each side is silent
    while the other talks (MEASURED, evals/README.md); kept as WAV when `flac` isn't installed."""
    if shutil.which("flac") is None:
        return wav
    flac = wav.with_suffix(".flac")
    done = subprocess.run(["flac", "--silent", "--best", "--force", "-o", str(flac), str(wav)],
                          capture_output=True, check=False)
    if done.returncode != 0:
        return wav
    wav.unlink()
    return flac


def batch_id(stack: str, now: datetime | None = None) -> str:
    return f"{(now or datetime.now(UTC)):%Y%m%d-%H%M%S}-{stack}"


class Batch:
    """One rig run's recordings. `add` writes a call as soon as it ends, so a stopped run keeps
    the calls it finished; `store` (evals/audio_rig/store.py) copies each file somewhere durable."""

    def __init__(self, folder: Path, stack: str, store=None, flac: bool = True):
        self.folder = folder
        self.store = store
        self.flac = flac
        if (folder / MANIFEST).is_file():
            # Adding to a batch: its calls keep their codes and new ones follow on.
            self.manifest = json.loads((folder / MANIFEST).read_text())
        else:
            self.manifest = {"batch": folder.name, "stack": stack,
                             "created": datetime.now(UTC).isoformat(timespec="seconds"),
                             "results": None, "calls": []}
        folder.mkdir(parents=True, exist_ok=True)
        self._save()

    def next_code(self) -> str:
        return f"S{len(self.manifest['calls']) + 1}"

    def add(self, recorder: CallRecorder, result, persona: str = "") -> dict:
        code = self.next_code()
        audio = self.folder / f"{code}.wav"
        duration = recorder.write_wav(audio)
        if self.flac:
            audio = to_flac(audio)
        _, lines = recorder.render()
        transcript = {
            "code": code, "scenario_id": result.scenario_id, "sample": result.sample,
            "call_id": result.call_id, "persona": persona, "outcome": result.outcome,
            "audio": audio.name, "duration_s": round(duration, 2), "rate": recorder.rate,
            "lines": lines,
        }
        (self.folder / f"{code}.json").write_text(json.dumps(transcript, indent=2) + "\n")
        entry = {"code": code, "scenario_id": result.scenario_id, "sample": result.sample,
                 "audio": audio.name, "transcript": f"{code}.json",
                 "duration_s": transcript["duration_s"]}
        self.manifest["calls"].append(entry)
        self._save()
        if self.store is not None:
            # The manifest goes with every call, so a run stopped partway is listed in full.
            for path in (audio, self.folder / f"{code}.json", self.folder / MANIFEST):
                self.store.put(path, f"{self.folder.name}/{path.name}")
        return entry

    def finish(self, results_path: Path | None) -> None:
        if results_path is not None:
            path = results_path.resolve()
            name = str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)
            # A batch added to more than once has a results file per run.
            earlier = self.manifest.get("results") or []
            earlier = [earlier] if isinstance(earlier, str) else earlier
            self.manifest["results"] = [*earlier, name]
        self._save()
        if self.store is not None:
            self.store.put(self.folder / MANIFEST, f"{self.folder.name}/{MANIFEST}")

    def _save(self) -> None:
        (self.folder / MANIFEST).write_text(json.dumps(self.manifest, indent=2) + "\n")
