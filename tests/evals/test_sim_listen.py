"""Recordings of simulated calls (evals/audio_rig/recording.py), their durable copy (store.py), the
listening page (listen.py) and `make sim` (sim.py)."""
import asyncio
import json
import math
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fakes import ScriptedModel, grade_reply, say
from test_audio_rig import FakeEntrant, FakeLine, fake_deepgram, scenario

from evals.audio_rig import line as line_mod
from evals.audio_rig import listen, recording, sim, store
from evals.audio_rig.audio import FRAME_MS
from evals.audio_rig.recording import LEAD_S, Batch, CallRecorder
from evals.audio_rig.rig import Rig, RigContext
from evals.audio_rig.router import Worlds
from evals.models import load_config

RATE = 24000


def tone(ms: int, rate: int = RATE, level: float = 0.3) -> bytes:
    n = rate * ms // 1000
    t = np.arange(n) / rate
    return (np.sin(2 * math.pi * 220 * t) * level * 32767).astype("<i2").tobytes()


def voiced(channel: np.ndarray) -> np.ndarray:
    return np.flatnonzero(np.abs(channel) > 100)


def channels(stereo: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pairs = stereo.reshape(-1, 2)
    return pairs[:, 0], pairs[:, 1]


# The stereo writer's timing

def test_the_pause_before_a_reply_is_the_measured_one():
    rec = CallRecorder(RATE)
    start = rec.caller(tone(300), RATE, end=10.3)
    rec.line("caller", "my heat is out", start)
    rec.agent(tone(500), RATE, start=11.0)
    rec.line("agent", "Sorry to hear that.", 11.0, reply_delay_ms=700)

    stereo, lines = rec.render()
    left, right = channels(stereo)
    caller_end = voiced(left)[-1] / RATE
    agent_start = voiced(right)[0] / RATE
    assert start == pytest.approx(10.0)
    assert voiced(left)[0] / RATE == pytest.approx(LEAD_S, abs=0.001)
    assert agent_start - caller_end == pytest.approx(0.7, abs=0.002)
    assert lines == [
        {"n": 1, "speaker": "caller", "t": 0.5, "text": "my heat is out"},
        {"n": 2, "speaker": "agent", "t": 1.5, "text": "Sorry to hear that.",
         "reply_delay_ms": 700},
    ]


def test_caller_left_agent_right_never_mixed():
    rec = CallRecorder(RATE)
    rec.caller(tone(200), RATE, end=1.2)
    rec.agent(tone(200), RATE, start=1.0)  # the agent talking over the caller
    left, right = channels(rec.render()[0])
    assert len(voiced(left)) and len(voiced(right))
    # Both channels sound at once, each on its own side.
    assert set(voiced(left)) & set(voiced(right))


def test_overlapping_agent_chunks_play_one_after_the_other_and_a_gap_stays_a_gap():
    rec = CallRecorder(RATE)
    rec.agent(tone(100), RATE, start=0.0)
    rec.agent(tone(100), RATE, start=0.05)  # arrived early; played after the first
    rec.agent(tone(100), RATE, start=1.0)  # after a real pause
    starts = [c.start for c in rec.clips]
    assert starts == pytest.approx([0.0, 0.1, 1.0])


def test_a_lower_rate_side_is_resampled_and_keeps_its_length():
    rec = CallRecorder(RATE)
    rec.agent(tone(400, rate=16000), 16000, start=0.0)
    _, right = channels(rec.render()[0])
    span = (voiced(right)[-1] - voiced(right)[0]) / RATE
    assert span == pytest.approx(0.4, abs=0.002)


def test_the_file_is_stereo_at_the_recorders_rate(tmp_path):
    rec = CallRecorder(RATE)
    rec.caller(tone(300), RATE, end=1.0)
    seconds = rec.write_wav(tmp_path / "S1.wav")
    with wave.open(str(tmp_path / "S1.wav")) as f:
        assert (f.getnchannels(), f.getsampwidth(), f.getframerate()) == (2, 2, RATE)
        assert f.getnframes() / RATE == pytest.approx(seconds)
    assert seconds == pytest.approx(LEAD_S + 0.3, abs=0.001)


def test_an_empty_call_renders_as_nothing():
    stereo, lines = CallRecorder(RATE).render()
    assert len(stereo) == 0 and lines == []


def test_a_whole_rig_call_is_recorded_with_the_measured_delay(tmp_path, monkeypatch):
    monkeypatch.setattr(line_mod, "SETTLE_S", 0.05)
    fake = FakeLine(["Sorry to hear that. Goodbye."])
    ctx = RigContext(Worlds(), "http://unused", load_config(), {})
    rig = Rig(FakeEntrant(fake), ctx, tmp_path, ScriptedModel([say("[END]")]),
              ScriptedModel([grade_reply()]), verbose=False)
    deepgram = fake_deepgram(["Thanks for calling Summit Air.", "Sorry to hear that. Goodbye."])
    monkeypatch.setattr("evals.audio_rig.rig.Deepgram", lambda *a, **k: deepgram)
    rec = CallRecorder(RATE)

    result = asyncio.run(rig.call(scenario(), recorder=rec))

    stereo, lines = rec.render()
    left, right = channels(stereo)
    caller_end = voiced(left)[-1] / RATE
    reply = voiced(right)[voiced(right) / RATE > caller_end][0] / RATE
    (timing,) = result.turn_timings
    # The caller's padded last frame can hold up to 20 ms of silence after the tone.
    assert (reply - caller_end) * 1000 == pytest.approx(timing.reply_delay_ms, abs=FRAME_MS + 5)
    assert [ln["speaker"] for ln in lines] == ["agent", "caller", "agent"]
    assert lines[2]["reply_delay_ms"] == round(timing.reply_delay_ms)


# A batch and its durable copy

class FakeStore:
    def __init__(self):
        self.put_keys = []

    def put(self, path, key):
        assert path.is_file()
        self.put_keys.append(key)
        return True


def result_for(scenario_id: str, sample: int = 1):
    return SimpleNamespace(scenario_id=scenario_id, sample=sample, call_id=f"rig-{scenario_id}",
                           outcome="pass")


def recorded_call() -> CallRecorder:
    rec = CallRecorder(RATE)
    rec.caller(tone(200), RATE, end=1.0)
    rec.line("caller", "hello", 0.8)
    return rec


def test_calls_get_short_codes_in_the_order_they_ran(tmp_path):
    keep = FakeStore()
    batch = Batch(tmp_path / "20261104-010203-livekit", "livekit", keep, flac=False)
    batch.add(recorded_call(), result_for("co-alarm-clear"), "Tomas, a renter")
    batch.add(recorded_call(), result_for("complaint-flag"))
    batch.finish(recording.ROOT / "data" / "sim" / "x" / "run.json")

    manifest = json.loads((batch.folder / "manifest.json").read_text())
    assert [c["code"] for c in manifest["calls"]] == ["S1", "S2"]
    assert [c["scenario_id"] for c in manifest["calls"]] == ["co-alarm-clear", "complaint-flag"]
    assert manifest["results"] == ["data/sim/x/run.json"]
    transcript = json.loads((batch.folder / "S1.json").read_text())
    assert transcript["persona"] == "Tomas, a renter"
    assert transcript["lines"][0]["text"] == "hello"
    b = "20261104-010203-livekit"
    assert keep.put_keys == [
        f"{b}/S1.wav", f"{b}/S1.json", f"{b}/manifest.json",
        f"{b}/S2.wav", f"{b}/S2.json", f"{b}/manifest.json",
        f"{b}/manifest.json",
    ]


def test_bucket_keys_come_only_from_the_bucket_lines_of_an_env_file():
    text = "ANTHROPIC_API_KEY=x\nRECORDING_S3_BUCKET=recordings\nRECORDING_S3_SECRET='s'\n"
    assert store.parse_env(text) == {"RECORDING_S3_BUCKET": "recordings",
                                     "RECORDING_S3_SECRET": "s"}


def test_bucket_keys_here_are_used_before_asking_the_server():
    def never(*_a, **_k):
        raise AssertionError("asked the server")

    keys = store.bucket_env({"RECORDING_S3_BUCKET": "recordings"}, run=never)
    assert keys["RECORDING_S3_BUCKET"] == "recordings"


def test_bucket_keys_are_read_from_the_server_and_none_when_it_refuses():
    asked = []

    def server(cmd, **_k):
        asked.append(cmd)
        return SimpleNamespace(returncode=0, stdout="RECORDING_S3_BUCKET=recordings\n")

    assert store.bucket_env({}, run=server)["RECORDING_S3_BUCKET"] == "recordings"
    assert asked[0][0] == "ssh" and store.HOST in asked[0]
    refused = store.bucket_env({}, run=lambda *a, **k: SimpleNamespace(returncode=255, stdout=""))
    assert refused is None


def test_files_go_under_sim_and_a_refusal_does_not_stop_the_run(tmp_path, capsys):
    path = tmp_path / "S1.flac"
    path.write_bytes(b"x")
    calls = []

    class Client:
        def upload_file(self, src, bucket, key, ExtraArgs):
            calls.append((bucket, key, ExtraArgs["ContentType"]))
            if len(calls) > 1:
                raise RuntimeError("denied")

    keep = store.SimStore({"RECORDING_S3_BUCKET": "recordings"}, client=Client())
    assert keep.put(path, "b/S1.flac")
    assert calls == [("recordings", "sim/b/S1.flac", "audio/flac")]
    assert not keep.put(path, "b/S1.flac")
    assert keep.failed == 1 and "denied" not in capsys.readouterr().out


# The listening page

def make_batch(root, name: str, codes: list[str]) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    calls = []
    for code in codes:
        (folder / f"{code}.flac").write_bytes(b"fLaC")
        (folder / f"{code}.json").write_text(json.dumps({
            "persona": "Tomas & co", "outcome": "fail", "lines": [
                {"n": 1, "speaker": "agent", "t": 0.5, "text": "Thanks for calling."},
                {"n": 2, "speaker": "caller", "t": 62.4, "text": "<b>my alarm</b>"},
            ]}))
        calls.append({"code": code, "scenario_id": "co-alarm-clear", "sample": 1,
                      "audio": f"{code}.flac", "transcript": f"{code}.json", "duration_s": 70})
    (folder / "manifest.json").write_text(json.dumps({"batch": name, "calls": calls}))


def test_batches_are_listed_newest_first_and_folders_without_a_manifest_are_skipped(tmp_path):
    make_batch(tmp_path, "20261104-010000-livekit", ["S1"])
    make_batch(tmp_path, "20261104-020000-livekit", ["S1", "S2"])
    (tmp_path / "20261104-030000-livekit").mkdir()
    (tmp_path / "notes").mkdir()
    assert listen.batches(tmp_path) == ["20261104-020000-livekit", "20261104-010000-livekit"]
    assert listen.batches(tmp_path / "missing") == []


def test_a_listing_carries_each_calls_lines(tmp_path):
    make_batch(tmp_path, "20261104-010000-livekit", ["S1", "S2"])
    batch = listen.listing(tmp_path, "20261104-010000-livekit")
    assert [c["code"] for c in batch["calls"]] == ["S1", "S2"]
    assert batch["calls"][0]["lines"][1]["t"] == 62.4


def test_the_page_shows_codes_numbered_timed_lines_and_no_verdict(tmp_path):
    from fastapi.testclient import TestClient

    make_batch(tmp_path, "20261104-010000-livekit", ["S1"])
    make_batch(tmp_path, "20261104-020000-livekit", ["S1", "S2"])
    client = TestClient(listen.create_app(tmp_path))

    page = client.get("/").text
    assert '<div class="code">S2</div>' in page
    assert 'data-t="62.4"' in page and '<span class="t">1:02</span>' in page
    assert '<span class="n">2</span>' in page
    assert "&lt;b&gt;my alarm&lt;/b&gt;" in page and "Tomas &amp; co" in page
    assert "fail" not in page
    older = client.get("/?batch=20261104-010000-livekit").text
    assert '<div class="code">S2</div>' not in older
    assert client.get("/?batch=nope").status_code == 404


def test_the_page_serves_only_a_calls_own_audio(tmp_path):
    from fastapi.testclient import TestClient

    make_batch(tmp_path, "20261104-010000-livekit", ["S1"])
    (tmp_path / "secret.txt").write_text("no")
    client = TestClient(listen.create_app(tmp_path))
    ok = client.get("/audio/20261104-010000-livekit/S1.flac")
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/flac"
    assert ok.headers.get("accept-ranges") == "bytes"
    assert client.get("/audio/20261104-010000-livekit/S1.json").status_code == 404
    assert client.get("/audio/20261104-010000-livekit/..%2Fsecret.txt").status_code == 404
    assert client.get("/audio/20261104-010000-livekit/S9.flac").status_code == 404


def test_an_empty_folder_says_how_to_make_a_batch(tmp_path):
    from fastapi.testclient import TestClient

    page = TestClient(listen.create_app(tmp_path)).get("/").text
    assert "make sim" in page


# make sim

def test_a_listening_batch_runs_each_named_scenario_once_into_its_own_folder():
    args = sim.rig_args("co-alarm-clear  complaint-flag,asks-if-ai", "", 2.0)
    assert args[args.index("--only") + 1] == "co-alarm-clear,complaint-flag,asks-if-ai"
    assert args[args.index("--repeats") + 1] == "1"
    assert args[args.index("--suite") + 1] == "full"
    assert "--results-in-batch" in args and "--no-upload" in args
    assert "--only" not in sim.rig_args("", "flow:safety", 2.0)


def test_a_scenario_id_that_does_not_exist_stops_the_rig_before_any_call(capsys):
    from evals.audio_rig import rig

    assert rig.main(["--stack", "livekit", "--suite", "full", "--only", "no-such-call"]) == 2
    assert "no-such-call" in capsys.readouterr().err


def test_adding_to_a_batch_numbers_on_and_keeps_every_results_file(tmp_path):
    folder = tmp_path / "20261104-010203-livekit"
    first = Batch(folder, "livekit", flac=False)
    first.add(recorded_call(), result_for("co-alarm-clear"))
    first.finish(tmp_path / "a.json")
    again = Batch(folder, "livekit", flac=False)
    entry = again.add(recorded_call(), result_for("complaint-flag"))
    again.finish(tmp_path / "b.json")
    manifest = json.loads((folder / "manifest.json").read_text())
    assert entry["code"] == "S2"
    assert [c["code"] for c in manifest["calls"]] == ["S1", "S2"]
    assert len(manifest["results"]) == 2


def test_adding_to_a_batch_that_does_not_exist_stops_before_any_call(tmp_path, capsys):
    from evals.audio_rig import rig

    code = rig.main(["--stack", "livekit", "--suite", "full", "--only", "co-alarm-clear",
                     "--record-dir", str(tmp_path), "--batch", "20261104-000000-livekit"])
    assert code == 2 and "no batch" in capsys.readouterr().err
    assert sim.rig_args("a", "", 1.0, batch="x")[-2:] == ["--batch", "x"]
