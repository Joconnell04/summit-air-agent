"""The caller's side of a plain WebRTC call (Retell's v3 web calls), with aiortc: the rig's caller
audio goes out as the microphone track, the agent's audio comes back to the listener frame by
frame as it arrives, and a `control` data channel carries the platform's call events.

The platform's signaling is the adapter's: `connect` takes a function that posts our offer and
returns the answer's SDP.
"""

import asyncio
import fractions
import json

from evals.audio_rig.audio import dbfs
from evals.audio_rig.room import SILENT_DBFS

ICE_WAIT_S = 1.0  # Retell's guidance: wait a second for candidates, then send the offer
MAX_QUEUED = 5


def ice_servers(raw: list[dict]):
    from aiortc import RTCIceServer

    return [
        RTCIceServer(urls=s.get("urls"), username=s.get("username"), credential=s.get("credential"))
        for s in raw
    ]


def caller_track(rate: int):
    from aiortc import MediaStreamTrack
    from av import AudioFrame

    class CallerTrack(MediaStreamTrack):
        """Hands aiortc each 20 ms frame the rig sends, at the rig's pace."""

        kind = "audio"

        def __init__(self):
            super().__init__()
            self.queue: asyncio.Queue = asyncio.Queue()
            self.pts = 0

        async def recv(self):
            pcm = await self.queue.get()
            samples = len(pcm) // 2
            frame = AudioFrame(format="s16", layout="mono", samples=samples)
            frame.planes[0].update(pcm)
            frame.sample_rate = rate
            frame.pts = self.pts
            frame.time_base = fractions.Fraction(1, rate)
            self.pts += samples
            return frame

    return CallerTrack()


class WebRtcPhone:
    def __init__(self, in_rate: int, out_rate: int = 24000):
        self.in_rate = in_rate
        self.out_rate = out_rate
        self.pc = None
        self.track = None
        self.listener = None
        self.reader = None
        self.events: list[dict] = []

    async def connect(self, servers: list[dict], post_offer, listener) -> None:
        from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

        self.listener = listener
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers(servers)))
        self.track = caller_track(self.in_rate)
        self.pc.addTrack(self.track)
        control = self.pc.createDataChannel("control")
        control.on("message", self._control)
        self.pc.on("track", self._remote)
        self.pc.on("connectionstatechange", self._state)
        await self.pc.setLocalDescription(await self.pc.createOffer())
        await asyncio.sleep(ICE_WAIT_S)
        answer = await post_offer(self.pc.localDescription.sdp)
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))

    def _control(self, message) -> None:
        try:
            event = json.loads(message)
        except (TypeError, ValueError):
            return
        self.events.append(event)
        if event.get("type") == "status" and event.get("state") in ("ended", "replaced"):
            self.listener.hung_up()

    def _state(self) -> None:
        if self.pc.connectionState in ("failed", "closed"):
            self.listener.hung_up()

    def _remote(self, track) -> None:
        if track.kind == "audio":
            self.reader = asyncio.create_task(self._read(track))

    async def _read(self, track) -> None:
        from aiortc.mediastreams import MediaStreamError
        from av import AudioResampler

        resampler = AudioResampler(format="s16", layout="mono", rate=self.out_rate)
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            for out in resampler.resample(frame):
                pcm = bytes(out.planes[0])[: out.samples * 2]
                if dbfs(pcm) >= SILENT_DBFS:
                    self.listener.audio(pcm, self.out_rate, live=True)

    async def send(self, pcm: bytes) -> None:
        # Until the connection is up nothing reads the track; keep only the newest frames so the
        # caller's words never go out late in a burst.
        while self.track.queue.qsize() >= MAX_QUEUED:
            self.track.queue.get_nowait()
        await self.track.queue.put(pcm)

    async def close(self) -> None:
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.pc is not None:
            await self.pc.close()
