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


def test_without_the_retell_lane_module_a_run_stops_with_a_clear_message():
    env = {k: "x" for k in ("RETELL_API_KEY", "RETELL_CHAT_AGENT_ID", "RETELL_EVAL_TOOL_SERVER",
                            "DEBUG_TOKEN")}
    with pytest.raises(RetellNotReady, match="brief 05"):
        open_retell("eval-x", "(212) 555-0999", env=env)
