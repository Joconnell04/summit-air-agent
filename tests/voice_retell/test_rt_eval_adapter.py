import json

import httpx
import pytest

from voice.retell.eval_adapter import (
    RetellChatAgent,
    RetellNotReady,
    forget_calls,
    open_agent,
    reset_world,
)


def agent(handler, opened=None):
    http = httpx.Client(base_url="https://api.retellai.com", transport=httpx.MockTransport(handler))

    def open_chat(call_id, caller_id, env):
        (opened if opened is not None else []).append((call_id, caller_id))
        return "chat_9", "Thanks for calling Summit Air."

    a = RetellChatAgent(http, open_chat, "(212) 555-0999", "eval-x", {})
    a.chat_id = "chat_9"
    return a


def test_start_opens_the_chat_and_keeps_our_call_id():
    opened = []
    a = agent(lambda r: httpx.Response(500), opened)
    turn = a.start()
    assert opened == [("eval-x", "(212) 555-0999")]
    assert turn.text == "Thanks for calling Summit Air."
    assert (a.call_id, a.chat_id) == ("eval-x", "chat_9")


def test_end_ends_the_chat():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    agent(handler).end("caller_hangup")
    assert seen == [("PATCH", "/end-chat/chat_9")]


def test_hear_joins_agent_lines_and_marks_tool_turns():
    messages = [
        {"role": "tool_call_invocation", "tool_call_id": "t1", "name": "find_slots",
         "arguments": "{}"},
        {"role": "tool_call_result", "tool_call_id": "t1", "content": "{}", "successful": True},
        {"role": "agent", "content": "I have Tuesday morning."},
    ]

    def handler(request):
        assert request.url.path == "/create-chat-completion"
        assert json.loads(request.content) == {"chat_id": "chat_9", "content": "any time"}
        return httpx.Response(201, json={"messages": messages})

    turn = agent(handler).hear("any time")
    assert turn.text == "I have Tuesday morning."
    assert turn.timing.kind == "lookup" and not turn.ended


def test_an_end_call_that_hangs_up_ends_the_turn():
    result = json.dumps({"ok": True, "data": {"hang_up": True}})
    messages = [
        {"role": "agent", "content": "Bye."},
        {"role": "tool_call_invocation", "tool_call_id": "t2", "name": "end_call", "arguments": "{}"},
        {"role": "tool_call_result", "tool_call_id": "t2", "content": result, "successful": True},
    ]
    turn = agent(lambda r: httpx.Response(201, json={"messages": messages})).hear("bye")
    assert turn.ended


ENV = {"RETELL_API_KEY": "k", "RETELL_CHAT_AGENT_ID": "a",
       "RETELL_EVAL_TOOL_SERVER": "https://eval.example", "RETELL_EVAL_DEBUG_TOKEN": "tok"}


def reset_client(seen, status=200):
    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("x-debug-token")))
        return httpx.Response(status, json={"reset": status == 200})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_missing_settings_stop_with_a_clear_message():
    with pytest.raises(RetellNotReady, match="RETELL_CHAT_AGENT_ID"):
        open_agent("eval-x", "(212) 555-0999", env={"RETELL_API_KEY": "k"})


def test_a_run_opens_its_chats_through_the_retell_lanes_open_chat(monkeypatch):
    from voice.retell.chat import open_chat

    monkeypatch.setattr("voice.retell.eval_adapter.reset_world", lambda env: None)
    monkeypatch.setattr("voice.retell.eval_adapter.forget_calls", lambda env: None)
    retell_agent, _server = open_agent("eval-x", "(212) 555-0999", env=ENV)
    assert retell_agent.open_chat is open_chat


def test_the_eval_servers_token_goes_on_every_request_and_to_open_chat(monkeypatch):
    monkeypatch.setattr("voice.retell.eval_adapter.reset_world", lambda env: None)
    monkeypatch.setattr("voice.retell.eval_adapter.forget_calls", lambda env: None)
    agent, server = open_agent("eval-x", "(212) 555-0999", env=ENV)
    assert server.client.headers["X-Debug-Token"] == "tok"
    assert server.debug_token == "tok"
    assert agent.env["DEBUG_TOKEN"] == "tok"


def test_the_mock_is_reset_before_the_chat_opens(monkeypatch):
    order = []
    monkeypatch.setattr("voice.retell.eval_adapter.reset_world", lambda env: order.append("reset"))
    monkeypatch.setattr("voice.retell.eval_adapter.forget_calls", lambda env: None)
    open_agent("eval-x", "(212) 555-0999", env=ENV)
    assert order == ["reset"]


def test_a_reset_asks_the_mock_behind_the_eval_server_with_the_token():
    seen = []
    reset_world(ENV, reset_client(seen))
    assert seen == [("POST", "https://eval.example/mock/debug/reset", "tok")]


def test_the_reset_address_can_be_overridden_for_a_tunnel():
    seen = []
    env = {**ENV, "RETELL_EVAL_MOCK_RESET_URL": "http://127.0.0.1:8111/debug/reset"}
    reset_world(env, reset_client(seen))
    assert seen[0][1] == "http://127.0.0.1:8111/debug/reset"


def test_the_eval_tool_server_forgets_earlier_calls_before_each_scenario():
    seen = []
    forget_calls(ENV, reset_client(seen))
    assert seen == [("POST", "https://eval.example/debug/forget-calls", "tok")]
    with pytest.raises(RetellNotReady, match="TOOL_ALLOW_FORGET_CALLS"):
        forget_calls(ENV, reset_client([], status=403))


def test_a_refused_reset_stops_the_scenario_instead_of_scoring_a_dirty_world():
    with pytest.raises(RetellNotReady, match=r"refused its reset \(403"):
        reset_world(ENV, reset_client([], status=403))


def test_an_unreachable_reset_stops_the_scenario():
    def down(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(RetellNotReady, match="could not reach"):
        reset_world(ENV, httpx.Client(transport=httpx.MockTransport(down)))


def test_the_record_is_read_once_the_webhook_has_ended_the_call():
    from evals.world import ToolServer

    states = iter([None, None, "2026-11-02T14:05:00Z"])

    def handler(request):
        return httpx.Response(200, json={"call": {"ended_at": next(states)}})

    server = ToolServer(httpx.Client(base_url="https://eval.example",
                                     transport=httpx.MockTransport(handler)), "tok")
    assert server.final_trace("eval-x", wait_s=5, every_s=0)["call"]["ended_at"]


def test_a_call_that_never_ends_is_returned_after_the_wait():
    from evals.world import ToolServer

    server = ToolServer(httpx.Client(
        base_url="https://eval.example",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"call": {"ended_at": None}})),
    ), "tok")
    assert server.final_trace("eval-x", wait_s=0, every_s=0)["call"]["ended_at"] is None
