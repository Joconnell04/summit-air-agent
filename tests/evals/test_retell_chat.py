import json

import httpx
import pytest

from evals.retell_chat import RetellChatAgent, RetellNotReady, open_retell


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


def test_missing_settings_stop_with_a_clear_message():
    with pytest.raises(RetellNotReady, match="RETELL_CHAT_AGENT_ID"):
        open_retell("eval-x", "(212) 555-0999", env={"RETELL_API_KEY": "k"})


def test_with_the_retell_lane_here_a_run_opens_its_chats_through_it():
    from voice.retell.chat import open_chat

    env = {k: "https://x" for k in ("RETELL_API_KEY", "RETELL_CHAT_AGENT_ID",
                                    "RETELL_EVAL_TOOL_SERVER", "RETELL_EVAL_DEBUG_TOKEN")}
    retell_agent, _server = open_retell("eval-x", "(212) 555-0999", env=env)
    assert retell_agent.open_chat is open_chat


def test_the_eval_servers_token_goes_on_every_request_and_to_open_chat(monkeypatch):
    import evals.retell_chat as rc

    monkeypatch.setattr(rc, "_open_chat", lambda: "opener")
    env = {"RETELL_API_KEY": "k", "RETELL_CHAT_AGENT_ID": "a",
           "RETELL_EVAL_TOOL_SERVER": "https://eval.example", "RETELL_EVAL_DEBUG_TOKEN": "tok"}
    agent, server = open_retell("eval-x", "(212) 555-0999", env=env)
    assert server.client.headers["X-Debug-Token"] == "tok"
    assert server.debug_token == "tok"
    assert agent.env["DEBUG_TOKEN"] == "tok"


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
