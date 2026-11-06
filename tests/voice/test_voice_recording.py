"""Recording each call with LiveKit egress (voice/archive/livekit/recording.py): one audio-only room
recording to the bucket under the name the server gave, reported back to the server, and stopped
when the agent leaves. A recorder that refuses never stops the call."""

import asyncio

from livekit import api

from tests.tool_server.toolkit import UNKNOWN
from voice.archive.livekit import recording
from voice.archive.livekit.settings import Settings

BUCKET = Settings(
    recording_bucket="summit-calls",
    recording_endpoint="https://account.r2.cloudflarestorage.com",
    recording_region="auto",
    recording_access_key="key",
    recording_secret="secret",
)


class Egress:
    def __init__(self, refuse: bool = False):
        self.refuse = refuse
        self.started = []
        self.stopped = []

    async def start_room_composite_egress(self, request):
        if self.refuse:
            raise api.TwirpError("resource_exhausted", "egress limit reached", status=429)
        self.started.append(request)
        return api.EgressInfo(egress_id="EG_123", room_name=request.room_name)

    async def stop_egress(self, request):
        self.stopped.append(request.egress_id)
        if self.refuse:
            raise api.TwirpError("failed_precondition", "egress already ended", status=412)


class LiveKit:
    def __init__(self, refuse: bool = False):
        self.egress = Egress(refuse)


def recordings(agent) -> list[dict]:
    conn = agent.db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM recordings")]
    conn.close()
    return rows


def test_the_request_records_audio_only_to_the_bucket_under_the_servers_name():
    request = recording.egress_request("call-1", "summit-air/call-1.mp4", BUCKET)
    assert request.room_name == "call-1"
    # Audio only and no layout keep it on the audio rate.
    assert request.audio_only and not request.layout and not request.custom_base_url
    (output,) = request.file_outputs
    assert output.file_type == api.EncodedFileType.MP4
    assert output.filepath == "summit-air/call-1.mp4"
    assert output.s3.bucket == "summit-calls"
    assert output.s3.endpoint == BUCKET.recording_endpoint
    assert output.s3.force_path_style


def test_recording_starts_and_the_server_is_told(agent, connect):
    agent.start(UNKNOWN, call_id="call-1")
    lk = LiveKit()
    wanted = {"object_key": "summit-air/call-1.mp4"}
    egress_id = asyncio.run(recording.start(lk, connect("call-1"), "call-1", wanted, BUCKET))
    assert egress_id == "EG_123"
    assert lk.egress.started[0].room_name == "call-1"
    (row,) = recordings(agent)
    assert (row["egress_id"], row["object_key"], row["status"]) == (
        "EG_123",
        "summit-air/call-1.mp4",
        "recording",
    )


def test_nothing_is_recorded_when_the_call_or_the_worker_says_no(agent, connect):
    agent.start(UNKNOWN, call_id="call-1")
    lk = LiveKit()
    wanted = {"object_key": "summit-air/call-1.mp4"}
    assert asyncio.run(recording.start(lk, connect("call-1"), "call-1", None, BUCKET)) is None
    assert asyncio.run(recording.start(lk, connect("call-1"), "call-1", wanted, Settings())) is None
    assert lk.egress.started == [] and recordings(agent) == []


def test_a_refused_recording_leaves_the_call_alone(agent, connect):
    agent.start(UNKNOWN, call_id="call-1")
    lk = LiveKit(refuse=True)
    wanted = {"object_key": "summit-air/call-1.mp4"}
    assert asyncio.run(recording.start(lk, connect("call-1"), "call-1", wanted, BUCKET)) is None
    assert recordings(agent) == []


def test_stop_ends_the_recording_and_an_ended_one_is_not_an_error():
    lk = LiveKit()
    asyncio.run(recording.stop(lk, "EG_123"))
    assert lk.egress.stopped == ["EG_123"]
    already = LiveKit(refuse=True)
    asyncio.run(recording.stop(already, "EG_123"))
    asyncio.run(recording.stop(already, None))
    assert already.egress.stopped == ["EG_123"]
