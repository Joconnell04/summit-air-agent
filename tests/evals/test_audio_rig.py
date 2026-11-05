"""The audio rig offline: loudness and the voice-activity threshold, the playout clock, turn
endings, the paced caller stream, the router, and a whole call through a fake entrant, scored on
the real tool server and mock. No network: speech goes through a fake Deepgram."""
import asyncio
import itertools
import math

import httpx
import numpy as np
import pytest
from fakes import ScriptedModel, grade_reply, say

from evals import compare
from evals.audio_rig import line as line_mod
from evals.audio_rig.audio import Playout, Vad, dbfs, frames, reply_delay_ms, resample
from evals.audio_rig.line import Listener
from evals.audio_rig.rig import (
    Rig,
    RigContext,
    Sender,
    load_entrant,
    must_repeat,
    newest_caller_timings,
    plan,
    tool_count,
    turn_agent_timings,
)
from evals.audio_rig.router import Worlds, build_app, call_id_of
from evals.audio_rig.speech import Deepgram
from evals.audio_rig.tunnel import public_url
from evals.models import load_config
from evals.results import Run
from evals.scenario import Scenario

RATE = 16000


def tone(ms: int, level: float = 0.3, rate: int = RATE) -> bytes:
    n = rate * ms // 1000
    t = np.arange(n) / rate
    return (np.sin(2 * math.pi * 220 * t) * level * 32767).astype("<i2").tobytes()


def silence(ms: int, rate: int = RATE) -> bytes:
    return b"\0" * (rate * ms // 1000 * 2)


class Clock:
    def __init__(self, t: float = 100.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# Loudness and voice activity

def test_loudness_of_full_scale_and_silence():
    assert dbfs(silence(20)) == -math.inf
    assert dbfs(tone(20, level=1.0)) == pytest.approx(-3.0, abs=0.1)


def test_onset_is_the_first_voiced_frame_of_a_long_enough_run():
    vad = Vad(threshold_dbfs=-40, min_voiced_ms=40)
    pcm = silence(100) + tone(100)
    assert vad.onset(pcm, RATE) == RATE * 100 // 1000


def test_a_click_shorter_than_the_minimum_is_not_speech():
    vad = Vad(threshold_dbfs=-40, min_voiced_ms=40)
    pcm = silence(100) + tone(20) + silence(100) + tone(60)
    assert vad.onset(pcm, RATE) == RATE * 220 // 1000


def test_quiet_noise_under_the_threshold_is_not_speech():
    vad = Vad(threshold_dbfs=-40)
    assert vad.onset(tone(200, level=0.001), RATE) is None


def test_trim_keeps_only_the_voiced_part():
    vad = Vad()
    trimmed = vad.trim(silence(100) + tone(200) + silence(300), RATE)
    assert len(trimmed) == len(tone(200))
    assert vad.trim(silence(100), RATE) == b""


def test_frames_are_fixed_size_and_the_last_is_padded():
    out = frames(silence(50), RATE)
    assert [len(f) for f in out] == [640, 640, 640]


def test_resample_keeps_the_duration():
    assert len(resample(tone(100, rate=24000), 24000, RATE)) == len(tone(100))


# The playout clock

def test_a_burst_plays_from_when_it_arrived_at_real_speed():
    p = Playout(RATE)
    p.add(silence(500) + tone(500), arrived=10.0)
    assert p.heard_at(RATE // 2) == pytest.approx(10.5)
    assert p.end == pytest.approx(11.0)


def test_a_chunk_arriving_while_another_plays_waits_its_turn():
    p = Playout(RATE)
    p.add(silence(1000), arrived=10.0)
    p.add(tone(100), arrived=10.2)
    first_of_second = RATE
    assert p.heard_at(first_of_second) == pytest.approx(11.0)


def test_live_audio_plays_when_it_arrives_even_from_two_tracks():
    p = Playout(RATE)
    p.add(tone(20), arrived=10.0, live=True)
    p.add(tone(20), arrived=10.0, live=True)  # a second track's frame at the same moment
    p.add(tone(20), arrived=10.02, live=True)
    assert p.heard_at(2 * 320) == pytest.approx(10.02)
    assert p.end == pytest.approx(10.04)


def test_reply_delay_never_goes_negative():
    assert reply_delay_ms(10.0, 10.8) == pytest.approx(800)
    assert reply_delay_ms(10.0, 9.9) == 0


# Turn endings

def listener(clock) -> Listener:
    return Listener(Vad(), clock=clock, rate=RATE)


def test_first_sound_is_when_the_caller_hears_the_voice_not_when_the_packet_came():
    clock = Clock(50.0)
    lst = listener(clock)
    lst.new_turn()
    lst.audio(silence(300) + tone(400), RATE)
    assert lst.first_sound() == pytest.approx(50.3)


def test_a_backchannel_while_the_caller_talks_is_not_the_reply(monkeypatch):
    monkeypatch.setattr(line_mod, "QUIET_S", 0.0)
    clock = Clock(50.0)
    lst = listener(clock)
    lst.new_turn()
    lst.audio(tone(300), RATE)  # "mm-hmm" from 50.0 while the caller is still speaking
    clock.t = 51.0
    lst.audio(silence(500) + tone(400), RATE)  # the reply starts at 51.5

    async def go():
        clock.t = 53.0
        return await lst.turn(signals_done=False, since=50.8)

    heard = asyncio.run(go())
    assert heard.first_sound == pytest.approx(51.5)


def test_a_reply_started_in_a_pause_mid_line_is_sound_over_the_caller(monkeypatch):
    monkeypatch.setattr(line_mod, "QUIET_S", 0.0)
    clock = Clock(50.0)
    lst = listener(clock)
    lst.new_turn()
    clock.t = 50.4
    lst.audio(tone(300), RATE)  # the agent starts at 50.4, in a pause before the caller goes on
    clock.t = 51.5
    lst.audio(tone(400), RATE)  # and answers again after the caller finished at 51.2

    async def go():
        clock.t = 53.0
        return await lst.turn(signals_done=False, since=51.2)

    heard = asyncio.run(go())
    assert heard.over_caller == pytest.approx(50.4)
    assert heard.first_sound == pytest.approx(51.5)


def test_a_quiet_line_while_the_caller_talks_is_no_sound_over_the_caller():
    clock = Clock(50.0)
    lst = listener(clock)
    lst.new_turn()
    lst.audio(silence(800), RATE, live=True)
    clock.t = 51.0
    lst.audio(tone(400), RATE, live=True)

    async def go():
        clock.t = 51.4 + line_mod.QUIET_S + 0.1
        return await lst.turn(signals_done=False, since=50.9)

    assert asyncio.run(go()).over_caller is None


def test_sample_at_finds_the_sample_playing_at_a_time():
    p = Playout(RATE)
    p.add(tone(100), arrived=10.0)
    p.add(tone(100), arrived=11.0)
    assert p.sample_at(10.05) == RATE // 20
    assert p.sample_at(10.5) == RATE // 10  # in the gap: the next chunk's first sample
    assert p.sample_at(20.0) == RATE // 5


def test_without_a_platform_signal_the_turn_ends_after_quiet():
    clock = Clock(50.0)
    lst = listener(clock)
    lst.audio(tone(500), RATE)
    clock.t = 50.5 + line_mod.QUIET_S - 0.1
    assert not lst.finished(clock.t, signals_done=False)
    clock.t = 50.5 + line_mod.QUIET_S + 0.01
    assert lst.finished(clock.t, signals_done=False)


def test_with_a_signal_the_turn_ends_once_playout_has_finished():
    clock = Clock(50.0)
    lst = listener(clock)
    lst.audio(tone(1000), RATE)
    lst.done()
    assert not lst.finished(50.5, signals_done=True)
    assert lst.finished(51.0 + line_mod.SETTLE_S, signals_done=True)


def test_new_audio_after_the_signal_reopens_the_turn():
    clock = Clock(50.0)
    lst = listener(clock)
    lst.audio(tone(200), RATE)
    lst.done()
    clock.t = 50.3
    lst.audio(tone(200), RATE)
    assert not lst.finished(50.5 + line_mod.SETTLE_S, signals_done=True)


def test_a_turn_with_no_sound_is_silent_after_the_timeout(monkeypatch):
    monkeypatch.setattr(line_mod, "FIRST_SOUND_TIMEOUT_S", 0.05)
    lst = Listener(Vad(), rate=RATE)
    heard = asyncio.run(lst.turn(signals_done=True, since=lst.clock()))
    assert heard.first_sound is None


def test_a_hang_up_with_no_sound_ends_the_turn_at_once():
    lst = Listener(Vad(), rate=RATE)
    lst.hung_up()
    heard = asyncio.run(lst.turn(signals_done=False, since=lst.clock()))
    assert heard.hung_up and heard.first_sound is None


# The caller's stream

def test_the_sender_paces_frames_and_reports_when_speech_ended():
    clock = Clock(0.0)
    sent = []

    async def send(frame):
        sent.append((clock.t, frame))

    async def sleep(seconds):
        clock.t += seconds
        await asyncio.sleep(0)

    async def go():
        sender = Sender(send, RATE, clock=clock, sleep=sleep)
        task = asyncio.create_task(sender.run())
        await asyncio.sleep(0)
        start, end = await sender.speak(tone(100))
        sender.stopped = True
        await task
        return start, end

    start, end = asyncio.run(go())
    speech = [t for t, f in sent if f != b"\0" * 640]
    assert len(speech) == 5
    assert all(b - a == pytest.approx(0.02) for a, b in itertools.pairwise(speech))
    assert (start, end) == (pytest.approx(speech[0]), pytest.approx(speech[-1]))


def test_speaking_on_a_dropped_line_returns_at_once():
    async def send(frame):
        raise ConnectionError("closed")

    async def go():
        sender = Sender(send, RATE)
        await sender.run()  # the first send fails, so the stream stops
        return await asyncio.wait_for(sender.speak(tone(100)), timeout=1)

    start, end = asyncio.run(go())
    assert start == end


def test_audio_still_buffered_on_our_side_dates_the_end_of_speech_later():
    clock = Clock(0.0)

    async def send(frame):
        return None

    async def sleep(seconds):
        clock.t += seconds
        await asyncio.sleep(0)

    async def go():
        sender = Sender(send, RATE, clock=clock, sleep=sleep, queued=lambda: 0.08)
        task = asyncio.create_task(sender.run())
        await asyncio.sleep(0)
        _, end = await sender.speak(tone(40))
        sender.stopped = True
        await task
        return end, clock.t

    end, now = asyncio.run(go())
    assert end == pytest.approx(now + 0.08, abs=0.021)


# Router, tunnel, plan

def test_router_finds_the_call_in_the_path_or_the_body():
    assert call_id_of("calls/c-1/turn", {}) == "c-1"
    assert call_id_of("debug/calls/c-2", {}) == "c-2"
    assert call_id_of("tools/check_service_area", {"call_id": "c-3"}) == "c-3"
    assert call_id_of("calls/start", {"call_id": "c-4"}) == "c-4"


def test_router_sends_a_call_to_its_world_and_refuses_unknown_calls(tmp_path):
    from fastapi.testclient import TestClient

    from evals.world import World, seeded_db

    seed = seeded_db(tmp_path / "seed")
    worlds = Worlds()
    worlds.add("c-1", World(tmp_path / "c-1", seed, scenario().start_time))
    client = TestClient(build_app(worlds))
    started = client.post("/calls/start", json={"call_id": "c-1", "caller_id": "(212) 555-0999",
                                                "dialed_number": "(646) 624-0418"})
    assert started.status_code == 200 and started.json()["ok"]
    assert client.post("/calls/start", json={"call_id": "nope"}).status_code == 404
    assert client.post("/outbound/claim", json={}).json()["data"]["outbound"] is None


def test_tunnel_url_is_the_https_one_for_our_port():
    tunnels = {"tunnels": [
        {"public_url": "http://a.ngrok.app", "config": {"addr": "http://localhost:9000"}},
        {"public_url": "https://b.ngrok.app", "config": {"addr": "http://localhost:9000"}},
        {"public_url": "https://c.ngrok.app", "config": {"addr": "http://localhost:9001"}},
    ]}
    assert public_url(tunnels, 9000) == "https://b.ngrok.app"
    assert public_url(tunnels, 9002) is None


def scenario(**overrides) -> Scenario:
    data = {"id": "rig-test", "flow": ["booking"], "difficulty": "easy", "journeys": ["J1"],
            "persona": "A test caller.", "opening": "Hi, my heat is out.",
            "must": [{"booking": "none"}], "max_turns": 4}
    return Scenario.model_validate({**data, **overrides})


def test_must_pass_scenarios_repeat():
    gas = scenario(id="gas", flow=["safety"], spec=["S-17"], must=[{"safety_lines": "said_first"}])
    read_back = scenario(id="read-back", spec=["S-26"])
    other = scenario(id="other")
    assert must_repeat(gas) and must_repeat(read_back) and not must_repeat(other)
    assert [(s.id, n) for s, n in plan([gas, other], 3)] == [
        ("gas", 1), ("gas", 2), ("gas", 3), ("other", 1)]
    assert [(s.id, n) for s, n in plan([other], 3, samples=2)] == [("other", 1), ("other", 2)]


def test_only_model_tools_count_toward_a_lookup_turn():
    trace = {"events": [{"type": "tool", "tool": "calls.start"},
                        {"type": "tool", "tool": "check_service_area"}]}
    assert tool_count(trace) == 1


def test_the_voice_programs_own_timing_is_read_from_the_turns_lines():
    trace = {"events": [
        {"type": "turn", "speaker": "agent", "timings": {}},
        {"type": "turn", "speaker": "caller", "timings": {}},
        {"type": "turn", "speaker": "agent", "timings": {"reply_ms": 950.0}},
    ]}
    assert turn_agent_timings(trace, 1) == ({"reply_ms": 950.0}, 2)
    assert turn_agent_timings(trace, 2) == ({}, 2)


def test_a_turn_with_two_agent_lines_is_timed_by_the_first():
    trace = {"events": [
        {"type": "turn", "speaker": "caller", "timings": {}},
        {"type": "turn", "speaker": "agent", "timings": {"reply_ms": 1200.0, "tool_ms": 0.0}},
        {"type": "turn", "speaker": "agent", "timings": {"tool_ms": 40.0}},
    ]}
    stages, seen = turn_agent_timings(trace, 0)
    assert seen == 2
    assert stages == {"reply_ms": 1200.0, "tool_ms": 40.0, "agent_lines": 2.0}


def test_the_caller_lines_stage_timings_come_from_the_newest_caller_line():
    trace = {"events": [
        {"type": "turn", "speaker": "caller", "timings": {"end_of_turn_ms": 400.0}},
        {"type": "turn", "speaker": "agent", "timings": {}},
        {"type": "turn", "speaker": "caller", "timings": {"end_of_turn_ms": 310.0}},
    ]}
    assert newest_caller_timings(trace) == {"end_of_turn_ms": 310.0}
    assert newest_caller_timings({"events": []}) == {}


def test_unknown_entrants_are_refused():
    with pytest.raises(ValueError):
        load_entrant("vapi")


def test_an_audio_run_is_marked_and_keeps_the_rig_fields(make_run, scenario_data):
    timing = {"kind": "plain", "reply_delay_ms": 900, "model_first_token_ms": 0,
              "caller_speech_ms": 1800, "agent_speech_ms": 2400}
    rig_scenario = scenario_data(turn_timings=[timing], silent_turns=1,
                                 cost_parts={"platform": 0.02},
                                 transcript=[{"role": "agent", "text": "Hi", "said": "Hi."}])
    run = Run.model_validate({**make_run().model_dump(mode="json"), "transport": "audio",
                              "scenarios": [rig_scenario]})
    again = Run.model_validate_json(run.model_dump_json())
    assert again.transport == "audio"
    assert again.scenarios[0].turn_timings[0].caller_speech_ms == 1800
    assert again.scenarios[0].transcript[0].said == "Hi."
    lines = compare.report(again)
    assert any("first sound" in line for line in lines)
    assert any("silent agent turns" in line for line in lines)


def test_a_text_run_reads_as_before(make_run):
    assert make_run().transport == "text"


# A whole call through a fake entrant

def fake_deepgram(script: list[str]) -> Deepgram:
    """Speaks any line as 300 ms of tone; hears agent audio as the next scripted line."""
    heard = list(script)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/speak":
            return httpx.Response(200, content=silence(40) + tone(300) + silence(40))
        text = heard.pop(0) if heard else ""
        return httpx.Response(200, json={"results": {"channels": [
            {"alternatives": [{"transcript": text}]}]}})

    config = load_config()["audio_rig"]
    client = httpx.AsyncClient(base_url="https://dg.test", transport=httpx.MockTransport(handle))
    return Deepgram(config, client=client)


class FakeLine:
    """Answers each caller line 400 ms after it ends with half a second of voice, and posts both
    lines to the tool server as a voice layer would."""

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.listener = None
        self.server = None
        self.call_id = ""
        self.speaking = False
        self.quiet_frames = 0
        self.closed = None

    async def open(self, call_id, caller_id, world, listener):
        self.call_id, self.listener, self.server = call_id, listener, world.server
        self.server.start(call_id, caller_id)
        await self.agent("Thanks for calling Summit Air.")
        return world.server

    async def agent(self, text: str) -> None:
        await asyncio.sleep(0.4)
        self.listener.audio(tone(500), RATE)
        self.listener.said(text)
        self.server.turn(self.call_id, "agent", text, {"tts_first_byte_ms": 150.0})
        self.listener.done()

    async def send(self, pcm: bytes) -> None:
        voiced = pcm != b"\0" * len(pcm)
        if voiced:
            self.speaking, self.quiet_frames = True, 0
        elif self.speaking:
            self.quiet_frames += 1
            if self.quiet_frames == 1:
                self.speaking = False
                self.server.turn(self.call_id, "caller", "my heat is out",
                                 {"end_of_turn_ms": 350.0})
                text = self.replies.pop(0) if self.replies else "Okay."
                asyncio.get_running_loop().create_task(self.agent(text))

    async def close(self, reason: str) -> None:
        self.closed = reason
        self.server.end(self.call_id, reason)

    async def cost(self) -> dict:
        return {"platform": 0.01}


class FakeEntrant:
    name, profile, in_rate = "fake", "fast", RATE
    local_world, final_record, signals_done = True, False, True

    def __init__(self, line):
        self._line = line

    def line(self):
        return self._line


def test_a_whole_call_is_timed_on_the_audio_and_scored_on_the_record(tmp_path, monkeypatch):
    monkeypatch.setattr(line_mod, "SETTLE_S", 0.05)
    fake = FakeLine(["Sorry to hear that. Goodbye."])
    ctx = RigContext(Worlds(), "http://unused", load_config(), {})
    caller = ScriptedModel([say("[END]")])
    rig = Rig(FakeEntrant(fake), ctx, tmp_path, caller, ScriptedModel([grade_reply()]),
              verbose=False)
    deepgram = fake_deepgram(["Thanks for calling Summit Air.", "Sorry to hear that. Goodbye."])
    monkeypatch.setattr("evals.audio_rig.rig.Deepgram", lambda *a, **k: deepgram)

    result = asyncio.run(rig.call(scenario()))

    assert result.outcome == "pass", result.error or result.check_failures
    assert fake.closed == "caller_hangup"
    (timing,) = result.turn_timings
    # The fake answers 400 ms after the caller's last frame. The test runs on the wall clock, so
    # pacing and a busy CI runner can add a few hundred ms; the bound only checks the delay is
    # measured from the last frame, not from the start of the call.
    assert 380 <= timing.reply_delay_ms <= 1500
    assert timing.kind == "plain" and timing.caller_speech_ms == pytest.approx(300, abs=25)
    assert timing.platform_stages == {"end_of_turn_ms": 350.0, "tts_first_byte_ms": 150.0}
    assert timing.over_caller_ms is None
    assert [t.role for t in result.transcript] == ["agent", "caller", "agent"]
    assert result.transcript[-1].said == "Sorry to hear that. Goodbye."
    assert result.cost_parts["platform"] == 0.01
    assert result.agent_cost_usd == 0.01
    assert result.cost_usd >= 0.01


# The live-like suite on the rig: the caller's patience, cutting in, the timeline and its checks

class PartsLine(FakeLine):
    """Answers each caller line with parts: (pause before it in seconds, text, ms of voice). Can
    hang up right after its last reply."""

    def __init__(self, replies: list[list[tuple[float, str, int]]], hang_up_after: bool = False):
        super().__init__([])
        self.parts = replies
        self.hang_up_after = hang_up_after

    async def speak(self, parts) -> None:
        for pause, text, ms in parts:
            await asyncio.sleep(pause)
            self.listener.audio(tone(ms), RATE)
            self.server.turn(self.call_id, "agent", text, {"tts_first_byte_ms": 150.0})
            await asyncio.sleep(ms / 1000)
            self.listener.done()
        if self.hang_up_after and not self.parts:
            self.listener.hung_up()

    async def open(self, call_id, caller_id, world, listener):
        self.call_id, self.listener, self.server = call_id, listener, world.server
        self.server.start(call_id, caller_id)
        await self.speak([(0.2, "Thanks for calling Summit Air. How can I help?", 300)])
        return world.server

    async def send(self, pcm: bytes) -> None:
        voiced = pcm != b"\0" * len(pcm)
        if voiced:
            self.speaking, self.quiet_frames = True, 0
        elif self.speaking:
            self.speaking = False
            self.server.turn(self.call_id, "caller", "a caller line", {})
            parts = self.parts.pop(0) if self.parts else [(0.3, "Okay?", 200)]
            asyncio.get_running_loop().create_task(self.speak(parts))


def parts_deepgram(heard: list[str], words: dict[str, list] | None = None) -> Deepgram:
    """Hears each agent clip as the next line of `heard`, with optional word timings by line."""
    queue = list(heard)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/speak":
            return httpx.Response(200, content=silence(40) + tone(300) + silence(40))
        text = queue.pop(0) if queue else ""
        timed = [{"word": w, "punctuated_word": w, "start": a, "end": b}
                 for w, a, b in (words or {}).get(text, [])]
        return httpx.Response(200, json={"results": {"channels": [
            {"alternatives": [{"transcript": text, "words": timed}]}]}})

    config = load_config()["audio_rig"]
    client = httpx.AsyncClient(base_url="https://dg.test", transport=httpx.MockTransport(handle))
    return Deepgram(config, client=client)


def run_call(monkeypatch, tmp_path, line, heard, caller_lines, timing, **overrides):
    monkeypatch.setattr(line_mod, "SETTLE_S", 0.05)
    monkeypatch.setattr("evals.audio_rig.rig.load_style", lambda name: {"timing": timing})
    ctx = RigContext(Worlds(), "http://unused", load_config(), {})
    caller = ScriptedModel([say(t) for t in caller_lines])
    rig = Rig(FakeEntrant(line), ctx, tmp_path, caller, ScriptedModel([grade_reply()]),
              verbose=False)
    deepgram = parts_deepgram(heard)
    monkeypatch.setattr("evals.audio_rig.rig.Deepgram", lambda *a, **k: deepgram)
    result = asyncio.run(rig.call(scenario(**overrides)))
    return result, caller


def test_the_caller_waits_out_a_line_that_asks_nothing_and_hears_what_comes_next(tmp_path,
                                                                                monkeypatch):
    line = PartsLine([[(0.2, "Let me look.", 200), (0.8, "I have Friday. Okay?", 300)]])
    heard = ["Thanks for calling Summit Air. How can I help?", "Let me look.",
             "I have Friday. Okay?"]
    result, caller = run_call(monkeypatch, tmp_path, line, heard, ["[END]"],
                              {"patience_s": 2.0})
    assert result.transcript[-1].text == "Let me look. I have Friday. Okay?"
    # The caller model was answering the whole turn, not the first half of it.
    assert caller.calls[0]["messages"][-1]["content"] == "Let me look. I have Friday. Okay?"
    assert len(result.turn_timings) == 1


def test_a_half_reply_then_silence_is_told_to_the_caller_and_fails_the_audio_checks(tmp_path,
                                                                                    monkeypatch):
    line = PartsLine([[(0.2, "Got it.", 200)]])
    heard = ["Thanks for calling Summit Air. How can I help?", "Got it."]
    heard.append("Okay?")
    result, caller = run_call(monkeypatch, tmp_path, line, heard, ["Is that all?", "[END]"],
                              {"patience_s": 3.2}, audio_checks=True)
    assert caller.calls[0]["messages"][-1]["content"] == "Got it. (Then nothing for 3 seconds.)"
    assert result.outcome == "fail"
    assert any(f.startswith('half_reply: "Got it." asked nothing') for f in result.check_failures)
    assert "half_reply" in result.checks and "time_as_asked" in result.checks


def test_without_audio_checks_the_same_call_is_scored_as_before(tmp_path, monkeypatch):
    line = PartsLine([[(0.2, "Got it.", 200)]])
    heard = ["Thanks for calling Summit Air. How can I help?", "Got it."]
    result, _ = run_call(monkeypatch, tmp_path, line, heard, ["[END]"], {"patience_s": 0.5})
    assert result.outcome == "pass", result.check_failures
    assert "half_reply" not in result.checks


def test_the_caller_cuts_in_on_a_turn_that_runs_too_long(tmp_path, monkeypatch):
    line = PartsLine([[(0.2, "A very long explanation of the visit and the windows", 3000)]])
    heard = ["Thanks for calling Summit Air. How can I help?", "A very long explanation"]
    result, caller = run_call(monkeypatch, tmp_path, line, heard, ["[END]"],
                              {"patience_s": 1.0, "cut_in_after_s": 0.5})
    assert caller.calls[0]["messages"][-1]["content"] == "A very long explanation ..."
    (timing,) = result.turn_timings
    assert timing.agent_speech_ms < 2000, "the caller stopped listening before the agent finished"


def test_a_hang_up_right_after_the_goodbye_fails_the_audio_checks(tmp_path, monkeypatch):
    line = PartsLine([[(0.2, "Thanks for calling. Goodbye.", 300)]], hang_up_after=True)
    heard = ["Thanks for calling Summit Air. How can I help?", "Thanks for calling. Goodbye."]
    result, _ = run_call(monkeypatch, tmp_path, line, heard, ["Bye."], {"patience_s": 1.0},
                         audio_checks=True)
    assert any(f.startswith("hangup_gap:") for f in result.check_failures), result.check_failures


def test_voiced_runs_join_short_pauses_and_split_long_ones():
    pcm = tone(200) + silence(100) + tone(200) + silence(1000) + tone(100)
    runs = Vad().runs(pcm, RATE)
    assert len(runs) == 2
    assert runs[0][0] == 0 and runs[1][0] == pytest.approx(RATE * 1.5, abs=RATE * 0.03)


def test_a_heard_turn_dates_its_samples_from_the_playout():
    clock = Clock(10.0)
    listener = Listener(Vad(), clock, RATE)
    listener.audio(tone(200), RATE)
    clock.t = 11.0
    listener.audio(tone(200), RATE)
    clock.t = 20.0
    heard = asyncio.run(listener.turn(False, 10.0))
    assert heard.at(0) == 10.0
    assert heard.at(RATE // 5) == 11.0


def test_a_turn_held_open_after_the_agent_stopped_is_not_cut_into():
    ticks = itertools.count(0)
    def clock():  # moves on every read
        return 12.0 + next(ticks) * 0.5
    listener = Listener(Vad(), clock, RATE)
    listener.playout.add(tone(200), 10.0)
    # No "listening" signal comes, so the turn stays open for QUIET_SIGNALLED_S after the agent's
    # last sound; the caller only cuts in while the agent is still talking.
    heard = asyncio.run(listener.turn(True, 9.0, cut_after_s=1.0))
    assert not heard.cut and len(heard.pcm) == len(tone(200))
