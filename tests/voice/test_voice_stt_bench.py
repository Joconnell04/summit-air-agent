"""The STT bench (voice/livekit/stt_bench.py): scoring, audio assembly and timing, offline."""

import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest

from evals.audio_rig.audio import samples
from voice.livekit import stt_bench as bench


@pytest.mark.parametrize("heard, expected", [
    ("Yeah, it's 248 Duffield Street.", "yeah its 248 duffield street"),
    ("two forty-eight Duffield St", "248 duffield st"),
    ("My number is seven one eight five five five zero one four two.", "my number is 7185550142"),
    ("My number is 718-555-0142.", "my number is 7185550142"),
    ("eleven fifty Ocean Parkway", "1150 ocean parkway"),
    ("one one two oh one", "11201"),
    ("Oh, I smell gas", "oh i smell gas"),
])
def test_normalize_reads_numbers_the_way_they_are_read_back(heard, expected):
    assert bench.normalize(heard) == expected


def test_a_text_span_needs_the_whole_phrase():
    span = bench.text("248 Duffield St", "248 duffield street", "248 duffield st")
    assert bench.span_hit(span, "It's two forty eight Duffield Street, in Brooklyn.")
    assert not bench.span_hit(span, "It's 248 Duffy Street.")
    assert not bench.span_hit(span, "It's 1248 Duffield Street.")


@pytest.mark.parametrize("heard", [
    "The last name is Nguyen. N, G, U, Y, E, N.",
    "Nguyen, N-G-U-Y-E-N.",
    "the last name is Nguyen, NGUYEN.",
])
def test_a_spelled_name_counts_only_as_letters(heard):
    assert bench.span_hit(bench.letters("N-G-U-Y-E-N", "nguyen"), heard)


def test_the_name_said_whole_is_not_the_spelling():
    assert not bench.span_hit(bench.letters("N-G-U-Y-E-N", "nguyen"), "The last name is Nguyen.")
    assert not bench.span_hit(bench.letters("N-G-U-Y-E-N", "nguyen"), "Nguyen. N G U Y N.")


def test_the_mulaw_round_trip_keeps_speech_close():
    tone = (np.sin(np.arange(800) / 5) * 8000).astype("<i2").tobytes()
    back = samples(bench.mulaw_round_trip(tone)).astype(float)
    err = np.abs(back - samples(tone).astype(float))
    assert err.max() < 8000 * 0.05 and err.max() > 0


def test_assemble_times_the_pauses_and_the_end_of_speech():
    word = b"\x10\x00" * bench.RATE  # one second
    clip = bench.assemble([word, 0.8, word], noise_dbfs=-60, seed=1)
    assert clip.speech_start_s == bench.LEAD_S
    assert clip.pauses == [(bench.LEAD_S + 1.0, bench.LEAD_S + 1.8)]
    assert clip.speech_end_s == pytest.approx(bench.LEAD_S + 2.8)
    assert len(clip.pcm) / 2 / bench.RATE == pytest.approx(bench.LEAD_S + 2.8 + bench.TRAIL_S)
    # Same seed, same audio: every arm and every run hears the same clip.
    assert clip.pcm == bench.assemble([word, 0.8, word], noise_dbfs=-60, seed=1).pcm


def phone_turn():
    return next(t for t in bench.TURNS if t.id == "digits-phone")


def test_score_takes_the_last_final_and_counts_a_turn_end_inside_a_pause_as_a_cut_off():
    clip = bench.Clip(b"", 0.5, 4.0, pauses=[(1.5, 2.3), (2.8, 3.6)])
    run = {"input_closed_s": 6.0, "events": [
        {"t": -2.3, "type": "FINAL_TRANSCRIPT", "text": "My number is 718"},
        {"t": -2.2, "type": "END_OF_SPEECH", "text": ""},
        {"t": 0.6, "type": "PREFLIGHT_TRANSCRIPT", "text": "555-0142."},
        {"t": 1.1, "type": "FINAL_TRANSCRIPT", "text": "555-0142."},
        {"t": 1.1, "type": "END_OF_SPEECH", "text": ""},
    ]}
    scored = bench.score(phone_turn(), clip, run)
    assert scored["transcript"] == "My number is 718 555-0142."
    assert scored["spans"] == {"718 555 0142": True}
    assert scored["final_s"] == 1.1 and scored["turn_end_s"] == 1.1 and scored["preflight_s"] == 0.6
    assert scored["finals_before_end"] == 1
    # The end came 0.3 s into the first pause, which had 0.5 s left.
    assert scored["early_ends"] == [{"t": -2.2, "silent_after_s": 0.5}]


def test_a_turn_end_after_the_caller_started_again_is_still_a_cut_off():
    clip = bench.Clip(b"", 0.5, 4.0, pauses=[(1.5, 2.3)])
    run = {"input_closed_s": 6.0, "events": [
        {"t": -1.5, "type": "END_OF_SPEECH", "text": ""},
        {"t": 0.5, "type": "END_OF_SPEECH", "text": ""}]}
    assert bench.score(phone_turn(), clip, run)["early_ends"] == [{"t": -1.5, "silent_after_s": 0.0}]


def test_rescore_rebuilds_the_pauses_from_the_cached_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "CACHE", tmp_path / "cache")
    word = b"\x10\x00" * 800
    turn = phone_turn()
    for part in turn.parts:
        if isinstance(part, str):
            key = bench.hashlib.sha1(f"v|{bench.RATE}|{part}".encode()).hexdigest()[:16]
            (tmp_path / "cache" / "v").mkdir(parents=True, exist_ok=True)
            (tmp_path / "cache" / "v" / f"{key}.pcm").write_bytes(word)
    run = {"arm": "nova-3", "turn": turn.id, "group": "digits", "voice": "v", "rep": 0,
           "seconds": 1.0, "speech_end_s": 0.5 + 0.3 + 0.8 + 0.1 + 0.8 + 0.1 - 0.0,
           "input_closed_s": 6.0, "events": [{"t": -1.0, "type": "END_OF_SPEECH", "text": ""}]}
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"lead_s": bench.LEAD_S, "noise_dbfs": -60, "runs": [run]}))
    bench.rescore(path)
    rescored = json.loads(path.read_text())["runs"][0]
    assert rescored["pauses"] == [[0.6, 1.4], [1.5, 2.3]]
    assert len(rescored["early_ends"]) == 1


def test_score_with_no_final_after_the_caller_stops():
    clip = bench.Clip(b"", 0.5, 2.0)
    scored = bench.score(phone_turn(), clip, {"input_closed_s": 6.0, "events": []})
    assert scored["final_s"] is None and scored["spans"] == {"718 555 0142": False}


def test_every_repeated_turn_runs_reps_times():
    planned = bench.plan(2)
    counts = {t.id: sum(1 for p, _ in planned if p.id == t.id) for t in bench.TURNS}
    assert all(counts[t.id] == (2 if t.group in bench.REPEATED else 1) for t in bench.TURNS)
    assert [t.id for t, _ in bench.plan(3, {"safety-gas"})] == ["safety-gas"]


def test_every_turn_has_a_span_and_every_group_is_reported():
    assert all(t.spans for t in bench.TURNS)
    assert {t.group for t in bench.TURNS} == {"street", "digits", "spelled", "safety"}


class FakeStream:
    """Answers with one final transcript and an end of speech once its input closes."""

    def __init__(self, text):
        self.text, self.frames, self.closed = text, 0, asyncio.Event()

    def push_frame(self, frame):
        self.frames += 1

    def end_input(self):
        self.closed.set()

    async def aclose(self):
        pass

    def __aiter__(self):
        return self.events()

    async def events(self):
        await self.closed.wait()
        alt = SimpleNamespace(text=self.text)
        yield SimpleNamespace(type=SimpleNamespace(name="FINAL_TRANSCRIPT"), alternatives=[alt])
        yield SimpleNamespace(type=SimpleNamespace(name="END_OF_SPEECH"), alternatives=[])


def test_stream_clip_gives_every_arm_the_same_frames():
    streams = {"a": FakeStream("one"), "b": FakeStream("two")}
    arms = {name: SimpleNamespace(stream=lambda s=s: s) for name, s in streams.items()}
    pcm = b"\0" * (bench.RATE // 10 * 2)  # 0.1 s, five frames
    clip = bench.Clip(pcm, 0.0, 0.06)
    out = asyncio.run(bench.stream_clip(arms, clip))
    assert streams["a"].frames == streams["b"].frames == 5
    assert [e["type"] for e in out["a"]["events"]] == ["FINAL_TRANSCRIPT", "END_OF_SPEECH"]
    assert out["b"]["events"][0]["text"] == "two"
    assert out["a"]["events"][0]["t"] >= out["a"]["input_closed_s"] >= 0


def test_summarize_and_report_from_a_results_file(tmp_path):
    clip = bench.Clip(b"", 0.5, 3.0)
    runs = []
    for arm, t in (("nova-3", 0.4), ("ink-2", 1.1)):
        run = {"input_closed_s": 6.0, "events": [
            {"t": t, "type": "FINAL_TRANSCRIPT", "text": "My number is 718-555-0142."},
            {"t": t, "type": "END_OF_SPEECH", "text": ""}]}
        runs.append({"arm": arm, "turn": "digits-phone", "group": "digits", "voice": "v",
                     "rep": 0, "seconds": 60.0, **run, **bench.score(phone_turn(), clip, run)})
    results = {"arms": {"nova-3": {"provider": "deepgram", "price_per_minute": 0.009},
                        "ink-2": {"provider": "cartesia", "price_per_minute": 0.0117}},
               "runs": runs, "caller_voice_chars": 1000}
    summary = bench.summarize(results, screen=lambda text: None)
    assert summary["arms"]["ink-2"]["decides_turn"] and not summary["arms"]["nova-3"]["decides_turn"]
    assert summary["arms"]["ink-2"]["usd"] == 0.0117
    assert summary["arms"]["nova-3"]["groups"]["digits"] == {"hits": 1, "n": 1}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(results))
    text = bench.report(path, screen=lambda text: None)
    assert "| digits | 1/1 | 1/1 |" in text and "| final_median_s | 0.4 | 1.1 |" in text


def test_the_server_screen_reads_the_safety_turns_as_the_tool_server_does():
    screen = bench.safety_screen()
    assert screen("The CO alarm is going off and it won't stop.") == "co"
    assert screen("I smelled gas in the kitchen.") == "gas"
    assert {t.screen for t in bench.TURNS if t.group == "safety"} == {"gas", "co"}


def test_a_safety_turn_the_screen_misses_is_counted_and_listed(tmp_path):
    turn = next(t for t in bench.TURNS if t.id == "safety-co")
    clip = bench.Clip(b"", 0.5, 2.0)
    run = {"input_closed_s": 6.0, "events": [
        {"t": 0.4, "type": "FINAL_TRANSCRIPT", "text": "The c o alarm is going off."}]}
    results = {"arms": {"nova-3": {"provider": "deepgram", "price_per_minute": 0.009}},
               "runs": [{"arm": "nova-3", "turn": "safety-co", "group": "safety", "voice": "v",
                         "rep": 0, "seconds": 8.0, **run, **bench.score(turn, clip, run)}]}
    screen = {"The c o alarm is going off.": None}.get
    summary = bench.summarize(results, screen=screen)["arms"]["nova-3"]
    assert summary["groups"]["safety"] == {"hits": 1, "n": 1}
    assert (summary["screen_hits"], summary["screen_n"]) == (0, 1)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(results))
    assert "server safety screen" in bench.report(path, screen=screen)
