"""The outbound poller hands calls the server chose to a worker (journey 12); the worker reports
back. The server decides who is called and when; these check only the hand-off."""

import asyncio
import json

from app import jobs
from tests.tool_server.toolkit import UNKNOWN
from voice.archive.livekit.capacity import Lines
from voice.archive.livekit.outbound import poll_once


def due_callback(agent):
    """A caller who asked the system to call back in 30 minutes, 31 minutes later."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool(
        "flag_person",
        call,
        reason="Call me back about the noise",
        callback_in_minutes=30,
        system_calls_back=True,
    )
    assert flagged["ok"], flagged
    agent.end(call)
    agent.clock.advance(minutes=31)
    jobs.run_once(agent.app.state.services)


def test_a_due_callback_is_dispatched_to_a_worker_in_a_room_named_for_the_call(
    agent, connect, tmp_path
):
    due_callback(agent)
    dispatched = []

    async def dispatch(room, metadata):
        dispatched.append((room, json.loads(metadata)))

    claim = asyncio.run(poll_once(connect(""), Lines(tmp_path, 3), "summit-air", dispatch))
    [(room, meta)] = dispatched
    assert room == claim["call_id"] and meta["outbound_id"] == claim["outbound_id"]
    assert meta["phone"] == UNKNOWN and meta["opening_line"]
    # Claimed once: the next poll finds nothing.
    assert asyncio.run(poll_once(connect(""), Lines(tmp_path, 3), "summit-air", dispatch)) is None


def test_nothing_is_claimed_while_every_line_is_busy(agent, connect, tmp_path):
    due_callback(agent)
    lines = Lines(tmp_path, 1)
    lines.take("call-busy")
    dispatched = []

    async def dispatch(room, metadata):
        dispatched.append(room)

    assert asyncio.run(poll_once(connect(""), lines, "summit-air", dispatch)) is None
    assert dispatched == []
    lines.free("call-busy")
    assert asyncio.run(poll_once(connect(""), lines, "summit-air", dispatch)) is not None


def test_the_worker_reports_the_result_of_a_placed_call(agent, connect, tmp_path):
    due_callback(agent)
    claim = asyncio.run(
        poll_once(connect(""), Lines(tmp_path, 3), "w", lambda r, m: asyncio.sleep(0))
    )
    server = connect(claim["call_id"])
    started = asyncio.run(server.start(claim["phone"], claim["dialed_from"], "outbound"))
    assert started["ok"]
    done = asyncio.run(server.outbound_done(claim["outbound_id"], False, True))
    assert done["data"]["result"] == "voicemail"
