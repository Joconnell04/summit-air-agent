"""The (646) line for the socket arm's phone runs (issue #176). The line is Jackson's: it is pointed
at the socket agent only for a run, and always put back on his agent and the live bridge after,
on error too.

    python retell/arms/socket/line.py busy      exit 1 if a call on the line started in the last
                                                30 minutes and hasn't ended
    python retell/arms/socket/line.py count     print how many calls are live on the line
    python retell/arms/socket/line.py arm       answer with the socket agent and the server-led
                                                world's inbound webhook
    python retell/arms/socket/line.py restore   answer with Jackson's agent and the live bridge
    python retell/arms/socket/line.py hangup    end our own scripted calls still live on the
                                                socket agent (their caller ID is the line's own)

Versioned Retell endpoints only (voice/retell/api.py).
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from voice.retell.api import Retell

NUMBER = "+16466240418"
JACKSON = {
    "inbound_agents": [
        {"agent_id": "agent_a3e38e6aa3d4e75dfac12aafc8", "agent_version": "latest", "weight": 1}
    ],
    "inbound_webhook_url": "https://45-77-105-247.sslip.io/retell/inbound",
}
SOCKET_AGENT_NAME = "Summit Air socket (test)"
SOCKET_INBOUND = "https://server-led.45-77-105-247.sslip.io/retell/inbound"
LIVE = ("registered", "ongoing")


def setup(agent_id: str) -> dict:
    return {
        "inbound_agents": [{"agent_id": agent_id, "agent_version": "latest", "weight": 1}],
        "inbound_webhook_url": SOCKET_INBOUND,
    }


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


def put(retell: Retell, wanted: dict) -> None:
    retell.request("PATCH", f"/update-phone-number/{NUMBER}", json=wanted)
    now = next(n for n in retell.list_all("GET", "/v2/list-phone-numbers")
               if n["phone_number"] == NUMBER)
    ids = [a["agent_id"] for a in now.get("inbound_agents") or []]
    if ids != [a["agent_id"] for a in wanted["inbound_agents"]] or (
        now.get("inbound_webhook_url") != wanted["inbound_webhook_url"]
    ):
        raise SystemExit(f"{NUMBER} did not take the change: {ids} {now.get('inbound_webhook_url')}")
    print(f"{NUMBER} answers with {ids[0]} via {now['inbound_webhook_url']}")


def ours(call: dict, agent_id: str) -> bool:
    """A scripted call of this arm: on the socket agent, from the test caller, whose caller ID is
    the line's own number. Anyone else's call is never touched."""
    return call.get("agent_id") == agent_id and call.get("from_number") == NUMBER


def socket_agent(retell: Retell) -> str:
    listed = retell.request("POST", "/v2/list-agents", json={})
    items = listed.get("items", []) if isinstance(listed, dict) else listed
    found = next((a for a in items if a.get("agent_name") == SOCKET_AGENT_NAME), None)
    if found is None:
        raise SystemExit(f"no agent named {SOCKET_AGENT_NAME!r}; run retell/arms/socket/push.py")
    return found["agent_id"]


def main() -> int:
    retell = Retell(os.environ["RETELL_API_KEY"])
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "busy":
        calls = live_calls(retell, int(time.time() * 1000))
        for c in calls:
            print(f"live: {c['call_id']} {c.get('agent_id')} {c.get('call_status')}")
        return 1 if calls else 0
    if action == "count":
        print(len(live_calls(retell, int(time.time() * 1000))))
        return 0
    if action == "arm":
        put(retell, setup(socket_agent(retell)))
    elif action == "restore":
        put(retell, JACKSON)
    elif action == "hangup":
        # A scripted caller stopped mid-call leaves its phone leg open, and the socket's dead-air
        # check-ins ran ten minutes on call_559d36684d7f1153b2df75f1d71.
        agent_id = socket_agent(retell)
        for c in live_calls(retell, int(time.time() * 1000)):
            if ours(c, agent_id):
                retell.request("POST", f"/v2/stop-call/{c['call_id']}")
                print(f"ended {c['call_id']}")
    else:
        raise SystemExit(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
