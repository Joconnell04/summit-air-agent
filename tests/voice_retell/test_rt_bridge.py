"""The bridge against the real tool server: a Retell call goes through the same lifecycle, gates
and logs as a LiveKit call."""

import json

import pytest
from fastapi.testclient import TestClient
from retell_kit import NOW_MS, SPOKEN, call_obj, event, inbound, signed, tool, utterance
from toolkit import DETAILS, in_area_address

from app.tools import TOOLS
from voice.retell.bridge import (
    HANGUP_GRACE_S,
    Bridge,
    ToolServer,
    build_app,
    end_reason,
    idempotency_key,
)
from voice.retell.ledger import Ledger


def rows(tool_app, sql, *params):
    conn = tool_app.state.services.connect()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


GREETING = utterance("agent", SPOKEN["greeting_unknown"], start=0.5)
HEAT_OUT = utterance("user", "Hi, my heat's out and it's freezing in here.", start=5.0)
# The server acts only on a ZIP the caller said (app/tools.py _zip_known).
WHERE = utterance("user", "It's 101 Union St, 11231.", start=9.0)


def booked_call(client, rid="abc"):
    """A Retell call that has confirmed its read-back, the way the model would drive it."""
    inbound(client, rid)
    asked = tool(
        client,
        "read_back",
        call_obj(rid, GREETING, HEAT_OUT, WHERE),
        address=in_area_address(),
        phone="caller_id",
    )
    assert asked["ok"], asked
    said = [GREETING, HEAT_OUT, WHERE, utterance("agent", asked["say"]), utterance("user", "Yes.")]
    confirmed = tool(
        client,
        "read_back",
        call_obj(rid, *said),
        address=in_area_address(),
        phone="caller_id",
        confirmed=True,
    )
    assert confirmed["ok"], confirmed
    return said


def test_the_inbound_webhook_starts_the_call_and_fills_the_prompt_and_greeting(client, tool_app):
    variables = inbound(client, "abc")["dynamic_variables"]
    assert variables["summit_call_id"] == "retell_abc"
    assert variables["instructions"] == "INSTRUCTIONS for Summit Air"
    assert variables["greeting"] == SPOKEN["greeting_unknown"]
    call = rows(tool_app, "SELECT * FROM calls WHERE id = ?", "retell_abc")[0]
    assert call["caller_id"] == "+12125550999"
    assert call["dialed_number"] == "+16783366730"


def test_tool_calls_reach_the_server_under_our_call_id_with_the_lines_said_so_far(client, tool_app):
    inbound(client, "abc")
    lines = call_obj("abc", GREETING, HEAT_OUT, WHERE)
    answer = tool(client, "check_service_area", lines, zip="11231")
    assert answer["ok"], answer
    logged = rows(tool_app, "SELECT call_id FROM tool_calls WHERE tool = 'check_service_area'")
    assert logged == [{"call_id": "retell_abc"}]
    turns = rows(tool_app, "SELECT speaker, text FROM turns WHERE call_id = 'retell_abc'")
    assert [t["speaker"] for t in turns] == ["agent", "caller", "caller"]


def test_the_read_back_gate_passes_because_the_callers_yes_is_posted_first(client, tool_app):
    said = booked_call(client)
    slots = tool(client, "find_slots", call_obj("abc", *said), job_type="tune_up")
    day = slots["data"]["days"][0]
    slot = {"date": day["date"], "window": day["windows"][0]}
    booking = {"job_type": "tune_up", "slot": slot, "details": DETAILS}
    first = tool(client, "book_appointment", call_obj("abc", *said), **booking)
    assert first["ok"], first
    # Retell retries a function on a network failure; the same key returns the same booking.
    again = tool(client, "book_appointment", call_obj("abc", *said), **booking)
    assert again["data"]["booking_id"] == first["data"]["booking_id"]
    assert len(rows(tool_app, "SELECT id FROM bookings WHERE call_id = 'retell_abc'")) == 1


def test_booking_without_the_callers_yes_is_refused(client):
    inbound(client, "abc")
    address = in_area_address()
    tool(
        client, "read_back", call_obj("abc", GREETING, HEAT_OUT, WHERE), address=address, phone="caller_id"
    )
    early = tool(
        client,
        "read_back",
        call_obj("abc", GREETING, HEAT_OUT, WHERE),
        address=address,
        phone="caller_id",
        confirmed=True,
    )
    assert early["refused"] == "gate_read_back"


def test_writes_carry_a_key_from_call_tool_and_arguments():
    a = idempotency_key("retell_abc", "book_appointment", {"slot": {"date": "2026-11-03"}})
    b = idempotency_key("retell_abc", "book_appointment", {"slot": {"date": "2026-11-03"}})
    c = idempotency_key("retell_abc", "book_appointment", {"slot": {"date": "2026-11-04"}})
    d = idempotency_key("retell_xyz", "book_appointment", {"slot": {"date": "2026-11-03"}})
    assert a == b and len({a, c, d}) == 3


def test_call_ended_posts_the_last_lines_and_ends_the_call(client, tool_app):
    said = booked_call(client)
    goodbye = utterance("agent", "Thanks for calling, stay warm.")
    ended = call_obj("abc", *said, goodbye, disconnection_reason="user_hangup")
    event(client, "call_ended", ended)
    event(client, "call_ended", ended)  # Retell retries webhooks
    call = rows(tool_app, "SELECT * FROM calls WHERE id = 'retell_abc'")[0]
    assert call["ended_at"] and call["end_reason"] == "caller_hangup"
    turns = rows(tool_app, "SELECT text FROM turns WHERE call_id = 'retell_abc' ORDER BY seq")
    assert [t["text"] for t in turns] == [line["content"] for line in [*said, goodbye]]


def test_a_turn_posted_from_a_webhook_carries_the_reply_delay(client, tool_app):
    inbound(client, "abc")
    reply = utterance("agent", "Oh no, let's get someone out to you.", start=8.4)
    event(client, "transcript_updated", call_obj("abc", GREETING, HEAT_OUT, reply))
    # The agent may still be talking, so its line waits for the next event.
    assert len(rows(tool_app, "SELECT 1 FROM turns WHERE call_id = 'retell_abc'")) == 2
    thanks = utterance("user", "Thank you.")
    event(client, "transcript_updated", call_obj("abc", GREETING, HEAT_OUT, reply, thanks))
    turns = rows(tool_app, "SELECT speaker, timings_json FROM turns WHERE call_id = 'retell_abc'")
    # The caller's line ends at 5.0 + 9 words * 0.3 - 0.05 = 7.65 s.
    assert json.loads(turns[2]["timings_json"]) == {"reply_ms": 750.0}


def test_a_gas_smell_makes_the_agent_say_the_safety_lines_now(client, retell, tool_app):
    inbound(client, "abc")
    gas = utterance("user", "Actually I smell gas in the basement.")
    event(client, "transcript_updated", call_obj("abc", GREETING, gas))
    assert len(retell.said) == 1
    rid, context = retell.said[0]
    lines_ = rows(tool_app, "SELECT safety_state FROM calls WHERE id = 'retell_abc'")
    assert rid == "abc" and lines_ == [{"safety_state": "signal"}]
    assert "Please get everyone out of the house now." in context
    # The model says them; the server marks them said only from the agent's words.
    spoken = utterance("agent", context.split("punctuation changed: ", 1)[1])
    okay = utterance("user", "Okay, we're going outside.")
    event(client, "transcript_updated", call_obj("abc", GREETING, gas, spoken, okay))
    state = rows(tool_app, "SELECT safety_state FROM calls WHERE id = 'retell_abc'")
    assert state == [{"safety_state": "lines_said"}]


def test_a_safety_line_that_arrives_with_a_tool_call_goes_into_its_result(client, retell):
    inbound(client, "abc")
    gas = utterance("user", "It smells like gas in here.")
    answer = tool(client, "check_service_area", call_obj("abc", GREETING, gas), zip="11231")
    assert answer["speak_first"].endswith(
        "Please get everyone out of the house now. Don't flip any switches or use the stove, and "
        "call 911 or the gas company once you're outside."
    )
    assert retell.said == []


def test_a_business_caller_gets_the_servers_note_without_cutting_in(client, retell):
    inbound(client, "abc")
    business = utterance("user", "I manage a restaurant on Atlantic Avenue and the AC died.")
    event(client, "transcript_updated", call_obj("abc", GREETING, business))
    assert retell.context == [("abc", "[server] commercial")]
    assert retell.said == []


def test_an_allowed_end_call_stops_the_call_after_the_goodbye(client, retell, later, tool_app):
    said = booked_call(client)
    bye = [*said, utterance("user", "That's all, thanks."), utterance("agent", "Take care, bye.")]
    answer = tool(client, "end_call", call_obj("abc", *bye), reason="resolved")
    assert answer["ok"] and answer["data"]["hang_up"]
    assert retell.stopped == []
    delay = later.pending[0][0]
    assert 3.0 < delay < 6.0
    later.run()
    assert retell.stopped == ["abc"]
    event(client, "call_ended", call_obj("abc", *bye, disconnection_reason="call_stopped"))
    call = rows(tool_app, "SELECT end_reason FROM calls WHERE id = 'retell_abc'")[0]
    assert call["end_reason"] == "agent_end"


def test_the_caller_speaking_after_end_call_keeps_the_call_open(client, retell, later):
    said = booked_call(client)
    bye = [*said, utterance("user", "That's all."), utterance("agent", "Bye now.")]
    tool(client, "end_call", call_obj("abc", *bye), reason="resolved")
    event(
        client,
        "transcript_updated",
        call_obj("abc", *bye, utterance("user", "Oh wait, one more thing")),
    )
    later.run()
    assert retell.stopped == []


def test_end_call_on_an_unsaid_safety_call_is_refused_and_nothing_hangs_up(client, retell, later):
    inbound(client, "abc")
    gas = utterance("user", "I smell gas.")
    event(client, "transcript_updated", call_obj("abc", GREETING, gas))
    answer = tool(client, "end_call", call_obj("abc", GREETING, gas), reason="resolved")
    assert answer["refused"] == "gate_safety"
    assert later.pending == [] and retell.stopped == []


def test_with_the_tool_server_down_the_agent_says_so_and_the_call_ends(tmp_path, retell, later):
    down = ToolServer(TestClient(build_app_down()))
    bridge = Bridge(
        "key_test_retell",
        down,
        Ledger(tmp_path / "b.db"),
        retell,
        instructions=lambda start: "unused",
        spoken=lambda slug: SPOKEN,
        notes=lambda hints: [],
        default_tenant="summit-air",
        tool_names=set(TOOLS),
        now_ms=lambda: 1_793_000_000_000,
        later=later,
    )
    variables = inbound(TestClient(build_app(bridge)), "abc")["dynamic_variables"]
    assert variables["greeting"] == SPOKEN["server_down"]
    later.run()
    assert retell.stopped == ["abc"]


def build_app_down():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.post("/calls/start")
    def start():
        return JSONResponse({"detail": "down"}, 503)

    return app


def test_a_web_call_with_no_inbound_webhook_is_started_on_first_contact(client, tool_app):
    web = call_obj("web1", GREETING, HEAT_OUT, WHERE, call_type="web_call")
    web.pop("retell_llm_dynamic_variables")
    web.pop("from_number")
    answer = tool(client, "check_service_area", web, zip="11231")
    assert answer["ok"], answer
    assert rows(tool_app, "SELECT caller_id FROM calls WHERE id = 'retell_web1'") == [
        {"caller_id": "call"}
    ]


def test_retell_disconnection_reasons_map_to_our_end_reasons():
    assert end_reason("user_hangup", False) == "caller_hangup"
    assert end_reason("agent_hangup", False) == "agent_end"
    assert end_reason("call_transfer", False) == "transfer"
    assert end_reason("inactivity", False) == "dropped"
    assert end_reason("error_llm_websocket_open", False) == "error"
    assert end_reason("dial_failed", False) == "error"
    assert end_reason("user_hangup", True) == "agent_end"
    assert end_reason(None, False) == "dropped"


def test_a_signed_request_with_a_body_that_is_not_json_is_refused(client):
    from retell_kit import KEY, NOW_MS

    from voice.retell import signature

    raw = b"not json"
    headers = {"X-Retell-Signature": signature.sign(raw, KEY, NOW_MS)}
    assert client.post("/retell/webhook", content=raw, headers=headers).status_code == 401
    assert signed(client, "/retell/webhook", {"event": "unknown"}).status_code == 204


def chat_event(client, name: str, chat: dict) -> None:
    response = signed(client, "/retell/webhook", {"event": name, "chat": chat})
    assert response.status_code == 204, response.text


def test_a_chat_maps_to_the_eval_call_id_and_its_end_closes_the_call(client, tool_app):
    chat = {
        "chat_id": "chat_1",
        "retell_llm_dynamic_variables": {"summit_call_id": "eval-42"},
        "message_with_tool_calls": [
            {"role": "agent", "content": SPOKEN["greeting_unknown"]},
            {"role": "user", "content": "My AC is making a grinding noise. I'm in 11231."},
        ],
    }
    chat_event(client, "transcript_updated", chat)
    body = {"name": "check_service_area", "chat": chat, "args": {"zip": "11231"}}
    answer = signed(client, "/retell/tools/check_service_area", body).json()
    assert answer["ok"], answer
    chat_event(client, "chat_ended", chat)
    call = rows(tool_app, "SELECT ended_at FROM calls WHERE id = 'eval-42'")[0]
    assert call["ended_at"]
    turns = rows(tool_app, "SELECT speaker FROM turns WHERE call_id = 'eval-42'")
    assert [t["speaker"] for t in turns] == ["agent", "caller"]


def test_transcript_updated_reads_the_transcript_sent_beside_the_call(client, tool_app):
    inbound(client, "abc")
    payload = {
        "event": "transcript_updated",
        "call": {**call_obj("abc"), "transcript_object": []},
        "transcript_with_tool_calls": [GREETING, HEAT_OUT],
    }
    assert signed(client, "/retell/webhook", payload).status_code == 204
    turns = rows(tool_app, "SELECT speaker FROM turns WHERE call_id = 'retell_abc'")
    assert [t["speaker"] for t in turns] == ["agent", "caller"]


def test_a_call_end_the_tool_server_missed_is_asked_for_again(tmp_path, retell, later):
    down = Bridge(
        "key_test_retell",
        ToolServer(TestClient(build_app_down())),
        Ledger(tmp_path / "b.db"),
        retell,
        instructions=lambda start: "unused",
        spoken=lambda slug: SPOKEN,
        notes=lambda hints: [],
        default_tenant="summit-air",
        tool_names=set(TOOLS),
        now_ms=lambda: 1_793_000_000_000,
        later=later,
    )
    ended = call_obj("abc", disconnection_reason="user_hangup")
    response = signed(
        TestClient(build_app(down)), "/retell/webhook", {"event": "call_ended", "call": ended}
    )
    assert response.status_code == 503


def test_a_refused_start_is_tried_again_on_the_next_event(tmp_path, retell, later):
    calls = []

    class Refusing(ToolServer):
        def start(self, *args):
            calls.append(args)
            return {"ok": False, "refused": "server_error"}

    bridge = Bridge(
        "key_test_retell",
        Refusing(None),
        Ledger(tmp_path / "b.db"),
        retell,
        instructions=lambda start: "unused",
        spoken=lambda slug: SPOKEN,
        notes=lambda hints: [],
        default_tenant="summit-air",
        tool_names=set(TOOLS),
        later=later,
    )
    bridge.ensure_started(call_obj("abc"), "call")
    bridge.ensure_started(call_obj("abc"), "call")
    assert len(calls) == 2


def test_a_tool_the_server_does_not_have_is_refused(client):
    body = {"name": "delete_everything", "call": call_obj("abc"), "args": {}}
    assert signed(client, "/retell/tools/delete_everything", body).status_code == 404


def test_call_analyzed_keeps_retells_latency_and_cost_for_the_report(client, bridge):
    inbound(client, "abc")
    analyzed = call_obj(
        "abc",
        latency={"e2e": {"p50": 1200, "p90": 1900}},
        call_cost={"combined_cost": 19.3, "total_duration_seconds": 112},
    )
    event(client, "call_analyzed", analyzed)
    assert bridge.ledger.summaries() == [
        {
            "call_id": "retell_abc",
            "retell_id": "abc",
            "e2e_p50_ms": 1200,
            "e2e_p90_ms": 1900,
            "cost_usd": 0.193,
            "duration_s": 112,
        }
    ]


def test_the_hang_up_waits_for_a_final_line_still_being_said(client, retell, later):
    """Jackson's call on the Retell number (call_ca9bfcec0b968db42ac2f428bea): end_call came at
    the start of the turn, the delay was sized from "I'm sorry," and the call was stopped in the
    middle of the out-of-area sentence."""
    said = booked_call(client)
    so_far = [*said, utterance("user", "Never mind, bye."), utterance("agent", "I'm sorry,")]
    tool(client, "end_call", call_obj("abc", *so_far), reason="caller_goodbye")
    full = "I'm sorry, but we only work in the five boroughs. Thanks for calling, take care."
    retell.calls["abc"] = call_obj("abc", *said, utterance("user", "Never mind, bye."),
                                   utterance("agent", full))
    later.run()
    assert retell.stopped == [], "the longer line gets its own time to play"
    delay = later.pending[0][0]
    assert delay >= HANGUP_GRACE_S + 0.35 * len(full.split())
    later.run()
    assert retell.stopped == ["abc"]


def test_a_goodbye_already_said_with_a_tool_sentence_stops_one_grace_after_it(
    client, bridge, retell, later, monkeypatch
):
    """PR #158 review: the line compared before stopping was the last agent line plus the tool's
    `say`, which never matched the transcript, so every hang-up waited one extra cycle."""
    said = booked_call(client)
    bye = "Thanks for calling Summit Air, take care."
    so_far = [*said, utterance("user", "That's all, bye."), utterance("agent", bye)]
    real = bridge.tools.tool

    def with_say(call_id, name, args):
        answer = real(call_id, name, args)
        return {**answer, "say": bye} if name == "end_call" else answer

    monkeypatch.setattr(bridge.tools, "tool", with_say)
    tool(client, "end_call", call_obj("abc", *so_far), reason="caller_goodbye")
    retell.calls["abc"] = call_obj("abc", *so_far)
    delay = later.pending[0][0]
    assert delay == pytest.approx(3.0 + 0.35 * len(bye.split()) + HANGUP_GRACE_S)
    later.run()
    assert retell.stopped == ["abc"], "stopped at the first check, no extra wait"


def test_a_thanks_and_bye_in_the_grace_keeps_the_hang_up(client, retell, later):
    """Retell call call_1840576e622e3d729b760085142: the caller said "Thanks. Bye." inside the
    grace, the hang-up was called off, and the call ran to its 5-minute limit."""
    said = booked_call(client)
    bye = [*said, utterance("user", "That's all."), utterance("agent", "Thanks for calling, take care.")]
    tool(client, "end_call", call_obj("abc", *bye), reason="resolved")
    event(client, "transcript_updated", call_obj("abc", *bye, utterance("user", "Thanks. Bye.")))
    later.run()
    assert retell.stopped == ["abc"]


def test_a_goodbye_said_again_after_the_hang_up_was_called_off_arms_it_again(
    client, retell, later
):
    said = booked_call(client)
    bye = [*said, utterance("user", "That's all."), utterance("agent", "Thanks for calling, take care.")]
    tool(client, "end_call", call_obj("abc", *bye), reason="resolved")
    more = [*bye, utterance("user", "Oh wait, is the tech bringing filters?")]
    event(client, "transcript_updated", call_obj("abc", *more))
    later.run()
    assert retell.stopped == [], "the caller asked something more"
    again = [*more, utterance("agent", "Yes, they will. Thanks for calling, have a great day.")]
    event(client, "transcript_updated", call_obj("abc", *again))
    later.run()
    assert retell.stopped == ["abc"]


def test_the_hang_up_comes_about_one_grace_after_the_goodbye_ends(client, bridge, retell, later):
    """Retell call call_5600e8a36929e14b4b3915c9fcb: end_call came before the goodbye was said,
    the wait was sized from the agent's 26-word line before it, and the hang-up came 4.7 s after
    the last word. The first check is a goodbye's length away, and Retell's word timings then
    stop the call 1.5 s after the agent's last word."""
    start = NOW_MS - 26_460  # end_call came 26.46 s into the call
    clock = {"ms": NOW_MS}
    bridge.now_ms = lambda: clock["ms"]
    said = booked_call(client)
    asked = utterance("agent", "Is there something I can help you with about your heating "
                      "or cooling today, or anything else at all for your home?", start=11.3)
    bye = utterance("user", "Great, that's all I needed. Thanks, bye.", start=20.9)
    tool(client, "end_call", call_obj("abc", *said, asked, bye), reason="caller_goodbye")
    delay = later.pending[0][0]
    assert delay == pytest.approx(3.0 + HANGUP_GRACE_S)
    goodbye = "Thanks for calling Summit Air. Have a great rest of your day."
    playing = utterance("agent", goodbye, start=26.7, gap=0.22)  # last word ends 29.29 s in
    retell.calls["abc"] = call_obj("abc", *said, asked, bye, playing, start_timestamp=start)
    # At the first check the goodbye ended 29.3 s in; it's now 31.0 s, past the grace.
    clock["ms"] = start + 26_460 + int(delay * 1000)
    later.run()
    assert retell.stopped == ["abc"]


def test_a_goodbye_still_playing_at_the_check_gets_the_grace_after_its_last_word(
    client, bridge, retell, later
):
    start = NOW_MS - 20_000
    clock = {"ms": NOW_MS}
    bridge.now_ms = lambda: clock["ms"]
    said = booked_call(client)
    bye = utterance("user", "That's all, bye.", start=18.0)
    tool(client, "end_call", call_obj("abc", *said, bye), reason="caller_goodbye")
    long_bye = " ".join(["word"] * 20)
    playing = utterance("agent", long_bye, start=20.5, gap=0.3)  # last word ends 26.45 s in
    retell.calls["abc"] = call_obj("abc", *said, bye, playing, start_timestamp=start)
    clock["ms"] = start + 24_500
    later.run()
    assert retell.stopped == []
    assert later.pending[0][0] == pytest.approx(26.45 + HANGUP_GRACE_S - 24.5)
    clock["ms"] = start + 27_950
    later.run()
    assert retell.stopped == ["abc"]


def test_without_timings_from_get_call_the_last_events_timings_are_used(
    client, bridge, retell, later
):
    """A live call's get-call may carry no word timings; the transcript_updated event does."""
    start = NOW_MS - 20_000
    clock = {"ms": NOW_MS}
    bridge.now_ms = lambda: clock["ms"]
    said = booked_call(client)
    bye = utterance("user", "That's all, bye.", start=18.0)
    tool(client, "end_call", call_obj("abc", *said, bye), reason="caller_goodbye")
    goodbye = utterance("agent", "Thanks for calling, take care.", start=20.5, gap=0.25)
    event(client, "transcript_updated",
          call_obj("abc", *said, bye, goodbye, start_timestamp=start))
    retell.calls["abc"] = call_obj("abc", *said, bye, utterance("agent", goodbye["content"]))
    clock["ms"] = start + 24_500  # the goodbye ended 21.7 s in: past the grace
    later.run()
    assert retell.stopped == ["abc"]


def test_the_electrical_lines_reach_the_agent_now(client, retell):
    """Retell eval suite round 1: the server's electrical lines came back in the turn's `spoken`,
    which the bridge dropped, so they were said in neither electrical case."""
    inbound(client, "abc")
    sparked = utterance("user", "Hi, um, my outdoor AC unit just sparked and made a popping sound "
                        "when it kicked on, and now it won't run.")
    event(client, "transcript_updated", call_obj("abc", GREETING, sparked))
    assert len(retell.said) == 1
    assert "Please step away from the unit and turn it off" in retell.said[0][1]
