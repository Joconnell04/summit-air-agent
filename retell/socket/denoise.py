"""Read or switch Retell's noise cancellation on a socket agent, in one call (issue #176).

    uv run --env-file .env python retell/socket/denoise.py                 show it
    uv run --env-file .env python retell/socket/denoise.py no-denoise      switch it
    uv run --env-file .env python retell/socket/denoise.py noise-cancellation --live

Jackson's call call_046f37112cac691a50019893717 had his words zeroed out of the caller channel for
up to 40 s (noise cancellation; 77% of the channel exactly silent), so one call is made with it off
to compare. push.py never copies this setting, so a push doesn't undo a switch. Whatever mode wins
is set on the production agent with --live.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voice.retell.api import Retell
from voice.retell.push import find_agent

MODES = ("noise-cancellation", "noise-and-background-speech-cancellation", "no-denoise")
TEST_AGENT = "Summit Air socket (test)"
LIVE_AGENT = "Summit Air (socket)"


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--live"]
    name = LIVE_AGENT if "--live" in sys.argv else TEST_AGENT
    retell = Retell(os.environ["RETELL_API_KEY"])
    agent = find_agent(retell, name)
    if agent is None:
        raise SystemExit(f"no agent named {name!r}")
    agent_id = agent["agent_id"]
    before = retell.request("GET", f"/get-agent/{agent_id}").get("denoising_mode")
    if args:
        if args[0] not in MODES:
            raise SystemExit(f"mode must be one of {', '.join(MODES)}")
        retell.request("PATCH", f"/update-agent/{agent_id}", json={"denoising_mode": args[0]})
    after = retell.request("GET", f"/get-agent/{agent_id}").get("denoising_mode")
    print(f"{name} ({agent_id}): denoising_mode {before} -> {after}" if args
          else f"{name} ({agent_id}): denoising_mode {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
