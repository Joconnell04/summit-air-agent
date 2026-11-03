"""Call recordings (story O-6, app/recordings.py): the server names the file, accepts only that
name back, copies the finished file from the bucket into its data folder, plays it on the call
page, and deletes it with the transcript (spec S-45)."""

from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from test_ts_owner import TOKEN
from toolkit import UNKNOWN, Agent, count, make_server

from app import jobs, recordings
from app.settings import Settings

AUDIO = b"\x00\x00\x00\x18ftypM4A fake audio"


class FakeBucket:
    """The bucket LiveKit uploads to, as a dict."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def fetch(self, key: str, dest: Path) -> bool:
        if key not in self.objects:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[key])
        return True

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def server(tmp_path, clock, mock, **settings) -> tuple[Agent, FakeBucket]:
    app = make_server(
        tmp_path, clock, mock[0], owner_token=TOKEN, recording_bucket="calls", **settings
    )
    bucket = FakeBucket()
    app.state.services.bucket = bucket
    return Agent(app, clock), bucket


@pytest.fixture
def recorded(tmp_path, clock, mock):
    return server(tmp_path, clock, mock)


def record(agent: Agent, call: str, key: str | None = None) -> dict:
    body = {"egress_id": "EG_1", "object_key": key or f"summit-air/{call}.mp4"}
    response = agent.client.post(f"/calls/{call}/recording", json=body)
    assert response.status_code == 200
    return response.json()


def collect(agent: Agent) -> dict:
    services = agent.app.state.services
    conn = services.connect()
    try:
        return recordings.collect(
            conn,
            services.tenants.default,
            services.bucket,
            services.settings.recordings_dir,
            services.clock(),
        )
    finally:
        conn.close()


def a_recorded_call(agent: Agent, bucket: FakeBucket) -> str:
    started = agent.start(UNKNOWN)
    call = started["data"]["call_id"]
    assert started["data"]["recording"] == {"object_key": f"summit-air/{call}.mp4"}
    assert record(agent, call)["ok"]
    agent.turn(call, "My AC is making a loud noise.")
    agent.end(call)
    bucket.objects[f"summit-air/{call}.mp4"] = AUDIO
    return call


def test_with_no_bucket_nothing_is_recorded(agent):
    assert agent.start(UNKNOWN)["data"]["recording"] is None


def test_a_tenant_that_does_not_record_gets_no_file_name(recorded, monkeypatch):
    agent, _ = recorded
    tenant = agent.app.state.services.tenants.default
    monkeypatch.setitem(tenant.retention, "record_calls", False)
    assert agent.start(UNKNOWN)["data"]["recording"] is None


def test_only_the_file_name_the_server_gave_is_accepted(recorded):
    agent, _ = recorded
    call = agent.start(UNKNOWN)["data"]["call_id"]
    other = agent.start("(212) 555-0123")["data"]["call_id"]
    assert record(agent, call, f"summit-air/{other}.mp4")["refused"] == "invalid_arguments"
    assert count(agent, "SELECT COUNT(*) FROM recordings") == 0


def test_a_finished_recording_is_copied_here_and_removed_from_the_bucket(recorded):
    agent, bucket = recorded
    call = a_recorded_call(agent, bucket)
    assert collect(agent) == {"stored": 1, "missing": 0}
    path = agent.app.state.services.settings.recordings_dir / f"summit-air/{call}.mp4"
    assert path.read_bytes() == AUDIO
    assert bucket.objects == {}
    assert count(agent, "SELECT COUNT(*) FROM recordings WHERE status = 'stored'") == 1
    # Collected once.
    assert collect(agent) == {"stored": 0, "missing": 0}


def test_a_call_still_going_is_left_in_the_bucket(recorded):
    agent, bucket = recorded
    call = agent.start(UNKNOWN)["data"]["call_id"]
    record(agent, call)
    bucket.objects[f"summit-air/{call}.mp4"] = AUDIO
    assert collect(agent) == {"stored": 0, "missing": 0}
    assert bucket.objects


def test_a_recording_that_never_arrives_is_marked_missing(recorded, clock):
    agent, _ = recorded
    call = agent.start(UNKNOWN)["data"]["call_id"]
    record(agent, call)
    agent.end(call)
    assert collect(agent) == {"stored": 0, "missing": 0}
    clock.advance(hours=3)
    assert collect(agent) == {"stored": 0, "missing": 1}


def test_the_jobs_loop_collects_recordings(recorded):
    agent, bucket = recorded
    a_recorded_call(agent, bucket)
    report = jobs.run_once(agent.app.state.services)
    assert report["summit-air"]["recordings"] == {"stored": 1, "missing": 0}


def test_the_call_page_plays_the_recording(recorded):
    agent, bucket = recorded
    call = a_recorded_call(agent, bucket)
    collect(agent)
    agent.client.get(f"/owner?token={TOKEN}")
    page = agent.client.get(f"/owner/calls/{call}").text
    assert f'src="/owner/calls/{call}/recording"' in page
    assert "data-player" in page
    assert "recording isn't kept" not in page
    audio = agent.client.get(f"/owner/calls/{call}/recording")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/mp4"
    assert audio.content == AUDIO


def test_a_call_with_no_recording_says_so_and_has_no_player(recorded):
    agent, _ = recorded
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.end(call)
    agent.client.get(f"/owner?token={TOKEN}")
    page = agent.client.get(f"/owner/calls/{call}").text
    assert "data-player" not in page
    assert "No recording of this call." in page
    assert agent.client.get(f"/owner/calls/{call}/recording").status_code == 404


def test_the_recording_needs_the_owner_sign_in(recorded):
    agent, bucket = recorded
    call = a_recorded_call(agent, bucket)
    collect(agent)
    response = agent.client.get(f"/owner/calls/{call}/recording")
    assert response.status_code != 200 or response.content != AUDIO


def test_a_recording_is_deleted_with_the_transcript(tmp_path, clock, mock):
    agent, bucket = server(tmp_path, clock, mock, environment="production")
    call = a_recorded_call(agent, bucket)
    collect(agent)
    path = agent.app.state.services.settings.recordings_dir / f"summit-air/{call}.mp4"
    clock.advance(days=29)
    jobs.run_once(agent.app.state.services)
    assert path.exists()
    clock.advance(days=2)
    report = jobs.run_once(agent.app.state.services)
    assert report["summit-air"]["purged"]["recordings"] == 1
    assert not path.exists()
    assert count(agent, "SELECT COUNT(*) FROM recordings WHERE status = 'deleted'") == 1
    agent.client.get(f"/owner?token={TOKEN}")
    assert agent.client.get(f"/owner/calls/{call}/recording").status_code == 404


def test_development_keeps_recordings(recorded, clock):
    agent, bucket = recorded
    a_recorded_call(agent, bucket)
    collect(agent)
    clock.advance(days=31)
    jobs.run_once(agent.app.state.services)
    assert count(agent, "SELECT COUNT(*) FROM recordings WHERE status = 'stored'") == 1


class FakeS3:
    """Enough of a boto3 S3 client for Bucket."""

    exceptions = type("Exceptions", (), {"ClientError": ClientError})

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def download_file(self, bucket, key, filename):
        Path(filename).write_bytes(self.objects[key])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key)


def test_the_bucket_copies_through_a_partial_file_and_deletes(tmp_path):
    s3 = FakeS3({"summit-air/a.mp4": AUDIO})
    bucket = recordings.Bucket(Settings(db_path=tmp_path / "t.db", recording_bucket="calls"), s3)
    dest = tmp_path / "rec" / "summit-air" / "a.mp4"
    assert not bucket.fetch("summit-air/b.mp4", dest)
    assert bucket.fetch("summit-air/a.mp4", dest)
    assert dest.read_bytes() == AUDIO
    assert not dest.with_suffix(".part").exists()
    bucket.delete("summit-air/a.mp4")
    assert s3.objects == {}


def test_a_bucket_that_refuses_is_logged_not_taken_for_not_there_yet(tmp_path, caplog):
    class Refusing(FakeS3):
        def head_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "403"}}, "HeadObject")

    bucket = recordings.Bucket(
        Settings(db_path=tmp_path / "t.db", recording_bucket="calls"), Refusing({})
    )
    assert not bucket.fetch("summit-air/a.mp4", tmp_path / "a.mp4")
    assert "bucket refused summit-air/a.mp4: 403" in caplog.text
    caplog.clear()
    assert not recordings.Bucket(
        Settings(db_path=tmp_path / "t.db", recording_bucket="calls"), FakeS3({})
    ).fetch("summit-air/a.mp4", tmp_path / "a.mp4")
    assert caplog.text == ""


def test_a_call_id_that_climbs_out_of_the_folder_is_never_written(recorded, tmp_path):
    agent, bucket = recorded
    # A call id is the voice layer's; one shaped like a path still can't place a file elsewhere.
    call = "../../outside"
    agent.start(UNKNOWN, call_id=call)
    key = f"summit-air/{call}.mp4"
    conn = agent.db()
    conn.execute(
        "INSERT INTO recordings (call_id, object_key, egress_id, started_at, updated_at) "
        "VALUES (?, ?, 'EG_1', '2026-11-02T14:00:00Z', '2026-11-02T14:00:00Z')",
        (call, key),
    )
    conn.execute("UPDATE calls SET ended_at = '2026-11-02T14:05:00Z' WHERE id = ?", (call,))
    conn.close()
    bucket.objects[key] = AUDIO
    assert collect(agent) == {"stored": 0, "missing": 1}
    folder = agent.app.state.services.settings.recordings_dir
    assert not (folder / key).resolve().exists()
    assert bucket.objects == {key: AUDIO}


def test_a_stored_path_outside_the_folder_is_never_served(recorded, tmp_path):
    agent, bucket = recorded
    call = a_recorded_call(agent, bucket)
    collect(agent)
    elsewhere = tmp_path / "elsewhere.mp4"
    elsewhere.write_bytes(AUDIO)
    conn = agent.db()
    conn.execute("UPDATE recordings SET path = ? WHERE call_id = ?", (str(elsewhere), call))
    conn.close()
    agent.client.get(f"/owner?token={TOKEN}")
    assert agent.client.get(f"/owner/calls/{call}/recording").status_code == 404


def test_recordings_live_beside_the_database_unless_set(tmp_path):
    assert Settings(db_path=tmp_path / "data" / "t.db").recordings_dir == tmp_path / "data" / (
        "recordings"
    )
    assert recordings.bucket(Settings(db_path=tmp_path / "t.db")) is None
