"""The worker's silence count (voice/dead_air.py, DECISIONS.md 081) on a short clock: a check-in
every check_in_s, the goodbye at hang_up_s, a reply starts it again, sound on the line holds a
check-in back but never the goodbye, and an aside on hold isn't a reply."""

import asyncio

import pytest
from livekit.agents import StopResponse
from livekit.agents.llm import ChatMessage

from tests.voice.test_voice_call import make_call
from voice.dead_air import CHECK_IN, GOODBYE, DeadAir, watch


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def run(timer: DeadAir, clock: FakeClock, busy=lambda: False, sound=lambda: False, limit=2000):
    """Runs watch() with a sleep that moves the fake clock; returns (time, what) for each line."""
    said = []

    async def sleep(seconds):
        clock.now += seconds
        if clock.now > limit:
            raise TimeoutError

    async def check_in():
        said.append((clock.now, CHECK_IN))

    async def goodbye():
        said.append((clock.now, GOODBYE))

    asyncio.run(watch(timer, busy, sound, check_in, goodbye, poll_s=1.0, sleep=sleep))
    return said


def test_check_ins_every_two_minutes_then_the_goodbye_at_eight():
    clock = FakeClock()
    said = run(DeadAir(120, 480, clock), clock)
    assert said == [(120, CHECK_IN), (240, CHECK_IN), (360, CHECK_IN), (480, GOODBYE)]


def test_a_reply_starts_the_count_again():
    clock = FakeClock()
    timer = DeadAir(120, 480, clock)
    clock.now = 200
    assert timer.due() == CHECK_IN
    timer.said(CHECK_IN)
    timer.reply()
    clock.now = 300
    assert timer.due() is None
    clock.now = 320
    assert timer.due() == CHECK_IN
    clock.now = 680
    assert timer.due() == GOODBYE


def test_nothing_is_said_while_the_agent_speaks():
    clock = FakeClock()
    said = run(DeadAir(120, 480, clock), clock, busy=lambda: clock.now < 500)
    assert said == [(500, GOODBYE)]


def test_sound_on_the_line_holds_a_check_in_back_but_not_the_goodbye():
    clock = FakeClock()
    # A TV on from 100 s to 300 s, then quiet, then on for good from 400 s.
    tv = lambda: 100 <= clock.now < 300 or clock.now >= 400
    said = run(DeadAir(120, 480, clock), clock, sound=tv)
    assert said == [(300, CHECK_IN), (480, GOODBYE)]


def test_the_timings_must_make_sense():
    with pytest.raises(ValueError):
        DeadAir(480, 120)


def caller(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=[text])


def agent_says(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=[text])


def test_an_aside_on_hold_gets_no_answer_and_doesnt_restart_the_count(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    clock = FakeClock()
    call.dead_air = DeadAir(120, 480, clock)
    asyncio.run(call.caller_line(caller("Hold on, let me grab my bill.")))
    clock.now = 100
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("Honey, where did you put the folder?")))
    assert call.session.said == []
    assert call.dead_air.quiet_for() == 100
    asyncio.run(call.caller_line(caller("Okay, I'm back.")))
    assert call.dead_air.quiet_for() == 0


def test_the_check_in_line_is_not_a_reply_but_the_agents_answer_is(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    clock = FakeClock()
    call.dead_air = DeadAir(120, 480, clock)
    call.timer_lines = {"Take your time, I'm still here."}
    clock.now = 130

    async def lines(*texts):
        for text in texts:
            call.agent_line(agent_says(text))
        await call.flush()

    asyncio.run(lines("Take your time, I'm still here."))
    assert call.dead_air.quiet_for() == 130
    asyncio.run(lines("Sure, go ahead."))
    assert call.dead_air.quiet_for() == 0


def test_the_worker_says_the_card_and_electrical_lines_word_for_word(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("Can I pay by card?")))
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("Also the unit is sparking.")))
    (card, card_cut), (sparks, sparks_cut) = call.session.said
    assert "card number" in card and "breaker" in sparks
    assert card_cut is sparks_cut is False


def test_a_running_tool_is_counted_so_the_call_isnt_silent(agent, connect):
    from prompts import assemble
    from tests.tool_server.toolkit import in_area_address
    from tests.voice.test_voice_tools import JOB_TYPES, Hangups, Voice
    from voice.livekit.bridge import Bridge
    from voice.livekit.tools import ToolLog, build_tools

    agent.start(call_id="call-1")
    log = ToolLog()
    server = connect("call-1")
    seen = []
    real = server.tool

    async def tool(*args, **kwargs):
        seen.append(log.running)
        return await real(*args, **kwargs)

    server.tool = tool
    built = build_tools(assemble.tool_specs(JOB_TYPES), server,
                        Bridge([], play=None, silent=lambda: True), log, Hangups(), Voice())
    tools = {t.info.name: t for t in built}
    asyncio.run(tools["read_back"]({"address": in_area_address(), "phone": "caller_id"}, None))
    assert seen == [1] and log.running == 0
