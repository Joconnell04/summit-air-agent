"""Records each call with LiveKit egress (story O-6): one audio-only room recording, mixed by
LiveKit, uploaded as an MP4 to the S3-compatible bucket in the settings. LiveKit Cloud can't write
to our server's disk, so the tool server's jobs loop copies the finished file from the bucket into
its data folder and deletes it there (app/recordings.py). The server names the file; the worker
records only when the call's start data asks for it and a bucket is set.
"""

import logging

from livekit import api

from .server import ToolServer
from .settings import Settings

log = logging.getLogger("voice.recording")


def egress_request(room: str, object_key: str, settings: Settings) -> api.RoomCompositeEgressRequest:
    """Audio only, with no layout, so it is billed at the audio rate."""
    upload = api.S3Upload(
        access_key=settings.recording_access_key,
        secret=settings.recording_secret,
        region=settings.recording_region,
        endpoint=settings.recording_endpoint,
        bucket=settings.recording_bucket,
        # S3-compatible stores other than AWS (R2, MinIO) want the bucket in the path.
        force_path_style=bool(settings.recording_endpoint),
    )
    output = api.EncodedFileOutput(
        file_type=api.EncodedFileType.MP4, filepath=object_key, s3=upload, disable_manifest=True
    )
    return api.RoomCompositeEgressRequest(room_name=room, audio_only=True, file_outputs=[output])


async def start(
    lk: api.LiveKitAPI, server: ToolServer, room: str, recording: dict | None, settings: Settings
) -> str | None:
    """Start recording the room and tell the server the file name. None when the call isn't to be
    recorded or the recorder refused (a full plan, a bad bucket); the call goes on either way."""
    if not recording or not settings.recording_bucket:
        return None
    try:
        info = await lk.egress.start_room_composite_egress(
            egress_request(room, recording["object_key"], settings)
        )
    except api.TwirpError as exc:
        log.warning("call %s not recorded: %s", room, exc.message)
        return None
    await server.recording(info.egress_id, recording["object_key"])
    return info.egress_id


async def stop(lk: api.LiveKitAPI, egress_id: str | None) -> None:
    """Stop when the agent leaves. A recording also stops by itself when the room empties, so an
    egress that has already ended is not an error."""
    if not egress_id:
        return
    try:
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
    except api.TwirpError as exc:
        log.info("egress %s already stopped: %s", egress_id, exc.message)
