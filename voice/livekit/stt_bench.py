"""The STT bench: the same scripted caller turns, at phone quality, streamed in real time through
our ears (config/models.yaml `voice.stt`) and one candidate from `voice.stt_candidates`, side by
side, through the same LiveKit plugins and keyterms the worker builds.

The full rig (`make rig`) runs a whole call, so the caller's words change with the agent's replies
and an STT's misses are mixed with the model's. Here every arm hears the same audio: each turn is
rendered once per caller voice by the rig's own voice (Deepgram Aura), at 8 kHz, through a G.711
mu-law round trip with a quiet line-noise floor, and both arms get the same frame at the same
moment. Nothing else of the stack runs: no VAD, no turn detector, no model.

Per turn it records every transcript event with its time from the end of the caller's speech, and
scores the key spans (an address, a phone number, a spelled name, a safety phrase) by exact match
after the normalization in `normalize`. `report` turns a results file into the tables of
evals/rounds/stt-ink2-01.md.

    make stt-bench [STT=ink-2] [REPS=2]
    uv run python -m voice.livekit.stt_bench --report evals/rounds/stt-ink2-01-data/<file>.json
"""

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from evals.audio_rig.audio import FRAME_MS, Vad, frames, samples
from evals.audio_rig.speech import Deepgram

ROOT = Path(__file__).resolve().parents[2]
RATE = 8000
LEAD_S = 0.5
# Ink-2 ends a turn at the latest 5.6 s after the caller stops (its default turn_end_timeout_ms,
# SOURCE: the LiveKit Cartesia plugin's STT docstring), so 6 s of silence shows every turn end.
TRAIL_S = 6.0
# After the input closes, how long to wait for a stream's last events.
DRAIN_S = 5.0
CACHE = ROOT / "data" / "stt_bench"
OUT = ROOT / "evals" / "rounds" / "stt-ink2-01-data"


@dataclass(frozen=True)
class Span:
    """A part of the turn that must come out exactly. `kind` is "text" (any of `accept` appears in
    the normalized transcript) or "letters" (a spelled-out run of letters equals `accept[0]`)."""

    label: str
    kind: str
    accept: tuple[str, ...]


@dataclass(frozen=True)
class Turn:
    id: str
    group: str
    # Spoken text, with a number between two pieces for a pause in seconds.
    parts: tuple
    spans: tuple[Span, ...]
    # For a safety turn, the kind the tool server's phrase screen (app/safety.py) must find in the
    # transcript: an exact span is not enough if the server reads it differently.
    screen: str | None = None


def text(label: str, *accept: str) -> Span:
    return Span(label, "text", accept)


def letters(label: str, spelled: str) -> Span:
    return Span(label, "letters", (spelled,))


TURNS = (
    Turn("street-duffield", "street", ("Yeah, it's 248 Duffield Street, in Downtown Brooklyn.",),
         (text("248 Duffield St", "248 duffield street", "248 duffield st"),)),
    Turn("street-ocean", "street", ("We're at 1150 Ocean Parkway, near Avenue J.",),
         (text("1150 Ocean Parkway", "1150 ocean parkway", "1150 ocean pkwy"),)),
    Turn("street-church", "street", ("The house is on Church Avenue, right by Flatbush.",),
         (text("Church Ave", "church avenue", "church ave"),)),
    Turn("street-houston", "street", ("I'm on Houston Street, between Lafayette and Mulberry.",),
         (text("Houston Street", "houston street", "houston st"),)),
    Turn("street-kosciuszko", "street", ("It's 312 Kosciuszko Street, in Bed-Stuy.",),
         (text("312 Kosciuszko St", "312 kosciuszko street", "312 kosciuszko st"),)),
    Turn("street-schermerhorn", "street", ("88 Schermerhorn Street, apartment 3.",),
         (text("88 Schermerhorn St", "88 schermerhorn street", "88 schermerhorn st"),)),
    Turn("street-cortelyou", "street", ("We live on Cortelyou Road, off Coney Island Avenue.",),
         (text("Cortelyou Road", "cortelyou road", "cortelyou rd"),)),
    Turn("street-joralemon", "street", ("It's 41 Joralemon Street.",),
         (text("41 Joralemon St", "41 joralemon street", "41 joralemon st"),)),
    Turn("digits-phone", "digits",
         ("My number is seven one eight,", 0.8, "five five five,", 0.8, "zero one four two."),
         (text("718 555 0142", "7185550142"),)),
    Turn("digits-phone-slow", "digits",
         ("Call me back at nine one seven,", 1.5, "four four two,", 1.5, "eight eight one nine."),
         (text("917 442 8819", "9174428819"),)),
    Turn("digits-zip-pause", "digits", ("The zip code is one one two,", 0.7, "two six."),
         (text("ZIP 11226", "11226"),)),
    Turn("digits-zip", "digits", ("It's one one two zero one.",),
         (text("ZIP 11201", "11201"),)),
    Turn("spelled-nguyen", "spelled", ("The last name is Nguyen. N, G, U, Y, E, N.",),
         (letters("N-G-U-Y-E-N", "nguyen"),)),
    Turn("spelled-siobhan", "spelled", ("First name Siobhan. S, I, O, B, H, A, N.",),
         (letters("S-I-O-B-H-A-N", "siobhan"),)),
    Turn("safety-gas", "safety", ("I smell gas in the kitchen.",),
         (text("smell gas", "smell gas"),), "gas"),
    Turn("safety-co", "safety", ("The C O alarm is going off and it won't stop.",),
         (text("CO alarm", "co alarm", "c o alarm"),), "co"),
    Turn("safety-gas-in-passing", "safety",
         ("No, it's not the heat. I think I smell gas by the stove.",),
         (text("smell gas", "smell gas"),), "gas"),
    Turn("safety-carbon-monoxide", "safety", ("My carbon monoxide detector keeps beeping.",),
         (text("carbon monoxide", "carbon monoxide"),), "co"),
)

# Street names and digits run twice per voice; the rest once.
REPEATED = {"street", "digits"}

UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}


def normalize(transcript: str) -> str:
    """Lowercase words with punctuation gone and numbers as digits, the way an address or a phone
    number is read back: number words become digits ("forty eight" is 48), and numbers next to
    each other join ("two forty eight" and "2 48" are 248, "718-555-0142" is 7185550142)."""
    words = re.sub(r"[^a-z0-9' ]", " ", transcript.lower().replace("-", " ")).replace("'", "").split()
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in TENS:
            value = TENS[w]
            if i + 1 < len(words) and words[i + 1] in UNITS and UNITS[words[i + 1]]:
                value += UNITS[words[i + 1]]
                i += 1
            out.append(str(value))
        elif w in TEENS:
            out.append(str(TEENS[w]))
        elif w in UNITS and w != "oh":
            out.append(str(UNITS[w]))
        elif w == "oh" and (out and out[-1].isdigit()):
            out.append("0")
        else:
            out.append(w)
        i += 1
    joined: list[str] = []
    for w in out:
        if w.isdigit() and joined and joined[-1].isdigit():
            joined[-1] += w
        else:
            joined.append(w)
    return " ".join(joined)


def spelled_runs(transcript: str) -> list[str]:
    """Runs of letters said one at a time ("N, G, U", "N-G-U", or written together as "NGUYEN"),
    lowercased and joined."""
    runs, current = [], []
    for token in re.split(r"[\s,.\-]+", transcript):
        if len(token) == 1 and token.isalpha():
            current.append(token.lower())
            continue
        if len(current) >= 2:
            runs.append("".join(current))
        current = []
        if len(token) >= 3 and token.isalpha() and token.isupper():
            runs.append(token.lower())
    if len(current) >= 2:
        runs.append("".join(current))
    return runs


def span_hit(span: Span, transcript: str) -> bool:
    if span.kind == "letters":
        return span.accept[0] in spelled_runs(transcript)
    heard = f" {normalize(transcript)} "
    return any(f" {normalize(a)} " in heard for a in span.accept)


def mulaw_round_trip(pcm: bytes) -> bytes:
    """G.711 mu-law encode and decode, the companding a phone line puts on every sample."""
    x = samples(pcm).astype(np.float64) / 32768.0
    mu = 255.0
    y = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    y = np.round((y + 1) / 2 * 255) / 255 * 2 - 1
    back = np.sign(y) * np.expm1(np.abs(y) * np.log1p(mu)) / mu
    return np.clip(back * 32768.0, -32768, 32767).astype("<i2").tobytes()


def line_noise(n_samples: int, dbfs: float, seed: int) -> np.ndarray:
    rms = 32768.0 * 10 ** (dbfs / 20)
    return np.random.default_rng(seed).normal(0.0, rms, n_samples)


@dataclass
class Clip:
    """One turn in one voice, ready to stream: lead silence, speech with its pauses, trailing
    silence. Times are seconds from the first frame."""

    pcm: bytes
    speech_start_s: float
    speech_end_s: float
    # (start, end) of each pause inside the speech.
    pauses: list[tuple[float, float]] = field(default_factory=list)


def silence(seconds: float) -> bytes:
    return b"\0" * (round(seconds * RATE) * 2)


def assemble(pieces: list[bytes | float], noise_dbfs: float, seed: int) -> Clip:
    """Lays trimmed speech pieces and pauses (seconds) end to end between the lead and trailing
    silence, adds the line noise, and puts it all through the mu-law round trip."""
    out = bytearray(silence(LEAD_S))
    pauses = []
    for piece in pieces:
        now = len(out) / 2 / RATE
        if isinstance(piece, bytes):
            out += piece
        else:
            out += silence(piece)
            pauses.append((now, now + piece))
    speech_end = len(out) / 2 / RATE
    out += silence(TRAIL_S)
    pcm = bytes(out)
    pcm = pcm[: len(pcm) - len(pcm) % (RATE * FRAME_MS // 1000 * 2)]
    mixed = samples(pcm).astype(np.float64) + line_noise(len(pcm) // 2, noise_dbfs, seed)
    pcm = np.clip(mixed, -32768, 32767).astype("<i2").tobytes()
    return Clip(mulaw_round_trip(pcm), LEAD_S, speech_end, pauses)


async def render(turn: Turn, voice: str, speech: Deepgram, vad: Vad) -> list[bytes | float]:
    """The turn's spoken pieces in one voice, trimmed to their voiced part, cached on disk so every
    run of the bench hears the same audio."""
    pieces: list[bytes | float] = []
    for part in turn.parts:
        if not isinstance(part, str):
            pieces.append(float(part))
            continue
        key = hashlib.sha1(f"{voice}|{RATE}|{part}".encode()).hexdigest()[:16]
        path = CACHE / voice / f"{key}.pcm"
        if not path.exists():
            speech.voice = {**speech.voice, "model": voice}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(vad.trim(await speech.say(part, RATE), RATE))
        pieces.append(path.read_bytes())
    return pieces


@dataclass
class Event:
    t: float  # seconds from the end of the caller's speech
    type: str
    text: str = ""


async def collect(stream, clock, origin: float, events: list[Event]) -> None:
    async for ev in stream:
        text = ev.alternatives[0].text if getattr(ev, "alternatives", None) else ""
        events.append(Event(round(clock() - origin, 3), ev.type.name, text))


async def stream_clip(arms: dict, clip: Clip, clock=time.perf_counter, sleep=asyncio.sleep) -> dict:
    """Plays one clip into every arm's stream at real speed, 20 ms a frame, and returns each arm's
    events, timed from the end of the caller's speech."""
    from livekit import rtc

    streams = {name: stt.stream() for name, stt in arms.items()}
    events: dict[str, list[Event]] = {name: [] for name in arms}
    start = clock()
    origin = start + clip.speech_end_s
    tasks = [asyncio.create_task(collect(s, clock, origin, events[n])) for n, s in streams.items()]
    per = RATE * FRAME_MS // 1000
    for i, frame in enumerate(frames(clip.pcm, RATE)):
        wait = start + i * FRAME_MS / 1000 - clock()
        if wait > 0:
            await sleep(wait)
        audio = rtc.AudioFrame(frame, RATE, 1, per)
        for s in streams.values():
            s.push_frame(audio)
    closed = round(clock() - origin, 3)
    for s in streams.values():
        s.end_input()
    await asyncio.wait(tasks, timeout=DRAIN_S)
    for s, task in zip(streams.values(), tasks):
        if not task.done():
            task.cancel()
        await s.aclose()
    return {name: {"events": [asdict(e) for e in evs], "input_closed_s": closed}
            for name, evs in events.items()}


def score(turn: Turn, clip: Clip, run: dict) -> dict:
    """What one arm made of one clip: the transcript, the span hits, and the timing.

    final_s is the last final transcript, from the end of the caller's speech. turn_end_s is the
    last end-of-speech event, which for a turn-detecting STT is its end of turn. early_ends lists
    the end-of-speech events before the caller's last word, the turn ends that would have cut the
    caller off, each with how long the caller stayed silent after it (0 when they were already
    talking again)."""
    events = [Event(**e) for e in run["events"]]
    finals = [e for e in events if e.type == "FINAL_TRANSCRIPT"]
    transcript = " ".join(e.text.strip() for e in finals if e.text.strip())
    ends = [e for e in events if e.type == "END_OF_SPEECH"]
    early = []
    for e in (e for e in ends if e.t < 0):
        resume = next((p_end for p_start, p_end in clip.pauses
                       if p_start - clip.speech_end_s <= e.t < p_end - clip.speech_end_s), None)
        early.append({"t": e.t, "silent_after_s":
                      round(resume - clip.speech_end_s - e.t, 3) if resume else 0.0})
    preflight = [e.t for e in events if e.type == "PREFLIGHT_TRANSCRIPT"]
    late_finals = [e.t for e in finals if e.t >= 0]
    return {
        "transcript": transcript,
        "spans": {s.label: span_hit(s, transcript) for s in turn.spans},
        "final_s": late_finals[-1] if late_finals else None,
        "final_after_close": bool(late_finals) and late_finals[-1] > run["input_closed_s"],
        "turn_end_s": ends[-1].t if ends and ends[-1].t >= 0 else None,
        "preflight_s": preflight[-1] if preflight and preflight[-1] >= 0 else None,
        "finals_before_end": sum(1 for e in finals if e.t < 0),
        "early_ends": early,
    }


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def plan(reps: int, only: set[str] | None = None) -> list[tuple[Turn, int]]:
    return [(t, r) for t in TURNS if not only or t.id in only
            for r in range(reps if t.group in REPEATED else 1)]


async def bench(candidate: str, reps: int, only: set[str] | None = None) -> Path:
    import aiohttp

    from voice import names as local_names

    from .worker import build_stt

    config = load_config()
    voice = config["voice"]
    bench_config = config["audio_rig"]["stt_bench"]
    names = local_names.load(ROOT / "tenants" / "summit-air")
    arm_configs = {voice["stt"]["model"]: voice["stt"],
                   candidate: voice["stt_candidates"][candidate]}
    vad = Vad(**config["audio_rig"]["vad"])
    speech = Deepgram(config["audio_rig"])
    results = {"started": datetime.now().isoformat(timespec="seconds"), "rate": RATE,
               "lead_s": LEAD_S, "trail_s": TRAIL_S, "noise_dbfs": bench_config["noise_dbfs"],
               "keyterms": names.keyterms(), "arms": arm_configs, "runs": []}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{stamp}-{candidate}.json"
    async with aiohttp.ClientSession() as session:
        arms = {name: build_stt({**voice, "stt": cfg}, names, http_session=session)
                for name, cfg in arm_configs.items()}
        todo = [(t, v, r) for v in bench_config["voices"] for t, r in plan(reps, only)]
        for n, (turn, voice_id, rep) in enumerate(todo, 1):
            pieces = await render(turn, voice_id, speech, vad)
            seed = int(hashlib.sha1(f"{turn.id}|{voice_id}".encode()).hexdigest()[:8], 16)
            clip = assemble(pieces, bench_config["noise_dbfs"], seed)
            runs = await stream_clip(arms, clip)
            for arm, run in runs.items():
                results["runs"].append({
                    "arm": arm, "turn": turn.id, "group": turn.group, "voice": voice_id,
                    "rep": rep, "seconds": round(len(clip.pcm) / 2 / RATE, 2),
                    "speech_end_s": clip.speech_end_s, "pauses": clip.pauses, **run,
                    **score(turn, clip, run)})
            print(f"[{n}/{len(todo)}] {turn.id} {voice_id} rep {rep}: " + "; ".join(
                f"{arm} {results['runs'][-len(runs) + i]['transcript']!r}"
                for i, arm in enumerate(runs)), flush=True)
            results["caller_voice_chars"] = speech.chars
            path.write_text(json.dumps(results, indent=1))
        for stt in arms.values():
            await stt.aclose()
    await speech.aclose()
    return path


def rescore(path: Path) -> None:
    """Scores a results file again from its stored events, after a change to `score`. Runs from
    before the pauses were stored get them from the cached audio (no network)."""
    config = load_config()
    results = json.loads(path.read_text())
    turns = {t.id: t for t in TURNS}
    vad = Vad(**config["audio_rig"]["vad"])
    speech = Deepgram(config["audio_rig"], client=object())
    for r in results["runs"]:
        turn = turns[r["turn"]]
        if "pauses" not in r:
            pieces = asyncio.run(render(turn, r["voice"], speech, vad))
            r["pauses"] = assemble(pieces, results["noise_dbfs"], 0).pauses
        clip = Clip(b"", results["lead_s"], r["speech_end_s"], [tuple(p) for p in r["pauses"]])
        r.update(score(turn, clip, r))
    path.write_text(json.dumps(results, indent=1))


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 3)


def safety_screen():
    """The tool server's safety phrase screen for the tenant, as a function of a transcript."""
    from app import safety
    from app.tenant import load_agent

    cfg = load_agent(ROOT / "tenants" / "summit-air")[0]["safety"]
    return lambda transcript: safety.clear_signal(transcript, cfg)


def summarize(results: dict, config: dict | None = None, screen=None) -> dict:
    """Per arm: span hits by group, the server's safety screen on the safety turns, timing,
    cut-offs and cost, from a results file."""
    config = config or load_config()
    screen = screen or safety_screen()
    expected = {t.id: t.screen for t in TURNS if t.screen}
    caller_price = config["audio_rig"]["caller_voice"]["price_per_1k_chars"]
    out = {"arms": {}, "caller_voice_usd": round(results.get("caller_voice_chars", 0) / 1000 *
                                                 caller_price, 4)}
    for arm, cfg in results["arms"].items():
        runs = [r for r in results["runs"] if r["arm"] == arm]
        groups = {}
        for r in runs:
            g = groups.setdefault(r["group"], {"hits": 0, "n": 0})
            g["hits"] += sum(r["spans"].values())
            g["n"] += len(r["spans"])
        finals = [r["final_s"] for r in runs if r["final_s"] is not None]
        ends = [r["turn_end_s"] for r in runs if r["turn_end_s"] is not None]
        digits = [r for r in runs if r["group"] == "digits"]
        seconds = sum(r["seconds"] for r in runs)
        screened = [r for r in runs if r["turn"] in expected]
        out["arms"][arm] = {
            # A turn-detecting STT ends the turn itself, so its early ends are cut-offs; for the
            # others, LiveKit's turn detector decides and their end-of-speech events are advice.
            "decides_turn": cfg["provider"] == "cartesia",
            "groups": groups,
            "screen_hits": sum(1 for r in screened if screen(r["transcript"]) == expected[r["turn"]]),
            "screen_n": len(screened),
            "n": len(runs),
            "final_median_s": median(finals), "final_p90_s": p90(finals),
            "final_max_s": max(finals) if finals else None, "no_final": len(runs) - len(finals),
            "final_after_close": sum(1 for r in runs if r["final_after_close"]),
            "turn_end_median_s": median(ends), "turn_end_p90_s": p90(ends),
            "preflight_median_s": median([r["preflight_s"] for r in runs
                                          if r["preflight_s"] is not None]),
            "early_ends": sum(len(r["early_ends"]) for r in runs),
            "early_end_runs": sum(1 for r in runs if r["early_ends"]),
            "digits_early_end_runs": sum(1 for r in digits if r["early_ends"]),
            "digits_n": len(digits),
            "finals_before_end": sum(r["finals_before_end"] for r in runs),
            "minutes": round(seconds / 60, 2),
            "usd": round(seconds / 60 * cfg["price_per_minute"], 4),
            "price_per_minute": cfg["price_per_minute"],
        }
    return out


def report(path: Path, screen=None) -> str:
    results = json.loads(path.read_text())
    summary = summarize(results, screen=screen)
    arms = list(summary["arms"])
    lines = [f"Results: {path.name}", "", "Key spans, exact match (hits / spans)", ""]
    lines.append("| group | " + " | ".join(arms) + " |")
    lines.append("|---|" + "---|" * len(arms))
    for group in ("street", "digits", "spelled", "safety"):
        cells = []
        for arm in arms:
            g = summary["arms"][arm]["groups"].get(group, {"hits": 0, "n": 0})
            cells.append(f"{g['hits']}/{g['n']}")
        lines.append(f"| {group} | " + " | ".join(cells) + " |")
    lines.append("| safety, server screen | " + " | ".join(
        f"{summary['arms'][a]['screen_hits']}/{summary['arms'][a]['screen_n']}" for a in arms) + " |")
    lines += ["", "Timing and cut-offs (seconds from the caller's last word)", ""]
    keys = ["decides_turn", "n", "final_median_s", "final_p90_s", "final_max_s", "no_final", "final_after_close",
            "turn_end_median_s", "turn_end_p90_s", "preflight_median_s", "early_ends",
            "early_end_runs", "digits_early_end_runs", "digits_n", "finals_before_end",
            "minutes", "usd"]
    lines.append("| measure | " + " | ".join(arms) + " |")
    lines.append("|---|" + "---|" * len(arms))
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(str(summary["arms"][a][key]) for a in arms) + " |")
    lines += ["", f"Caller voice: ${summary['caller_voice_usd']}", "", "Misses:"]
    screen = screen or safety_screen()
    expected = {t.id: t.screen for t in TURNS if t.screen}
    for r in results["runs"]:
        missed = [label for label, hit in r["spans"].items() if not hit]
        if r["turn"] in expected and screen(r["transcript"]) != expected[r["turn"]]:
            missed.append("server safety screen")
        if missed:
            lines.append(f"- {r['arm']} {r['turn']} {r['voice']} rep {r['rep']}: "
                         f"{', '.join(missed)} in {r['transcript']!r}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m voice.livekit.stt_bench")
    parser.add_argument("--stt", default="ink-2", help="a name under voice.stt_candidates")
    parser.add_argument("--reps", type=int, default=2, help="runs per voice of street and digits turns")
    parser.add_argument("--only", default="", help="turn ids, comma separated (a smoke run)")
    parser.add_argument("--report", type=Path, help="print the tables for a results file")
    parser.add_argument("--rescore", action="store_true", help="with --report: score it again first")
    args = parser.parse_args(argv)
    if args.report:
        if args.rescore:
            rescore(args.report)
        print(report(args.report))
        return 0
    only = {t for t in args.only.split(",") if t} or None
    path = asyncio.run(bench(args.stt, args.reps, only))
    print(report(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
