"""Push the socket arm (issue #176) to Retell: one voice agent, "Summit Air socket (test)", whose
response engine is our custom-LLM socket on the server-led instance (deploy/server-led, port 8133).
The socket speaks every sentence the server-led flow hands it as written, and Haiku writes the rest
(voice/retell_llm).

The listening and voice settings are read from Jackson's agent, which is only read. The agent has
no webhook: the socket posts every line to the tool server and closes the call there. Idempotent:
it finds the agent by name and sends only what differs.

    uv run --env-file .env python retell/arms/socket/push.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from voice.retell import build
from voice.retell.api import Retell
from voice.retell.push import find_agent
from voice.retell_llm.llm_socket import dead_air_checks
from voice.retell_llm.rig_adapter import CHECK_IN_MS, socket_url

BASE_URL = "https://server-led.45-77-105-247.sslip.io"
JACKSON_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"
AGENT_NAME = "Summit Air socket (test)"
# Fields of Jackson's agent that are its identity or its wiring, not how it listens and speaks.
NOT_COPIED = {
    "agent_id", "agent_name", "base_version", "version", "is_published", "response_engine",
    "webhook_url", "webhook_events", "last_modification_timestamp", "channel",
}


def agent_body(jackson: dict, api_key: str) -> dict:
    copied = {k: v for k, v in jackson.items() if k not in NOT_COPIED and v is not None}
    checks = dead_air_checks(ROOT / "config" / "models.yaml")
    return {
        **copied,
        "agent_name": AGENT_NAME,
        "response_engine": {"type": "custom-llm", "llm_websocket_url": socket_url(BASE_URL, api_key)},
        # Retell's own do-not-call detection hangs up mid-call; mark_do_not_call on our server
        # handles the request instead (side-by-side lane, #169).
        "enable_dnc_detection": False,
        # The socket answers dead air with the tenant's check-ins (config/models.yaml dead_air).
        "reminder_trigger_ms": CHECK_IN_MS,
        "reminder_max_count": checks,
        "end_call_after_silence_ms": CHECK_IN_MS * (checks + 1),
    }


def main() -> int:
    api_key = os.environ["RETELL_API_KEY"]
    retell = Retell(api_key)
    jackson = retell.request("GET", f"/get-agent/{JACKSON_AGENT}")
    desired = agent_body(jackson, api_key)
    listed = find_agent(retell, AGENT_NAME)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
