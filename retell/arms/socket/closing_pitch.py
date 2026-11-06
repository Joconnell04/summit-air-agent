"""Whether a closing line falls or rises in Brian's voice, measured on real call recordings.

    PYTHONPATH=. uv run --env-file .env python retell/arms/socket/closing_pitch.py <call_id>... \
        [--out pitch.json]

Jackson heard "Thanks for calling Summit Air." rise like a question and called "Thanks so much for
calling Summit Air today. Have a great day!" "perfect, on the money". Retell's Brian is Retell's own
voice on Cartesia, with no public Cartesia id to render candidates through, so this reads the
agent's channel of calls that already said them. For each closing sentence in a call (found in
Retell's transcript with its word timings), it estimates the pitch every 20 ms over the sentence's
last 400 ms of voiced audio (autocorrelation, 70 to 300 Hz) and fits a line: the slope in
semitones a second. Below FALLING_ST_S the sentence ends falling.
"""

import argparse
import io
import json
import os
import re
import sys
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from voice.retell.api import Retell

TAIL_S = 0.4
FRAME_S = 0.04
HOP_S = 0.02
F0_RANGE = (70.0, 300.0)
# A fall of at least this much (semitones a second) over the tail counts as falling (ASSUMED: a
# statement's final fall is usually several semitones over its last syllables).
FALLING_ST_S = -4.0
CLOSING = re.compile(r"thanks( so much)? for calling summit air|have a (great|good)|enjoy", re.IGNORECASE)


def f0(frame: np.ndarray, rate: int) -> float | None:
    """The frame's fundamental frequency by autocorrelation, or None when it isn't voiced."""
    frame = frame - frame.mean()
    if np.sqrt(np.mean(frame**2)) < 300:
        return None
    corr = np.correlate(frame, frame, mode="full")[len(frame) - 1:]
    lo, hi = int(rate / F0_RANGE[1]), int(rate / F0_RANGE[0])
    lag = lo + int(np.argmax(corr[lo:hi]))
    return rate / lag if corr[lag] > 0.3 * corr[0] else None


def tail_slope(samples: np.ndarray, rate: int, start: float, end: float) -> dict:
    """Pitch over the last TAIL_S of voiced audio between start and end, and its slope."""
    points = []
    t = start
    while t + FRAME_S <= end:
        hz = f0(samples[int(t * rate):int((t + FRAME_S) * rate)].astype(float), rate)
        if hz:
            points.append((t, 12 * np.log2(hz / 100.0)))
        t += HOP_S
    if not points:
        return {"slope_st_s": None, "frames": 0}
    last = points[-1][0]
    tail = [(t, st) for t, st in points if t >= last - TAIL_S]
    if len(tail) < 4:
        return {"slope_st_s": None, "frames": len(tail)}
    slope = float(np.polyfit([t for t, _ in tail], [st for _, st in tail], 1)[0])
    return {"slope_st_s": round(slope, 1), "frames": len(tail), "falls": slope <= FALLING_ST_S}


def agent_channel(url: str) -> tuple[np.ndarray, int]:
    data = urllib.request.urlopen(url, timeout=60).read()
    with wave.open(io.BytesIO(data)) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    raw = raw.reshape(-1, channels)
    # The agent's channel is the louder one during the agent's words; Retell's multichannel files
    # put the caller first.
    return raw[:, 1 if channels > 1 else 0], rate


def sentences(entry: dict) -> list[tuple[str, float, float]]:
    """The entry's sentences with the time of their first and last word."""
    out, words, text = [], entry.get("words") or [], []
    first = None
    for w in words:
        first = w["start"] if first is None else first
        text.append(w["word"].strip())
        if re.search(r"[.!?]$", w["word"].strip()):
            out.append((" ".join(text), first, w["end"]))
            text, first = [], None
    if text:
        out.append((" ".join(text), first, words[-1]["end"]))
    return out


def measure(retell: Retell, call_id: str) -> list[dict]:
    call = retell.request("GET", f"/v2/get-call/{call_id}")
    url = call.get("recording_multi_channel_url")
    if not url:
        return []
    found = []
    for entry in call.get("transcript_object") or []:
        if entry.get("role") != "agent":
            continue
        for text, start, end in sentences(entry):
            if CLOSING.search(text):
                found.append({"call_id": call_id, "sentence": text, "start": start, "end": end})
    if not found:
        return []
    samples, rate = agent_channel(url)
    for item in found:
        item.update(tail_slope(samples, rate, item["start"], item["end"]))
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("calls", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args()
    retell = Retell(os.environ["RETELL_API_KEY"])
    results = [r for c in args.calls for r in measure(retell, c)]
    for r in results:
        print(f"{r['slope_st_s']!s:>6} st/s  {'falls' if r.get('falls') else 'RISES/flat':10}  "
              f"{r['sentence']}  ({r['call_id']})")
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
