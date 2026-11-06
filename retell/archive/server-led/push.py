"""Push the server-led arm (issue #171) to Retell: its LLM (this folder's prompt and tools), a voice
agent for web calls and a chat agent for the eval harness, both named "Summit Air server-led
(test)". Both reach the arm's test instance (deploy/server-led), never the live bridge.

The voice agent's listening and voice settings are read from Jackson's agent, which is only read:
the arm differs in its prompt, its tools and where they go, nothing else.

Idempotent like voice/retell/push.py: it finds the agents by name and sends only what differs.

    uv run --env-file .env python retell/archive/server-led/push.py
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from voice.retell import build
from voice.retell.api import Retell
from voice.retell.push import ensure_agent

HERE = Path(__file__).resolve().parent
BASE_URL = "https://server-led.45-77-105-247.sslip.io"
JACKSON_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"
AGENT_NAME = "Summit Air server-led (test)"
CHAT_AGENT_NAME = "Summit Air server-led (test, chat)"
GREETING = (
    "Hi there, thanks so much for calling Summit Air today! I'm Brooke, the virtual assistant. "
    "How can I help?"
)
# The model as on Jackson's LLM (llm_12eba5f1d4a3b067f0635097de30), at temperature 0 instead of
# 0.3: at 0.3 it reworded the server's sentences on every offer and booking (eval runs
# 20261106-011527 and -012105).
MODEL = {
    "model": "claude-5.5-haiku",
    "model_temperature": 0,
    "model_high_priority": False,
}
# Fields of Jackson's agent that are its identity or its wiring, not how it listens and speaks.
NOT_COPIED = {
    "agent_id", "agent_name", "base_version", "version", "is_published", "response_engine",
    "webhook_url", "last_modification_timestamp", "channel",
}


def llm_body() -> dict:
    tools = json.loads((HERE / "tools.json").read_text())["general_tools"]
    for tool in tools:
        if not tool["url"].startswith(f"{BASE_URL}/retell/tools/"):
            raise SystemExit(f"{tool['name']} goes to {tool['url']}, not the arm's instance")
    return {
        **MODEL,
        "general_prompt": (HERE / "prompt.md").read_text(),
        "general_tools": tools,
        "begin_message": "{{greeting}}",
        "start_speaker": "agent",
        "default_dynamic_variables": {"call_context": "", "greeting": GREETING},
    }


def voice_body(jackson: dict, llm_id: str) -> dict:
    copied = {k: v for k, v in jackson.items() if k not in NOT_COPIED and v is not None}
    return {
        **copied,
        "agent_name": AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "webhook_url": f"{BASE_URL}/retell/webhook",
        # Retell's own do-not-call detection hangs up mid-call; mark_do_not_call on our server
        # handles the request instead (side-by-side lane, #169).
        "enable_dnc_detection": False,
    }


def chat_body(llm_id: str) -> dict:
    return {
        "agent_name": CHAT_AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "webhook_url": f"{BASE_URL}/retell/webhook",
        "webhook_events": build.CHAT_EVENTS,
    }


def main() -> int:
    retell = Retell(os.environ["RETELL_API_KEY"])
    jackson = retell.request("GET", f"/get-agent/{JACKSON_AGENT}")
    log: list[str] = []
    voice = ensure_agent(retell, "agent", AGENT_NAME, lambda i: voice_body(jackson, i),
                         llm_body(), log)
    chat = ensure_agent(retell, "chat-agent", CHAT_AGENT_NAME, chat_body, llm_body(), log)
    print("\n".join(log) or "nothing to change")
    print(f"voice agent {voice}\nchat agent {chat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
