import json

import httpx
import pytest

from evals.retell_chat import RetellChatAgent, RetellNotReady, open_retell


def agent(handler):
    http = httpx.Client(base_url="https://api.retellai.com", transport=httpx.MockTransport(handler))
    return RetellChatAgent(http, "agent_1", "(212) 555-0999", "eval-x")


def test_start_creates_a_chat_and_uses_its_id_as_the_call_id():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"chat_id": "chat_9", "agent_id": "agent_1",
                                         "chat_status": "ongoing"})

    a = agent(handler)
    a.start()
    assert seen["path"] == "/create-chat"
    assert seen["body"]["agent_id"] == "agent_1"
    assert seen["body"]["retell_llm_dynamic_variables"] == {"caller_id": "(212) 555-0999"}
    assert a.call_id == "chat_9"


def test_hear_joins_agent_lines_and_marks_tool_turns():
    messages = [
        {"role": "tool_call_invocation", "tool_call_id": "t1", "name": "find_slots",
         "arguments": "{}"},
        {"role": "tool_call_result", "tool_call_id": "t1", "content": "{}", "successful": True},
        {"role": "agent", "content": "I have Tuesday morning."},
    ]

    def handler(request):
        assert request.url.path == "/create-chat-completion"
        assert json.loads(request.content) == {"chat_id": "eval-x", "content": "any time"}
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
