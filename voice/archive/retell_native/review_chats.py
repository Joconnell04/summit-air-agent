"""Three scripted text chats through the native agent's review twin (same prompt, knowledge base
and functions, pointed at the eval bridge), to read how it handles a trivia opener, a Manhattan
booking and a gas smell before any phone call.

    uv run --env-file .env python -m voice.archive.retell_native.review_chats [OUT.md]

The caller's lines are fixed, so a change to the agent is the only thing that changes a transcript.
"""

import os
import sys
import time

from voice.retell.api import Retell, RetellError
from voice.retell.chat import open_chat
from voice.retell.eval_adapter import forget_calls, reset_world
from voice.retell.push import find_agent

from .push import CHAT_AGENT_NAME

CHATS = {
    "trivia opener": [
        "How many e's are in seventeen?",
        "Ha, okay. Are you open on Sundays?",
        "Great, that's all. Bye.",
    ],
    "Manhattan booking": [
        "Hi, I need to book a furnace tune-up. It's a gas furnace, works fine, just due.",
        "199 East 4th Street, unit 2, 10009. I'll be home, it's Jordan Lee.",
        "Yes, that's right.",
        "Yes, Jordan Lee.",
        "Yes, that works.",
        "No, that's all. Thanks, bye.",
    ],
    "gas smell": [
        "Hi, I think I smell gas in my kitchen.",
        "Okay, I'm outside now.",
        "Thanks. Bye.",
    ],
}


def run(retell: Retell, env: dict, title: str, lines: list[str]) -> list[str]:
    reset_world(env)
    forget_calls(env)
    call_id = f"native-review-{int(time.time())}"
    chat_id, greeting = open_chat(call_id, "+13475550213", env)
    out = [f"agent: {greeting}"]
    for line in lines:
        out.append(f"caller: {line}")
        try:
            answer = retell.request("POST", "/create-chat-completion",
                                    json={"chat_id": chat_id, "content": line})
        except RetellError as exc:
            out.append(f"(chat closed: {exc})")
            break
        for message in answer.get("messages", []):
            if message.get("role") == "agent" and message.get("content"):
                out.append(f"agent: {message['content']}")
            elif message.get("role") == "tool_call_invocation":
                out.append(f"  [tool] {message.get('name')} {message.get('arguments', '')[:160]}")
    try:
        retell.request("PATCH", f"/end-chat/{chat_id}")
    except RetellError:
        out.append("(the agent had already ended the chat)")
    return out


def main() -> int:
    retell = Retell(os.environ["RETELL_API_KEY"])
    agent = find_agent(retell, CHAT_AGENT_NAME)
    env = {**os.environ, "RETELL_CHAT_AGENT_ID": agent["agent_id"],
           "DEBUG_TOKEN": os.environ["RETELL_EVAL_DEBUG_TOKEN"]}
    report = []
    for title, lines in CHATS.items():
        report += [f"## {title}", "", *run(retell, env, title, lines), ""]
    text = "\n".join(report)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
