"""Sets fields on a side-by-side arm's Retell agent, for a settings A/B.

    PYTHONPATH=. uv run --env-file .env python voice/retell/side_by_side/set_agent.py <agent_id> key=value ...

Values are read as JSON when they parse (true, 0.8, "fast"), else as text. Refuses Jackson's own
agent. Prints the fields as Retell has them afterwards.
"""

import json
import os
import sys

from voice.retell.api import Retell

JACKSON_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"


def value(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def main() -> None:
    agent_id, pairs = sys.argv[1], sys.argv[2:]
    if agent_id == JACKSON_AGENT:
        raise SystemExit("refusing to change Jackson's own agent")
    body = {k: value(v) for k, v in (p.split("=", 1) for p in pairs)}
    retell = Retell(os.environ["RETELL_API_KEY"])
    retell.request("PATCH", f"/update-agent/{agent_id}", json=body)
    agent = retell.request("GET", f"/get-agent/{agent_id}")
    print(agent_id, {k: agent.get(k) for k in body})


if __name__ == "__main__":
    main()
