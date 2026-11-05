"""One call's lines, posted in order to the real tool server; safety lines spoken from the server's
text, never generated (spec S-17)."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml
from livekit.agents import Agent, StopResponse
from livekit.agents.llm import ChatMessage

from app.tenant import load_agent
from app.trace import as_text, timeline
from prompts.assemble import FOLLOW_UP_NOTES, SpokenReply, greeting
from tests.tool_server.toolkit import TENANT_DIR
from voice.livekit.bridge import Bridge
from voice.livekit.call import Call, SummitAgent, goodbye_grace
from voice.livekit.server import ToolServer
from voice.livekit.settings import load_voice
from voice.livekit.tools import ToolLog
from voice.livekit.worker import leave_voicemail, turn_handling

SAFETY = load_agent(TENANT_DIR)[0]["safety"]
GAS_LINES = " ".join(SAFETY["kinds"]["gas"]["lines"].split())
ENDPOINTING = load_voice()["endpointing"]


class Session:
    """Stands in for the LiveKit session: records what the worker asked it to do."""

    def __init__(self):
        self.said: list[tuple[str, bool]] = []
        self.endpointing: list[dict] = []
        self.replies: list[dict] = []

    def say(self, text, allow_interruptions=True):
        self.said.append((text, allow_interruptions))

    def update_options(self, endpointing_opts):
        self.endpointing.append(endpointing_opts)

    def generate_reply(self, **kwargs):
        self.replies.append(kwargs)


def make_call(connect, call_id="call-1") -> Call:
    bridge = Bridge([], play=None, silent=lambda: True)
    call = Call(connect(call_id), bridge, ToolLog(), ENDPOINTING)
    call.session = Session()
    return call


def caller(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=[text])


def agent_says(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=[text])


def test_a_gas_smell_makes_the_worker_say_the_servers_lines_word_for_word(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    with pytest.raises(StopResponse):
        asyncio.run(
            call.caller_line(caller("It kind of smells like gas in here, and the heat's out"))
        )
    assert call.session.said == [(GAS_LINES, False)]


def test_the_lines_count_as_said_once_the_agent_line_is_posted(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("I smell gas")))

    async def spoken():
        call.agent_line(agent_says(call.session.said[0][0]))
        await call.flush()

    asyncio.run(spoken())
    conn = agent.db()
    state = conn.execute("SELECT safety_state FROM calls WHERE id = 'call-1'").fetchone()[0]
    conn.close()
    assert state == "lines_said"


def test_an_ordinary_line_goes_to_the_model(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    message = caller("My furnace is making a banging noise, and we have a gas furnace.")
    asyncio.run(call.caller_line(message))
    assert call.session.said == []
    assert message.text_content.startswith("My furnace")


def test_a_business_caller_gets_a_server_note_for_the_model(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    message = caller("Hi, I run a restaurant in Park Slope and our AC is out.")
    asyncio.run(call.caller_line(message))
    assert "[server]" in message.text_content and "commercial" in message.text_content


def test_lines_are_posted_in_the_order_they_happened(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)

    async def conversation():
        call.agent_line(agent_says("Thanks for calling, how can I help?"))
        await call.caller_line(caller("The heat's out."))
        call.agent_line(agent_says("Oh no. When did it stop?"))
        await call.caller_line(caller("Last night."))
        await call.flush()

    asyncio.run(conversation())
    conn = agent.db()
    rows = conn.execute(
        "SELECT speaker FROM turns WHERE call_id = 'call-1' ORDER BY seq"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["agent", "caller", "agent", "caller"]


def test_the_wait_is_longer_right_after_asking_for_a_number(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)

    async def run():
        call.agent_line(agent_says("What's the best number to reach you?"))
        call.agent_line(agent_says("Got it. And what's going on with the heat?"))
        await call.flush()

    asyncio.run(run())
    assert call.session.endpointing == [ENDPOINTING["digits"], ENDPOINTING["normal"]]
    assert ENDPOINTING["digits"]["min_delay"] > ENDPOINTING["normal"]["min_delay"]


def test_the_normal_wait_comes_back_after_the_digits_on_a_real_livekit_session(connect):
    """LiveKit's update_options writes into the options dict it is given. If the worker passes
    its own settings, the first digits question overwrites the normal wait and every caller turn
    after it waits the long digits wait for the rest of the call (MEASURED on the audio rig,
    evals/rounds/bakeoff-audio-02.md)."""
    from livekit.agents import AgentSession

    voice = load_voice()
    endpointing = voice["endpointing"]
    normal = dict(endpointing["normal"])
    call = Call(connect("call-1"), Bridge([], play=None, silent=lambda: True), ToolLog(),
                endpointing)

    async def run():
        call.session = AgentSession(turn_handling=turn_handling(voice))
        call.agent_line(agent_says("What's the ZIP code there?"))
        call.agent_line(agent_says("Got it. And how cold is it inside?"))
        await call.flush()

    asyncio.run(run())
    assert endpointing["normal"] == normal
    live = call.session._opts.endpointing
    assert (live["min_delay"], live["max_delay"]) == (normal["min_delay"], normal["max_delay"])


def test_every_agent_line_carries_its_stage_timings(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.caller_stopped_at = 100.0
    call.tool_log.ms = 42.0
    message = agent_says("I've got Tuesday morning open.")
    message.metrics = {"started_speaking_at": 101.1, "llm_node_ttft": 0.5, "tts_node_ttfb": 0.15}

    async def run():
        call.agent_line(message)
        await call.flush()

    asyncio.run(run())
    conn = agent.db()
    timings = conn.execute(
        "SELECT timings_json FROM turns WHERE call_id = 'call-1' AND speaker = 'agent'"
    ).fetchone()[0]
    conn.close()
    assert '"reply_ms": 1100.0' in timings and '"tool_ms": 42.0' in timings


def agent_timings_rows(agent) -> list[dict]:
    conn = agent.db()
    rows = conn.execute(
        "SELECT timings_json FROM turns WHERE call_id = 'call-1' AND speaker = 'agent' ORDER BY id"
    ).fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def test_only_the_first_line_after_the_caller_carries_the_reply_delay(agent, connect):
    """On a tool turn the agent can speak twice ("Let me check", then the answer). The caller
    waited for the first; the second line timed from the caller's end read up to 11 s too high
    (exploration 014)."""
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.caller_stopped_at = 100.0
    first = agent_says("Let me check what we have.")
    first.metrics = {"started_speaking_at": 101.2}
    second = agent_says("I've got Tuesday morning open.")
    second.metrics = {"started_speaking_at": 104.9}

    async def run():
        call.agent_line(first)
        call.agent_line(second)
        await call.flush()

    asyncio.run(run())
    rows = agent_timings_rows(agent)
    assert rows[0]["reply_ms"] == 1200.0
    assert "reply_ms" not in rows[1]


def test_the_caller_lines_trip_to_the_server_is_timed_on_the_reply(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    heard = caller("my heat is out")
    heard.metrics = {"stopped_speaking_at": 100.0}
    reply = agent_says("Sorry to hear that. When did it stop?")
    reply.metrics = {"started_speaking_at": 101.3}

    async def run():
        await call.caller_line(heard)
        call.agent_line(reply)
        await call.flush()

    asyncio.run(run())
    (row,) = agent_timings_rows(agent)
    assert row["reply_ms"] == 1300.0
    assert 0 < row["server_ms"] < 5000


def test_the_trace_shows_whether_the_prompt_cache_was_hit(agent, connect):
    agent.start(call_id="call-1")
    call = make_call(connect)
    call.llm_log.record(
        SimpleNamespace(
            type="llm_metrics",
            cancelled=False,
            prompt_tokens=9400,
            prompt_cached_tokens=9300,
            cache_creation_tokens=40,
            completion_tokens=30,
        )
    )

    async def run():
        call.agent_line(agent_says("I've got Tuesday morning open."))
        await call.flush()

    asyncio.run(run())
    conn = agent.db()
    timings = json.loads(
        conn.execute(
            "SELECT timings_json FROM turns WHERE call_id = 'call-1' AND speaker = 'agent'"
        ).fetchone()[0]
    )
    conn.close()
    assert timings["cache_read_tokens"] == 9300 and timings["cache_write_tokens"] == 40
    assert "cache_read_tokens" in as_text(timeline(agent.db(), "call-1"))


def test_the_greeting_names_a_single_matched_customer(scenarios, agent):
    started = agent.start(scenarios["repeatCallerOpenJob"]["phone"])["data"]
    lines = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())["voice"]
    first = started["customers"][0]["name"].split()[0]
    assert greeting(started, lines) == lines["greeting_known"].format(first_name=first)
    assert greeting(agent.start()["data"], lines) == lines["greeting_unknown"]


def test_a_voicemail_at_capacity_flags_a_person_to_call_back(agent, connect):
    agent.start(call_id="call-1")
    result = asyncio.run(
        leave_voicemail(connect("call-1"), "Hi it's Dana at 12 Union St, no heat, call me back", 60)
    )
    assert result["ok"] is True, result
    conn = agent.db()
    reason = conn.execute("SELECT reason FROM flags WHERE call_id = 'call-1'").fetchone()[0]
    conn.close()
    assert reason.startswith("Voicemail while every line was busy: Hi it's Dana")


def test_a_server_that_stops_answering_mid_call_ends_the_call_with_the_safety_line():
    def down(request):
        raise httpx.ConnectError("refused")

    server = ToolServer("http://tool-server", "call-1", transport=httpx.MockTransport(down))
    call = Call(server, Bridge([], play=None, silent=lambda: True), ToolLog(), ENDPOINTING)
    call.session = Session()
    ended = []
    call.server_down = lambda: ended.append(True)
    with pytest.raises(StopResponse):
        asyncio.run(call.caller_line(caller("I smell gas")))
    assert ended == [True]


def test_the_reply_audio_waits_for_a_bridge_clip_to_finish(monkeypatch):
    events = []

    async def clip(_path):
        events.append("clip starts")
        await asyncio.sleep(0.1)
        events.append("clip ends")

    async def fake_tts(_agent, _text, _settings):
        for n in range(2):
            yield f"frame {n}"

    monkeypatch.setattr(Agent.default, "tts_node", fake_tts)

    async def run():
        bridge = Bridge(["a"], clip, lambda: True, after_s=0.01)
        call = Call(None, bridge, ToolLog(), ENDPOINTING)
        agent = SummitAgent(call, "", [])

        async def slow_tool():
            await asyncio.sleep(0.03)

        async def reply():
            yield "Okay, I found it."

        await bridge.run(slow_tool())
        async for frame in agent.tts_node(reply(), None):
            events.append(frame)

    asyncio.run(run())
    assert events == ["clip starts", "clip ends", "frame 0", "frame 1"]


def test_the_worker_stops_the_recording_when_the_caller_objects(agent, connect):
    """Two-party consent on: the server reads the objection and the worker stops the recorder."""
    from app import owner_settings

    conn = agent.db()
    try:
        tenant = agent.app.state.services.tenants.default
        owner_settings.change(conn, tenant, {"two_party_consent": True}, "Pat", agent.clock())
    finally:
        conn.close()
    agent.start(call_id="call-1")
    call = make_call(connect)
    stopped = []

    async def stop() -> None:
        stopped.append(True)

    call.stop_recording = stop

    async def run(text: str) -> None:
        await call.caller_line(caller(text))
        await asyncio.sleep(0)

    asyncio.run(run("Please don't record this. The AC is out."))
    assert stopped == [True]
    assert call.session.said == [("Okay, I've turned the recording off.", True)]
    asyncio.run(run("The AC is still out."))
    assert stopped == [True], "once"
    assert len(call.session.said) == 1
# Eval round 3: the caller hears the server's read-back sentence alone, never a lead-in.


def _stream(*parts):
    """A model reply as the LLM node streams it: text, then any tool calls."""
    from livekit.agents import llm

    async def fake_llm(_agent, _ctx, _tools, _settings):
        for part in parts:
            if isinstance(part, str):
                yield llm.ChatChunk(id="c", delta=llm.ChoiceDelta(content=part))
            else:
                name, args = part
                call = llm.FunctionToolCall(name=name, arguments=json.dumps(args), call_id="t1")
                yield llm.ChatChunk(id="c", delta=llm.ChoiceDelta(tool_calls=[call]))

    return fake_llm


def _heard(monkeypatch, reply_setup, *parts) -> tuple[str, list[str]]:
    from livekit.agents import llm

    monkeypatch.setattr(Agent.default, "llm_node", _stream(*parts))
    call = Call(None, LeadIns(), ToolLog(), ENDPOINTING)
    call.session = Session()
    reply_setup(call.reply)
    agent = SummitAgent(call, "", [])

    async def run():
        text, tools = [], []
        async for chunk in agent.llm_node(None, [], None):
            if isinstance(chunk, str):
                text.append(chunk)
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta:
                text.append(chunk.delta.content or "")
                tools += [t.name for t in chunk.delta.tool_calls]
        return "".join(text), tools

    text, tools = asyncio.run(run())
    return text.strip(), tools


def _at_booking(reply):
    reply.tool_done("check_service_area", {"ok": True})


def test_a_reply_that_calls_read_back_drops_the_text_before_it(monkeypatch):
    text, tools = _heard(
        monkeypatch, _at_booking, "Got it. ", "Let me read that back.", ("read_back", {"phone": "x"})
    )
    assert text == ""
    assert tools == ["read_back"]


def test_a_reply_that_calls_another_tool_keeps_its_text(monkeypatch):
    text, tools = _heard(
        monkeypatch, _at_booking, "Let me see. ", ("find_slots", {"job_type": "no_heat"})
    )
    assert text == "Let me see."
    assert tools == ["find_slots"]


def test_confirming_the_read_back_keeps_the_text(monkeypatch):
    text, _ = _heard(
        monkeypatch, _at_booking, "Great.", ("read_back", {"phone": "x", "confirmed": True})
    )
    assert text == "Great."


def test_before_an_address_is_checked_text_streams_untouched(monkeypatch):
    text, _ = _heard(monkeypatch, lambda reply: None, "Sure. ", ("read_back", {"phone": "x"}))
    assert text == "Sure."


def test_on_a_safety_call_a_goodbye_before_the_sign_off_is_dropped(monkeypatch):
    def safety_call(reply):
        reply.heard({"action": "speak_lines"})

    text, _ = _heard(monkeypatch, safety_call, "Take care. ", ("end_call", {"reason": "x"}))
    assert text == ""
    text, _ = _heard(monkeypatch, safety_call, "Okay.")
    assert text == "Okay."



def _gave_address(reply):
    reply.heard(None, "it's three two two one avenue n brooklyn one one two three four")
    reply.heard(None, "six three one five five five oh one three three")


def test_a_read_back_announcement_is_dropped_from_any_reply(monkeypatch):
    """Eval round 3 run 20261104-024140, two-family-owner-residential: "I'll read back the
    details" in a reply that called find_slots."""
    text, _ = _heard(
        monkeypatch,
        lambda reply: None,
        "Thursday afternoon it is. I'll read back the details in a moment. ",
        "Which window works?",
        ("find_slots", {"job_type": "tune_up"}),
    )
    assert text == "Thursday afternoon it is. Which window works?"


def test_a_restated_zip_or_number_is_dropped_and_the_rest_kept(monkeypatch):
    text, _ = _heard(
        monkeypatch,
        _gave_address,
        "Got it, 3221 Avenue N, Brooklyn, 11234. ",
        "And the number is 631-555-0133. ",
        "Is the furnace working right now?",
    )
    assert text == "Is the furnace working right now?"


def test_a_reply_with_nothing_left_is_silent(monkeypatch):
    text, _ = _heard(
        monkeypatch, _gave_address, "Let me read that back. ", "Your ZIP is 11234."
    )
    assert text == ""


class LeadIns:
    """Stands in for the bridge: counts the holding lines the worker asked for."""

    def __init__(self):
        self.count = 0

    def lead_in(self) -> bool:
        self.count += 1
        return True


def _follow_ups(monkeypatch, reply_setup, *parts) -> tuple[str, list[dict]]:
    """What the caller heard, and the follow-up replies the worker asked the session for."""
    from livekit.agents import llm

    monkeypatch.setattr(Agent.default, "llm_node", _stream(*parts))
    call = Call(None, LeadIns(), ToolLog(), ENDPOINTING)
    call.session = Session()
    reply_setup(call.reply)
    agent = SummitAgent(call, "", [])
    _follow_ups.call = call

    async def run():
        text = []
        async for chunk in agent.llm_node(None, [], None):
            if isinstance(chunk, str):
                text.append(chunk)
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta:
                text.append(chunk.delta.content or "")
        return "".join(text).strip()

    return asyncio.run(run()), call.session.replies


def _asked_to_cancel(reply):
    reply.heard(None, "Yeah. I just need to cancel the tune up at one four two Main Street.")
    reply.heard(None, "Just didn't like it.")


def test_a_promise_with_no_tool_call_is_asked_again(monkeypatch):
    """Live call 1 (issue #149), turn 7: the model said it would look the visit up, the sentence
    naming the street was dropped as a restatement, no tool ran, and the caller heard "Got it."
    then 19 s of silence."""
    text, replies = _follow_ups(
        monkeypatch, _asked_to_cancel, "Got it. ", "I'll look up the tune-up at 142 Main Street now."
    )
    assert text == "Got it."
    assert len(replies) == 1
    assert "tool_choice" not in replies[0], "no tool is forced (PR #155 review)"
    assert replies[0]["instructions"] == FOLLOW_UP_NOTES["promised"]


def test_a_reply_the_caller_heard_nothing_of_is_asked_again(monkeypatch):
    text, replies = _follow_ups(monkeypatch, _gave_address, "Your ZIP is 11234.")
    assert text == ""
    assert [r["instructions"] for r in replies] == [FOLLOW_UP_NOTES["silent"]]


def test_a_promise_that_calls_its_tool_is_not_asked_again(monkeypatch):
    text, replies = _follow_ups(
        monkeypatch, _asked_to_cancel, "Got it. ", "One moment.",
        ("find_customer", {"phone": "+16784774024"}),
    )
    assert text == "Got it. One moment."
    assert replies == []


def test_a_reply_that_only_acknowledges_is_asked_to_take_the_next_step(monkeypatch):
    """The second live call (call-_+16784774024_MXJ5xsvpZQ5H): "Sure, I can set up an AC tune-up."
    and later "Yes.", each followed by about 20 s of silence."""
    for line in ("Sure, I can set up an AC tune-up.", "Yes.", "Got it."):
        _, replies = _follow_ups(monkeypatch, lambda reply: None, line)
        assert [r["instructions"] for r in replies] == [FOLLOW_UP_NOTES["stalled"]], line


def test_a_reply_whose_question_was_filtered_out_is_asked_to_take_the_next_step():
    """Live-like rig run on 5b57b92 (issue #150): the question was dropped and the restatement,
    said in words by the caller, stayed, so the reply asked nothing and the caller waited 21 s."""
    reply = SpokenReply()
    raw = "Got it, 248 Duffield Street. Is that the right address?"
    assert reply.dead_end(raw, "Got it, 248 Duffield Street.", []) == "stalled"
    reply = SpokenReply()
    raw = "Got it, 10009. Let me check that it's in our service area."
    assert reply.dead_end(raw, "Let me check that it's in our service area.", []) == "promised"
    checked = SpokenReply().dead_end(raw, "Let me check that it's in our service area.",
                                     [("check_service_area", {"zip": "10009"})])
    assert checked is None, "a promise that calls its tool goes on"


def test_a_question_or_a_plain_answer_is_not_asked_again(monkeypatch):
    for line in ("Let me check: what's the ZIP there?", "We're open until 8 tonight."):
        _, replies = _follow_ups(monkeypatch, lambda reply: None, line)
        assert replies == [], line


def test_only_one_follow_up_until_the_caller_speaks_again():
    reply = SpokenReply()
    assert reply.dead_end("Let me check on that.", "Let me check on that.", []) == "promised"
    assert reply.dead_end("Let me check on that.", "Let me check on that.", []) is None
    reply.heard(None, "Hello?")
    assert reply.dead_end("Let me check on that.", "Let me check on that.", []) == "promised"




HOLDING = ["Just give me one second.", "Let me pull that up.", "One moment."]


def _with_holding(setup):
    def apply(reply):
        reply.holding_lines = HOLDING
        setup(reply)

    return apply


def test_live_call_1_got_it_before_a_look_up_ends_with_a_holding_line(monkeypatch):
    """Issue #149: the sentence naming the street is dropped, so the caller heard "Got it." and then
    waited through the model's second step in silence."""
    text, replies = _follow_ups(
        monkeypatch, _with_holding(_asked_to_cancel), "Got it. ",
        "I'll look up the tune-up at 142 Main Street now.",
        ("find_customer", {"phone": "+16784774024"}),
    )
    assert text == "Got it. Just give me one second."
    assert replies == []


def test_a_look_up_after_the_models_own_words_gets_no_holding_line(monkeypatch):
    text, _ = _follow_ups(
        monkeypatch, _with_holding(_asked_to_cancel), "Okay, let me find that visit for you.",
        ("find_customer", {"phone": "+16784774024"}),
    )
    assert text == "Okay, let me find that visit for you."


def test_a_booking_gets_no_holding_line_since_its_confirmation_is_spoken_at_once(monkeypatch):
    text, _ = _follow_ups(
        monkeypatch, _with_holding(_gave_address), "Got it. ", ("book_appointment", {"slot_id": "x"})
    )
    assert text == "Got it."


def test_a_look_up_beside_a_read_back_gets_no_holding_line():
    """Must-pass run on PR #155, heat-and-sink-leak: "Just give me one second." came right before
    the server's read-back sentence, with no wait to fill."""
    reply = SpokenReply(HOLDING)
    assert reply.holding_line("", [("find_slots", {}), ("read_back", {"phone": "x"})]) == ""


def test_holding_lines_take_turns_and_never_repeat_back_to_back():
    reply = SpokenReply(HOLDING)
    look = [("find_slots", {})]
    said = [reply.holding_line("Okay.", look) for _ in range(4)]
    assert said == [*HOLDING, HOLDING[0]]
    assert SpokenReply(["One moment."]).holding_line("", look) == "One moment."
    single = SpokenReply(["One moment."])
    single.holding_line("", look)
    assert single.holding_line("", look) == ""

def test_the_line_drops_only_after_a_quiet_second_past_the_goodbye():
    """Issue #149: the call ended the instant the goodbye finished."""

    async def run():
        began = asyncio.get_running_loop().time()
        quiet = await goodbye_grace(0.2, lambda: False, poll_s=0.01)
        return quiet, asyncio.get_running_loop().time() - began

    quiet, waited = asyncio.run(run())
    assert quiet is True
    assert waited >= 0.2


def test_a_caller_who_speaks_after_the_goodbye_keeps_the_call_open():
    async def run():
        loop = asyncio.get_running_loop()
        began = loop.time()
        return await goodbye_grace(1.0, lambda: loop.time() - began > 0.05, poll_s=0.01)

    assert asyncio.run(run()) is False


def test_the_goodbye_pause_is_a_second_and_a_half():
    assert load_voice()["end_call_grace_s"] == 1.5



@pytest.mark.parametrize(
    "line",
    [
        "Someone will call you Friday around 2 PM. I'll let the team know.",
        "I'll get that to the technician. Goodbye.",
        "I'll send that over to the office.",
    ],
)
def test_a_closing_that_promises_something_later_is_not_a_dead_end(line):
    """PR #155 review: these read as "promised" and forced a tool call after a finished request."""
    reply = SpokenReply()
    reply.heard(None, "Okay, thanks.")
    assert reply.dead_end(line, line, []) is None


def test_after_a_booking_or_callback_went_through_nothing_is_asked_again():
    reply = SpokenReply()
    reply.heard(None, "Friday at two works.")
    reply.tool_done("flag_person", {"ok": True, "data": {}})
    assert reply.dead_end("Got it.", "Got it.", []) is None
    reply.heard(None, "And one more thing.")
    assert reply.dead_end("Got it.", "Got it.", []) == "stalled"



MISMATCH = (
    "I'm sorry, I'm not able to make that change from this number. I can have someone from our "
    "office call the number we have on file for it. Would that help?"
)


def test_live_call_2_the_refusal_is_said_once_when_the_model_already_said_it():
    """Live call 2 (issue #149), turns 17 and 18: the same refusal twice back to back. The model
    had the line from the linked call's turns and said it, then the worker spoke the server's."""
    reply = SpokenReply()
    reply.heard(None, "Yeah. That's correct.")
    assert reply.text(MISMATCH, []) == MISMATCH
    assert reply.already_said(MISMATCH)


def test_a_server_line_already_spoken_is_not_said_again_by_the_model():
    reply = SpokenReply()
    reply.heard(None, "Yeah. That's correct.")
    reply.server_said(MISMATCH)
    model = "I'm sorry, I'm not able to make that change from this number. Want a callback?"
    assert reply.text(model, []) == "Want a callback?"



def _call_2_zip(reply):
    reply.heard(None, "I moved from one four two Main Street. To seventy five Hoyt Street.")
    reply.tool_done("find_address", {"ok": True, "data": {"zip": "11201"}})
    reply.heard(None, "Yeah. But why do you say ZIP one one two zero one? Just say the ZIP code is.")


def test_live_call_2_turn_9_sorry_about_that_is_not_the_whole_reply(monkeypatch):
    """Live call 2 (issue #149), turn 9: "Sorry about that." and then nothing until the caller
    asked "Well, what's going on?" 23 s later. The ZIP sentence after it was dropped as a
    restatement, and the bare "Is that right?" with it."""
    text, replies = _follow_ups(
        monkeypatch, _call_2_zip, "Sorry about that. ",
        "The new address is 75 Hoyt Street, Brooklyn, ZIP code 11201. ",
        "Is that right?",
    )
    assert text == "Sorry about that."
    assert [r["instructions"] for r in replies] == [FOLLOW_UP_NOTES["stalled"]]


def test_live_call_2_turns_24_and_27_are_asked_to_go_on(monkeypatch):
    """Turn 24 "Sure, I can set up an AC tune-up." then 24 s of silence; turn 27 "Yes." then
    silence until "Hello?"."""
    for caller, line in (
        ("Yeah. I need to do my a c six as well.", "Sure, I can set up an AC tune-up. "),
        ("Okay. Are you gonna ask me a question?", "Yes. "),
    ):
        text, replies = _follow_ups(
            monkeypatch, lambda reply, c=caller: reply.heard(None, c), line
        )
        assert text == line.strip()
        assert [r["instructions"] for r in replies] == [FOLLOW_UP_NOTES["stalled"]], line


def test_the_known_facts_note_is_added_once_not_to_every_caller_line(agent, connect):
    """Realism suite on #155: the same "Already known" note on every caller line threw away
    LiveKit's early model request on every turn. It goes on the line where the facts change."""
    agent.start(call_id="call-1")
    call = make_call(connect)
    lines = [caller("My furnace is running but it's not getting warm."), caller("Since yesterday.")]

    async def conversation():
        await call.caller_line(lines[0])
        call.agent_line(agent_says("Since when?"))
        await call.caller_line(lines[1])
        await call.flush()

    asyncio.run(conversation())
    assert "Already known" in lines[0].text_content
    assert "[server]" not in lines[1].text_content


def test_the_servers_question_is_not_asked_again_after_the_model_asked_it():
    """Stripped rig check on #160, live-multi-book-then-callback: "And what name should I put the
    visit under? Thanks. What name should I put the visit under?" The server's line ends in the
    question the model just asked, so the worker says nothing more. A short question isn't
    matched this way."""
    reply = SpokenReply()
    reply.heard(None, "Yeah. That's correct.")
    assert reply.text("And what name should I put the visit under?", []) != ""
    assert reply.already_said("Thanks. And what name should I put the visit under?")
    assert not reply.already_said("Thanks. Is that right?")
