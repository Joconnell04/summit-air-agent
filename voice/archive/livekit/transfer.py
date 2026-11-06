"""Warm transfer to a person (C-11). The tool server decides whether a flagged call goes to a person
and writes every word said; this module dials, plays hold music, gives the person the summary, and
joins them to the caller, or brings the agent back when nobody takes the call.

The person is dialed into a room of their own through the outbound trunk, so the caller never
hears the ringing or the summary. When they pick up they hear one line that names no caller, since
a voicemail greeting may be what answered; only after they press 1 do they hear who is calling and
why, and are moved into the caller's room (LiveKit's MoveParticipant), and the agent leaves. A
voicemail box never presses 1, so it is never put through and never hears the caller's details. LiveKit's own WarmTransferTask joins the calls the same way, but there a model talking
to the person decides when to connect; here the key press does, and the server decides who is
called.

A caller who demands a person gets a cold handoff instead, decided by the server on the caller's
own turn: the worker says the server's one line and sends the caller's own call leg to the number
with a SIP transfer (a REFER through the inbound trunk), so there is no consult room, summary or
key press. If the transfer fails, the agent comes back with the callback time the server stored.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

import aiohttp
from livekit import api, rtc

from .server import ToolServer

log = logging.getLogger("voice.transfer")

PERSON = "person"
ACCEPT_DIGIT = "1"
# LiveKit gives up ringing at ring_seconds; this is only a backstop on the request itself.
DIAL_GRACE_S = 5.0
# A transfer still ringing when LiveKit answers has been accepted by the phone company, and the
# caller is on their way; only a failed one brings the agent back.
SENT = (api.SIPTransferStatus.STS_TRANSFER_SUCCESSFUL, api.SIPTransferStatus.STS_TRANSFER_ONGOING)


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
        await consult.say(plan["prompt"])
        try:
            await asyncio.wait_for(pressed.wait(), plan["accept_seconds"])
        except TimeoutError:
            return "not_accepted"
        await consult.say(plan["summary"])
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


async def send_through(lk: api.LiveKitAPI, caller_room: str, caller: str, plan: dict) -> str:
    """Send the caller's call to the person's number. Returns handed_off or failed."""
    request = api.TransferSIPParticipantRequest(
        room_name=caller_room,
        participant_identity=caller,
        transfer_to=f"tel:{plan['phone']}",
        play_dialtone=False,
    )
    request.ringing_timeout.FromSeconds(int(plan["ring_seconds"]))
    try:
        sent = await asyncio.wait_for(
            lk.sip.transfer_sip_participant(request), plan["ring_seconds"] + DIAL_GRACE_S
        )
    except (api.TwirpError, TimeoutError, aiohttp.ClientError) as exc:
        log.info("handoff %s not sent: %s", plan["id"], type(exc).__name__)
        return "failed"
    if sent.status not in SENT:
        log.info("handoff %s refused: %s", plan["id"], sent.reason)
        return "failed"
    return "handed_off"


class Transfer:
    """Asks the server after each person flag, and runs the transfer once the agent's turn has
    played. `hold(True)` mutes the conversation and plays hold music; `hand_over` ends the agent's
    part of the call without hanging up the caller. `caller` is the caller's participant identity,
    for a cold handoff; `ending` records why the call is ending, set before a SIP transfer because
    the caller leaves the room as it goes through."""

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
        caller: str = "",
        ending: Callable[[str], None] = lambda reason: None,
    ):
        self.server = server
        self.lk = lk
        self.caller_room = caller_room
        self.trunk_id = trunk_id
        self.open_consult = open_consult
        self.speak = speak
        self.hold = hold
        self.hand_over = hand_over
        self.caller = caller
        self.ending = ending
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

    def handoff(self, line, plan: dict, fallback: str) -> None:
        """The server sent a caller who demanded a person straight through; `line` is the handoff
        line's speech handle, already playing."""
        self.task = asyncio.create_task(self.run_handoff(line, plan, fallback))

    async def run_handoff(self, line, plan: dict, fallback: str) -> str:
        """Like `run`, never leaves the caller in silence: a failed transfer, or any error, takes
        them off hold with the callback sentence the server stored before anything was sent."""
        reported = False
        try:
            await line.wait_for_playout()
            self.hold(True)
            self.ending("transfer")
            outcome = (
                await send_through(self.lk, self.caller_room, self.caller, plan)
                if self.caller
                else "failed"
            )
            if outcome == "handed_off":
                # The caller has gone; the server is told and the agent leaves.
                await self.server.handoff_done(plan["id"], outcome)
                await self.hand_over()
                return outcome
            self.ending("caller_hangup")
            done = await self.server.handoff_done(plan["id"], outcome)
            reported = True
            self.hold(False)
            await self.speak(done["say"] or fallback).wait_for_playout()
            return outcome
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("handoff %s broke off; back to the caller", plan["id"])
            self.ending("caller_hangup")
            if not reported:
                await self.server.handoff_done(plan["id"], "failed")
            try:
                self.hold(False)
                await self.speak(fallback).wait_for_playout()
            except Exception:
                log.exception("handoff %s: could not take the caller off hold", plan["id"])
            return "failed"

    def cancel(self) -> None:
        """The caller hung up: stop dialing. The person's room is deleted on the way out."""
        if self.task and not self.task.done():
            self.task.cancel()

    async def run(self, turn, handoff: str, plan: dict, fallback: str) -> str:
        """Never leaves the caller on hold: any error takes them off it and says the flag's own
        callback sentence, which the server stored before anyone was dialed."""
        reported = False
        try:
            if turn is not None:
                await turn.wait_for_playout()
            await self.speak(handoff).wait_for_playout()
            self.hold(True)
            outcome = await dial_and_join(
                self.lk,
                self.open_consult(f"{self.caller_room}-transfer"),
                plan,
                self.caller_room,
                self.trunk_id,
            )
            done = await self.server.transfer_done(plan["id"], outcome)
            reported = True
            if outcome == "connected":
                await self.hand_over()
                return outcome
            self.hold(False)
            # The server's line names the callback time; if it couldn't be reached, the flag's own
            # sentence says the same.
            await self.speak(done["say"] or fallback).wait_for_playout()
            return outcome
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("transfer %s broke off; back to the caller", plan["id"])
            if not reported:
                await self.server.transfer_done(plan["id"], "failed")
            try:
                self.hold(False)
                await self.speak(fallback).wait_for_playout()
            except Exception:
                log.exception("transfer %s: could not take the caller off hold", plan["id"])
            return "failed"
