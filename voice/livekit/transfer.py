"""Warm transfer to a person (C-11). The tool server decides whether a flagged call goes to a person
and writes every word said; this module dials, plays hold music, gives the person the summary, and
joins them to the caller, or brings the agent back when nobody takes the call.

The person is dialed into a room of their own through the outbound trunk, so the caller never
hears the ringing or the summary. Only after they press 1 are they moved into the caller's room
(LiveKit's MoveParticipant), and the agent leaves. A voicemail box never presses 1, so it is never
put through. LiveKit's own WarmTransferTask joins the calls the same way, but there a model talking
to the person decides when to connect; here the key press does, and the server decides who is
called.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from livekit import api, rtc

from .server import ToolServer

log = logging.getLogger("voice.transfer")

PERSON = "person"
ACCEPT_DIGIT = "1"
# LiveKit gives up ringing at ring_seconds; this is only a backstop on the request itself.
DIAL_GRACE_S = 5.0


class ConsultRoom:
    """The room the person is dialed into, apart from the caller. The agent joins it only to say
    the summary and hear the key press."""

    def __init__(self, name: str, url: str, tts):
        self.name = name
        self.url = url
        self.tts = tts
        self.room = rtc.Room()
        self.source: rtc.AudioSource | None = None
        self._pressed: dict[str, asyncio.Event] = {}

    async def connect(self) -> None:
        token = (
            api.AccessToken()
            .with_identity("agent")
            .with_kind("agent")
            .with_grants(
                api.VideoGrants(room_join=True, room=self.name, can_publish=True, can_subscribe=True)
            )
            .to_jwt()
        )
        self.room.on("sip_dtmf_received", self._digit)
        await self.room.connect(self.url, token)
        self.source = rtc.AudioSource(self.tts.sample_rate, self.tts.num_channels)
        track = rtc.LocalAudioTrack.create_audio_track("summary", self.source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(track, options)

    def _digit(self, dtmf: rtc.SipDTMF) -> None:
        if dtmf.digit in self._pressed:
            self._pressed[dtmf.digit].set()

    def listen_for(self, digit: str) -> asyncio.Event:
        return self._pressed.setdefault(digit, asyncio.Event())

    async def say(self, text: str) -> None:
        async with self.tts.synthesize(text) as stream:
            async for audio in stream:
                await self.source.capture_frame(audio.frame)
        await self.source.wait_for_playout()

    async def close(self) -> None:
        await self.room.disconnect()


async def dial_and_join(
    lk: api.LiveKitAPI, consult, plan: dict, caller_room: str, trunk_id: str
) -> str:
    """Dial the person, give them the summary, and move them to the caller if they press 1.
    Returns the outcome the server is told: connected, no_answer, not_accepted or failed."""
    try:
        await consult.connect()
        pressed = consult.listen_for(ACCEPT_DIGIT)
        request = api.CreateSIPParticipantRequest(
            room_name=consult.name,
            sip_trunk_id=trunk_id,
            sip_call_to=plan["phone"],
            participant_identity=PERSON,
            wait_until_answered=True,
        )
        request.ringing_timeout.FromSeconds(int(plan["ring_seconds"]))
        try:
            await asyncio.wait_for(
                lk.sip.create_sip_participant(request), plan["ring_seconds"] + DIAL_GRACE_S
            )
        except (api.TwirpError, TimeoutError) as exc:
            log.info("transfer %s not answered: %s", plan["id"], type(exc).__name__)
            return "no_answer"
        await consult.say(plan["summary"])
        try:
            await asyncio.wait_for(pressed.wait(), plan["accept_seconds"])
        except TimeoutError:
            return "not_accepted"
        await lk.room.move_participant(
            api.MoveParticipantRequest(
                room=consult.name, identity=PERSON, destination_room=caller_room
            )
        )
        return "connected"
    except Exception:
        log.exception("transfer %s failed", plan["id"])
        return "failed"
    finally:
        with contextlib.suppress(Exception):
            await consult.close()
        # Hangs up a person still on the line when they weren't put through; empty once they were.
        with contextlib.suppress(Exception):
            await lk.room.delete_room(api.DeleteRoomRequest(room=consult.name))


class Transfer:
    """Asks the server after each person flag, and runs the transfer once the agent's turn has
    played. `hold(True)` mutes the conversation and plays hold music; `hand_over` ends the agent's
    part of the call without hanging up the caller."""

    def __init__(
        self,
        server: ToolServer,
        lk: api.LiveKitAPI,
        caller_room: str,
        trunk_id: str,
        open_consult: Callable[[str], object],
        speak: Callable[[str], object],
        hold: Callable[[bool], None],
        hand_over: Callable[[], Awaitable[None]],
    ):
        self.server = server
        self.lk = lk
        self.caller_room = caller_room
        self.trunk_id = trunk_id
        self.open_consult = open_consult
        self.speak = speak
        self.hold = hold
        self.hand_over = hand_over
        self.task: asyncio.Task | None = None

    async def offer(self, turn, flagged: dict) -> bool:
        """After flag_person returned ok. True when the call is going to a person, so the model
        says nothing more this turn; False leaves the flag's result to the model as before."""
        if not self.trunk_id:
            return False
        data = flagged["data"]
        asked = await self.server.transfer(data["flag_id"], data.get("callback_id"))
        plan = (asked.get("data") or {}).get("transfer") if asked["ok"] else None
        if not plan:
            return False
        self.task = asyncio.create_task(self.run(turn, asked["say"], plan, flagged["say"]))
        return True

    async def run(self, turn, handoff: str, plan: dict, fallback: str) -> str:
        if turn is not None:
            await turn.wait_for_playout()
        await self.speak(handoff).wait_for_playout()
        self.hold(True)
        outcome = await dial_and_join(
            self.lk, self.open_consult(f"{self.caller_room}-transfer"), plan, self.caller_room,
            self.trunk_id,
        )
        done = await self.server.transfer_done(plan["id"], outcome)
        if outcome == "connected":
            await self.hand_over()
            return outcome
        self.hold(False)
        # The server's line names the callback time; if it couldn't be reached, the flag's own
        # sentence says the same.
        await self.speak(done["say"] or fallback).wait_for_playout()
        return outcome
