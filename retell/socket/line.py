"""The socket arm's own line, +1 516 212 9051, for its phone runs (issue #176). It always answers
with the socket agent (push.py sets it up), so a run never repoints a number.

    python retell/socket/line.py busy      exit 1 if a call on the line started in the last
                                                30 minutes and hasn't ended
    python retell/socket/line.py hangup    end our own scripted calls still live on the line

Versioned Retell endpoints only (voice/retell/api.py).
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voice.retell.api import Retell

NUMBER = "+15162129051"
# The scripted callers' caller IDs: the Twilio test trunk (voice/livekit/test_call.py), then our
# Retell number the test-caller agent dials from (retell_caller.py).
TEST_CALLERS = ("+16466240418", "+16783366730")
SOCKET_AGENT_NAME = "Summit Air socket (test)"
LIVE = ("registered", "ongoing")


def live_calls(retell: Retell, now_ms: int) -> list[dict]:
    body = {
        "filter_criteria": {
            "to_number": {"type": "string", "op": "eq", "value": NUMBER},
            "start_timestamp": {"type": "range", "op": "bt",
                                "value": [now_ms - 30 * 60_000, now_ms + 60_000]},
        },
        "limit": 50,
    }
    items = retell.request("POST", "/v3/list-calls", json=body).get("items", [])
    return [c for c in items if c.get("call_status") in LIVE]


def ours(call: dict, agent_id: str) -> bool:
    """A scripted call of this arm: on the socket agent, from the test caller. Anyone else's call
    is never touched."""
    return call.get("agent_id") == agent_id and call.get("from_number") in TEST_CALLERS


def socket_agent(retell: Retell) -> str:
    listed = retell.request("POST", "/v2/list-agents", json={})
    items = listed.get("items", []) if isinstance(listed, dict) else listed
    found = next((a for a in items if a.get("agent_name") == SOCKET_AGENT_NAME), None)
    if found is None:
        raise SystemExit(f"no agent named {SOCKET_AGENT_NAME!r}; run retell/socket/push.py")
    return found["agent_id"]


def main() -> int:
    retell = Retell(os.environ["RETELL_API_KEY"])
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    calls = live_calls(retell, int(time.time() * 1000))
    if action == "busy":
        for c in calls:
            print(f"live: {c['call_id']} {c.get('agent_id')} {c.get('call_status')}")
        return 1 if calls else 0
    if action == "hangup":
        # A scripted caller stopped mid-call leaves its phone leg open, and the socket's check-ins
        # ran ten minutes on call_559d36684d7f1153b2df75f1d71.
        agent_id = socket_agent(retell)
        for c in calls:
            if ours(c, agent_id):
                retell.request("POST", f"/v2/stop-call/{c['call_id']}")
                print(f"ended {c['call_id']}")
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main())
