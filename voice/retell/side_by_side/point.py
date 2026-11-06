"""Points one of our Retell numbers at a side-by-side arm, or saves and restores what it was.

    python point.py save <number> <file>                  write the number's inbound setup to <file>
    python point.py arm <number> <agent_id> <inbound_url> answer with this agent, this inbound webhook
    python point.py restore <number> <file>               put back what save wrote

Run on the summit-air server from the app checkout with its .env, so voice.retell.api (versioned
endpoints only) is importable. The inbound webhook decides which world the call starts in: the live
bridge (/retell/inbound) or the eval bridge (/eval/retell/inbound).
"""

import json
import os
import sys
from pathlib import Path

from voice.retell.api import Retell

NUMBERS = ("+16466240418", "+16783366730")
FIELDS = ("inbound_agents", "inbound_webhook_url")


def current(retell: Retell, number: str) -> dict:
    for n in retell.list_all("GET", "/v2/list-phone-numbers"):
        if n["phone_number"] == number:
            return {k: n.get(k) for k in FIELDS}
    raise SystemExit(f"{number} is not on this Retell account")


def put(retell: Retell, number: str, setup: dict) -> None:
    retell.request("PATCH", f"/update-phone-number/{number}", json=setup)
    now = current(retell, number)
    ids = [a["agent_id"] for a in now["inbound_agents"] or []]
    if ids != [a["agent_id"] for a in setup["inbound_agents"]] or (
        now["inbound_webhook_url"] != setup["inbound_webhook_url"]
    ):
        raise SystemExit(f"{number} did not take the change: {now}")


def main() -> None:
    retell = Retell(os.environ["RETELL_API_KEY"])
    action, number = sys.argv[1], sys.argv[2]
    if number not in NUMBERS:
        raise SystemExit(f"{number} is not a side-by-side line")
    if action == "save":
        Path(sys.argv[3]).write_text(json.dumps(current(retell, number)))
    elif action == "arm":
        agent_id, inbound_url = sys.argv[3], sys.argv[4]
        put(
            retell,
            number,
            {
                "inbound_agents": [{"agent_id": agent_id, "agent_version": "latest", "weight": 1}],
                "inbound_webhook_url": inbound_url,
            },
        )
    elif action == "restore":
        put(retell, number, json.loads(Path(sys.argv[3]).read_text()))
    else:
        raise SystemExit(__doc__)
    now = current(retell, number)
    print(
        action, number, [a["agent_id"] for a in now["inbound_agents"]], now["inbound_webhook_url"]
    )


if __name__ == "__main__":
    main()
