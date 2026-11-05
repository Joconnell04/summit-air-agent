"""Warm transfer in the worker (voice/livekit/transfer.py), against the real tool server and a fake
LiveKit: the server decides, the worker dials the person into a room of their own, gives them the
summary, and joins them to the caller only when they press 1. Nobody answering, or a voicemail,
brings the agent back with the callback time."""

import asyncio
import json

import pytest
from livekit import api

from prompts import assemble
from tests.tool_server.toolkit import UNKNOWN
from tests.voice.replies import no_reply
from voice.livekit.bridge import Bridge
from voice.livekit.tools import ToolLog, build_tools
from voice.livekit.transfer import PERSON, Transfer

OFFICE = "+12125550100"
TRUNK = "ST_outbound"
JOB_TYPES = ["no_heat", "no_cooling", "tune_up", "safety_check", "estimate"]


class Handle:
    """A speech handle that has already played."""

    async def wait_for_playout(self) -> None:
        return None


class Turn:
    speech_handle = Handle()


class Voice:
    def __init__(self):
        self.said = []

    def __call__(self, text):
        self.said.append(text)
        return Handle()


class Sip:
    def __init__(self, answers: bool):
        self.answers = answers
        self.requests = []

    async def create_sip_participant(self, request):
        self.requests.append(request)
        if not self.answers:
            raise api.TwirpError("unavailable", "no answer", status=480)


class Rooms:
    def __init__(self):
        self.moved = []
        self.deleted = []

    async def move_participant(self, request):
        self.moved.append(request)

    async def delete_room(self, request):
        self.deleted.append(request.room)


class LiveKit:
    def __init__(self, answers: bool = True):
        self.sip = Sip(answers)
        self.room = Rooms()


class Consult:
    """The room the person is dialed into. `presses` says whether they press 1 after the summary."""

    def __init__(self, name: str, presses: bool):
        self.name = name
        self.presses = presses
        self.said = []
        self.events: dict[str, asyncio.Event] = {}
        self.closed = False

    async def connect(self) -> None:
        return None

    def listen_for(self, digit):
        return self.events.setdefault(digit, asyncio.Event())

    async def say(self, text):
        self.said.append(text)
        if self.presses:
            self.events["1"].set()

    async def close(self):
        self.closed = True


class Worker:
    """The pieces of the worker a transfer touches."""

    def __init__(self, server, answers=True, presses=True, trunk=TRUNK):
        self.voice = Voice()
        self.lk = LiveKit(answers)
        self.consults = []
        self.holds = []
        self.handed_over = False

        def open_consult(name):
            self.consults.append(Consult(name, presses))
            return self.consults[-1]

        async def hand_over():
            self.handed_over = True

        self.transfer = Transfer(
            server, self.lk, "call-1", trunk, open_consult, self.voice, self.holds.append, hand_over
        )
        bridge = Bridge([], play=None, silent=lambda: True)
        built = build_tools(
            assemble.tool_specs(JOB_TYPES), server, bridge, ToolLog(), None, self.voice,
            self.transfer,
        )
        self.tools = {tool.info.name: tool for tool in built}

    def flag(self) -> str | None:
        args = {"reason": "Caller wants to talk to someone"}
        return asyncio.run(self._flag_and_run(args))

    async def _flag_and_run(self, args):
        reply = await self.tools["flag_person"](args, Turn())
        if self.transfer.task:
            self.outcome = await self.transfer.task
        return reply


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)


def asked_for_a_person(agent):
    agent.start(UNKNOWN, call_id="call-1")
    agent.turn("call-1", "Can I talk to a real person please?")


def status(agent) -> str | None:
    conn = agent.db()
    row = conn.execute("SELECT status FROM transfers WHERE call_id = 'call-1'").fetchone()
    conn.close()
    return row["status"] if row else None


def test_a_person_who_presses_1_is_joined_to_the_caller_and_the_agent_leaves(
    agent, connect, numbers
):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"))
    # The model says nothing more: the worker speaks from here.
    assert no_reply(worker.flag())
    assert worker.outcome == "connected"
    assert worker.voice.said == ["I'm connecting you with someone from our team now."]
    assert worker.holds == [True]
    dialed = worker.lk.sip.requests[0]
    assert (dialed.sip_call_to, dialed.sip_trunk_id) == (OFFICE, TRUNK)
    assert dialed.room_name == "call-1-transfer" and dialed.wait_until_answered
    assert dialed.ringing_timeout.seconds == 20
    # The person hears a line with no caller details, presses 1, then hears who it is, all in
    # their own room.
    consult = worker.consults[0]
    assert consult.said[0] == "Summit Air has a caller for the office. Press 1 to take the call."
    assert consult.said[1].startswith("Caller at 212, 555, 0999:")
    moved = worker.lk.room.moved[0]
    assert (moved.room, moved.identity, moved.destination_room) == (
        "call-1-transfer",
        PERSON,
        "call-1",
    )
    assert worker.handed_over
    assert status(agent) == "connected"


def test_nobody_answers_and_the_agent_comes_back_with_the_callback_time(agent, connect, numbers):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"), answers=False)
    assert no_reply(worker.flag())
    assert worker.outcome == "no_answer"
    assert worker.holds == [True, False]
    assert worker.voice.said[-1] == (
        "I couldn't reach anyone just now, so I've let the team know and someone will call you "
        "by 9:30 AM."
    )
    assert not worker.handed_over and not worker.lk.room.moved
    assert worker.lk.room.deleted == ["call-1-transfer"]
    assert status(agent) == "no_answer"


def test_a_voicemail_that_never_presses_1_is_not_put_through(agent, connect, numbers):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"), presses=False)
    asyncio.run(_shorter_wait(worker))
    assert worker.outcome == "not_accepted"
    assert not worker.lk.room.moved
    # A voicemail greeting only ever gets the line that names no caller.
    assert worker.consults[0].said == [
        "Summit Air has a caller for the office. Press 1 to take the call."
    ]
    # The person's leg is hung up, and the caller hears the callback time.
    assert worker.lk.room.deleted == ["call-1-transfer"]
    assert worker.voice.said[-1].endswith("someone will call you by 9:30 AM.")
    assert status(agent) == "not_accepted"


async def _shorter_wait(worker):
    original = worker.transfer.server.transfer

    async def quick(flag_id, callback_id):
        asked = await original(flag_id, callback_id)
        asked["data"]["transfer"]["accept_seconds"] = 0.01
        return asked

    worker.transfer.server.transfer = quick
    await worker._flag_and_run({"reason": "Caller wants to talk to someone"})


def test_with_no_number_set_the_model_reads_the_flag_as_before(agent, connect):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"))
    reply = asyncio.run(
        worker.tools["flag_person"](
            {"reason": "Caller wants to talk to someone", "callback_time": "11 AM"}, Turn()
        )
    )
    assert json.loads(reply)["say"] == "I've let our team know, and someone will call you around 11 AM."
    assert worker.transfer.task is None and not worker.lk.sip.requests


def test_with_no_outbound_trunk_nothing_is_dialed(agent, connect, numbers):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"), trunk="")
    reply = worker.flag()
    assert json.loads(reply)["ok"] is True
    assert worker.transfer.task is None and status(agent) is None


def test_an_error_mid_transfer_takes_the_caller_off_hold_with_the_callback_time(
    agent, connect, numbers
):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"))

    async def broken():
        raise RuntimeError("room service unavailable")

    worker.transfer.hand_over = broken
    assert no_reply(worker.flag())
    assert worker.outcome == "failed"
    # Back off hold, and the flag's own sentence: the callback stored before anyone was dialed.
    assert worker.holds[-1] is False
    assert worker.voice.said[-1] == "I've let our team know, and someone will call you by 9:30 AM."


def test_an_error_before_the_report_tells_the_server_it_failed(agent, connect, numbers):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"))
    holds = worker.transfer.hold

    def hold(on):
        holds(on)
        if on:
            raise RuntimeError("audio output gone")

    worker.transfer.hold = hold
    assert no_reply(worker.flag())
    assert worker.outcome == "failed"
    assert worker.holds == [True, False]
    assert status(agent) == "failed"
    assert not worker.lk.sip.requests


class Ringing(Sip):
    """A phone that rings until the call is stopped."""

    async def create_sip_participant(self, request):
        self.requests.append(request)
        await asyncio.sleep(3600)


def test_a_caller_who_hangs_up_mid_transfer_stops_the_dial(agent, connect, numbers):
    asked_for_a_person(agent)
    worker = Worker(connect("call-1"))
    worker.lk.sip = Ringing(True)

    async def hang_up_while_ringing():
        await worker.tools["flag_person"]({"reason": "Caller wants to talk to someone"}, Turn())
        while not worker.lk.sip.requests:
            await asyncio.sleep(0.01)
        worker.transfer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker.transfer.task

    asyncio.run(hang_up_while_ringing())
    # The person's leg is hung up, and the caller, who has gone, hears nothing more.
    assert worker.lk.room.deleted == ["call-1-transfer"]
    assert worker.consults[0].closed
    assert worker.voice.said == ["I'm connecting you with someone from our team now."]
