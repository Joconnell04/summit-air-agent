"""The caller's side of a LiveKit room, for the two entrants whose calls run in one: our LiveKit
stack (its own project) and Retell web calls (Retell's LiveKit project). It publishes the rig's
caller audio as a microphone track and hands every frame of the agent's audio to the listener.

Uses only the `livekit` client package, not a voice folder, so either voice layer can be deleted
on its own.
"""

import asyncio
import json

from evals.audio_rig.audio import dbfs

AGENT_STATE = "lk.agent.state"  # set by LiveKit agents: listening, thinking, speaking
# A room track streams all the time, silence included, and an agent can publish two (our worker's
# voice and its bridge clips). Frames this quiet are left out, so only sound reaches the listener,
# each frame at the time it arrived.
SILENT_DBFS = -60.0
# The audio source's buffer. Small, so a stall on our side can't leave the caller's words queued
# for long; what is queued is reported to the rig through `queued_s`.
QUEUE_MS = 100


class RoomPhone:
    def __init__(self, in_rate: int, out_rate: int = 24000):
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.room = None
        self.source = None
        self.listener = None
        self.readers: list[asyncio.Task] = []
        self.data: list[dict] = []

    async def connect(self, url: str, token: str, listener) -> None:
        from livekit import rtc

        self.listener = listener
        self.room = rtc.Room()
        self.room.on("track_subscribed", self._track)
        self.room.on("participant_attributes_changed", self._attributes)
        self.room.on("data_received", self._data)
        self.room.on("disconnected", lambda *_: listener.hung_up())
        self.room.on("participant_disconnected", lambda *_: listener.hung_up())
        await self.room.connect(url, token, rtc.RoomOptions(auto_subscribe=True))
        self.source = rtc.AudioSource(self.in_rate, 1, queue_size_ms=QUEUE_MS)
        track = rtc.LocalAudioTrack.create_audio_track("caller", self.source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(track, options)

    def _track(self, track, _publication, _participant) -> None:
        from livekit import rtc

        if track.kind == rtc.TrackKind.KIND_AUDIO:
            self.readers.append(asyncio.create_task(self._read(track)))

    async def _read(self, track) -> None:
        from livekit import rtc

        stream = rtc.AudioStream(track, sample_rate=self.out_rate, num_channels=1)
        async for event in stream:
            pcm = bytes(event.frame.data)
            if dbfs(pcm) >= SILENT_DBFS:
                self.listener.audio(pcm, self.out_rate, live=True)

    def _attributes(self, changed: dict, _participant) -> None:
        if changed.get(AGENT_STATE) == "listening":
            self.listener.done()

    def _data(self, packet) -> None:
        try:
            self.data.append(json.loads(bytes(packet.data)))
        except ValueError:
            return
        if self.data[-1].get("event_type") == "agent_stop_talking":
            # Retell's web call events (its web client SDK listens for the same ones).
            self.listener.done()

    def queued_s(self) -> float:
        return self.source.queued_duration if self.source is not None else 0.0

    async def send(self, pcm: bytes) -> None:
        from livekit import rtc

        frame = rtc.AudioFrame(pcm, self.in_rate, 1, len(pcm) // 2)
        await self.source.capture_frame(frame)

    async def close(self) -> None:
        if self.room is not None and self.room.isconnected():
            await self.room.disconnect()
        for task in self.readers:
            task.cancel()
        await asyncio.gather(*self.readers, return_exceptions=True)
