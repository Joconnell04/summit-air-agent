"""Push the socket arm (issue #176) to Retell: one voice agent, "Summit Air socket (test)", whose
response engine is our custom-LLM socket on the server-led instance (deploy/server-led, port 8133),
and the arm's own number, +1 516 212 9051, answered by it through the server-led bridge.
The socket speaks every sentence the server-led flow hands it as written, and Haiku writes the rest
(voice/retell_llm).

The listening and voice settings are read from Jackson's agent, which is only read. The agent has
no webhook: the socket posts every line to the tool server and closes the call there. Idempotent:
it finds the agent by name and sends only what differs.

    uv run --env-file .env python retell/socket/push.py
    uv run --env-file .env python retell/socket/push.py --live

--live pushes the production agent instead, "Summit Air (socket)", on the live socket (deploy/retell
`llm`, docs/ai-process/briefs/socket-cutover.md). It points no number anywhere: (646) is switched
by hand at cut-over, and pointed back at Jackson's agent to roll back.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voice.retell import build
from voice.retell.api import Retell
from voice.retell.push import find_agent
from voice.retell_llm.rig_adapter import socket_url

BASE_URL = "https://server-led.45-77-105-247.sslip.io"
JACKSON_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"
AGENT_NAME = "Summit Air socket (test)"
LIVE_BASE_URL = "https://45-77-105-247.sslip.io"
LIVE_AGENT_NAME = "Summit Air (socket)"
NUMBER = "+15162129051"
INBOUND = f"{BASE_URL}/retell/inbound"
# Retell ends the call after this much silence: the dead-air hang-up (DECISIONS.md 081, 8 minutes).
SILENCE_HANG_UP_MS = 480_000
# Fields of Jackson's agent that are its identity or its wiring, not how it listens and speaks.
NOT_COPIED = {
    "agent_id", "agent_name", "base_version", "version", "is_published", "response_engine",
    "webhook_url", "webhook_events", "last_modification_timestamp", "channel",
    # The socket agents' own setting (retell/socket/denoise.py), so a push never undoes a
    # switch made for a test call.
    "denoising_mode",
}


def agent_body(jackson: dict, api_key: str, base_url: str = BASE_URL,
               name: str = AGENT_NAME) -> dict:
    """Jackson's voice (cartesia-Brian), backchannels and check-ins (8 s, twice), on our socket."""
    copied = {k: v for k, v in jackson.items() if k not in NOT_COPIED and v is not None}
    return {
        **copied,
        "agent_name": name,
        "response_engine": {"type": "custom-llm", "llm_websocket_url": socket_url(base_url, api_key)},
        # Retell's own do-not-call detection hangs up mid-call; mark_do_not_call on our server
        # handles the request instead (side-by-side lane, #169).
        "enable_dnc_detection": False,
        # The socket answers each of Jackson's check-ins with "Are you still there?".
        "end_call_after_silence_ms": SILENCE_HANG_UP_MS,
    }


def number_body(agent_id: str) -> dict:
    return {
        "inbound_agents": [{"agent_id": agent_id, "agent_version": "latest", "weight": 1}],
        "inbound_webhook_url": INBOUND,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    live = parser.parse_args().live
    api_key = os.environ["RETELL_API_KEY"]
    retell = Retell(api_key)
    jackson = retell.request("GET", f"/get-agent/{JACKSON_AGENT}")
    name = LIVE_AGENT_NAME if live else AGENT_NAME
    desired = agent_body(jackson, api_key, LIVE_BASE_URL if live else BASE_URL, name)
    listed = find_agent(retell, name)
    if listed is None:
        agent_id = retell.request("POST", "/create-agent", json=desired)["agent_id"]
        print(f"created agent {agent_id}")
    else:
        agent_id = listed["agent_id"]
        current = retell.request("GET", f"/get-agent/{agent_id}")
        diff = build.changes(current, desired)
        if diff:
            retell.request("PATCH", f"/update-agent/{agent_id}", json=diff)
        print(f"agent {agent_id}: {', '.join(sorted(diff)) or 'nothing to change'}")
    if live:
        print(f"set RETELL_SERVER_LED_AGENTS={agent_id} in ~/summit-air/retell.env, then restart "
              "summit-retell@bridge")
        return 0
    number = next(n for n in retell.list_all("GET", "/v2/list-phone-numbers")
                  if n["phone_number"] == NUMBER)
    diff = build.changes(number, number_body(agent_id))
    if diff:
        retell.request("PATCH", f"/update-phone-number/{NUMBER}", json=diff)
    print(f"{NUMBER}: {', '.join(sorted(diff)) or 'nothing to change'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
