"""Cold handoff in the worker (voice/livekit/call.py and transfer.py), against the real tool server
and a fake LiveKit: the server decides on the caller's turn, the worker says its one line and sends
the caller's own call leg to the number with a SIP transfer, with no consult room, summary or key
press. A failed transfer brings the agent back with the callback time, never silence."""

import asyncio
from types import SimpleNamespace

import pytest
from livekit import api
from livekit.agents import StopResponse
from livekit.agents.llm import ChatMessage

from tests.tool_server.toolkit import UNKNOWN
from voice.livekit.bridge import Bridge
from voice.livekit.call import Call
from voice.livekit.settings import load_voice
from voice.livekit.tools import ToolLog
from voice.livekit.transfer import Transfer

OFFICE = "+12125550100"
CALLER = "sip_caller"
DEMAND = "Put me through to a manager right now. This is ridiculous."
HANDOFF_LINE = "Transferring you to a person now."
CALLBACK = (
    "I couldn't reach anyone just now, so I've let the team know and someone will call you by "
    "9:30 AM."
)


class Handle:
    async def wait_for_playout(self) -> None:
        return None


class Session:
    def __init__(self):
        self.said: list[tuple[str, bool]] = []

    def say(self, text, allow_interruptions=True):
        self.said.append((text, allow_interruptions))
        return Handle()


class Sip:
    """`status` is what LiveKit answers the transfer with; `error` makes the request itself fail."""

    def __init__(self, status=api.SIPTransferStatus.STS_TRANSFER_SUCCESSFUL, error=None):
        self.status = status
        self.error = error
        self.transfers = []
        self.dialed = []

    async def transfer_sip_participant(self, request):
        self.transfers.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(status=self.status, reason="STR_REJECTED")

    async def create_sip_participant(self, request):
        self.dialed.append(request)


class Worker:
    """The pieces of the worker a handoff touches: the call, the transfer, the end reason."""

    def __init__(self, server, sip=None, caller=CALLER):
        self.session = Session()
        self.lk = SimpleNamespace(sip=sip or Sip(), room=SimpleNamespace())
        self.holds = []
        self.ended = {"reason": "caller_hangup"}
        self.handed_over = False

        async def hand_over():
            self.handed_over = True

        def speak(text):
            return self.session.say(text, allow_interruptions=False)

        def open_consult(name):
            raise AssertionError("a cold handoff never opens a consult room")

        self.transfer = Transfer(
            server, self.lk, "call-1", "ST_outbound", open_consult, speak, self.holds.append,
            hand_over, caller=caller, ending=lambda reason: self.ended.update(reason=reason),
        )
        self.call = Call(server, Bridge([], play=None, silent=lambda: True), ToolLog(),
                         load_voice()["endpointing"])
        self.call.session = self.session
        self.call.handoff = self.transfer.handoff

    def hear(self, text: str) -> str | None:
        """The caller's line; returns the outcome of any handoff it started."""

        async def run():
            with pytest.raises(StopResponse):
                await self.call.caller_line(ChatMessage(role="user", content=[text]))
            return await self.transfer.task if self.transfer.task else None

        return asyncio.run(run())


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)


def status(agent) -> str | None:
    conn = agent.db()
    row = conn.execute("SELECT status FROM handoffs WHERE call_id = 'call-1'").fetchone()
    conn.close()
    return row["status"] if row else None


def test_a_demand_is_sent_straight_to_the_office_with_a_sip_transfer(agent, connect, numbers):
    agent.start(UNKNOWN, call_id="call-1")
    worker = Worker(connect("call-1"))
    assert worker.hear(DEMAND) == "handed_off"
    # One line from the server, said word for word, then the transfer; the model says nothing.
    assert worker.session.said == [(HANDOFF_LINE, False)]
    sent = worker.lk.sip.transfers[0]
    assert (sent.room_name, sent.participant_identity, sent.transfer_to) == (
        "call-1",
        CALLER,
        f"tel:{OFFICE}",
    )
    assert sent.ringing_timeout.seconds == 20 and not sent.play_dialtone
    # Nobody is dialed into a room of their own, and nobody hears a summary.
    assert not worker.lk.sip.dialed
    assert worker.handed_over and worker.ended["reason"] == "transfer"
    assert status(agent) == "handed_off"


@pytest.mark.parametrize(
    "sip",
    [
        Sip(status=api.SIPTransferStatus.STS_TRANSFER_FAILED),
        Sip(error=api.TwirpError("unavailable", "REFER rejected", status=503)),
    ],
)
def test_a_failed_transfer_brings_the_agent_back_with_the_callback_time(
    agent, connect, numbers, sip
):
    agent.start(UNKNOWN, call_id="call-1")
    worker = Worker(connect("call-1"), sip=sip)
    assert worker.hear(DEMAND) == "failed"
    assert worker.session.said == [(HANDOFF_LINE, False), (CALLBACK, False)]
    assert worker.holds == [True, False]
    assert not worker.handed_over and worker.ended["reason"] == "caller_hangup"
    assert status(agent) == "failed"


def test_with_no_number_set_the_caller_hears_the_callback_line_and_nothing_is_sent(
    agent, connect
):
    agent.start(UNKNOWN, call_id="call-1")
    worker = Worker(connect("call-1"))
    assert worker.hear(DEMAND) is None
    assert worker.session.said == [
        ("I've let our team know, and someone will call you by 9:30 AM.", False)
    ]
    assert not worker.lk.sip.transfers
    assert status(agent) == "not_dialed"


def test_with_no_caller_to_transfer_the_agent_comes_back(agent, connect, numbers):
    agent.start(UNKNOWN, call_id="call-1")
    worker = Worker(connect("call-1"), caller="")
    assert worker.hear(DEMAND) == "failed"
    assert not worker.lk.sip.transfers
    assert worker.session.said[-1] == (CALLBACK, False)


def test_an_error_mid_handoff_takes_the_caller_off_hold_with_the_callback_line(
    agent, connect, numbers
):
    agent.start(UNKNOWN, call_id="call-1")
    worker = Worker(connect("call-1"))

    async def broken():
        raise RuntimeError("room service unavailable")

    worker.transfer.hand_over = broken
    assert worker.hear(DEMAND) == "failed"
    assert worker.holds[-1] is False
    assert worker.session.said[-1] == (
        "I've let our team know, and someone will call you by 9:30 AM.",
        False,
    )
    assert worker.ended["reason"] == "caller_hangup"


def test_a_calm_ask_goes_to_the_model(agent, connect, numbers):
    agent.start(UNKNOWN, call_id="call-1")
    worker = Worker(connect("call-1"))

    async def run():
        await worker.call.caller_line(
            ChatMessage(role="user", content=["Can I talk to a real person please?"])
        )

    asyncio.run(run())
    assert worker.session.said == [] and worker.transfer.task is None
