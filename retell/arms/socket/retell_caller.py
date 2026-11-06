"""The scripted caller for the socket arm's phone runs, as a Retell agent (issue #176).

    uv run --env-file .env python retell/arms/socket/retell_caller.py ensure
    uv run --env-file .env python retell/arms/socket/retell_caller.py call <persona.txt> [--to N]

The first scripted caller (voice.livekit.test_call) dialed out over a Twilio trunk; Twilio disabled
voice on the account (error 10005) after call r176-01 of the done bar. This one is a Retell agent,
"Summit Air test caller", whose prompt is the persona script, given per call in the dynamic
variable {{persona}}. It dials from our other Retell number, +1 678 336 6730, to the socket arm's
line, so the call is real phone audio end to end on Retell alone. Each call has two Retell legs (the
caller's and the socket agent's), so it costs about twice a single call.

`call` prints the conversation from the caller's side as the scripted runs' logs did ("agent :" is
Brian, "caller:" the persona), and waits for the call to end, at most MAX_WAIT_S.
"""

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from voice.retell.api import Retell
from voice.retell.push import ensure_agent

AGENT_NAME = "Summit Air test caller"
FROM_NUMBER = "+16783366730"
TO_NUMBER = "+15162129051"
MAX_WAIT_S = 420
# The caller's own words come from the persona; this only keeps it in the caller's seat.
PROMPT = """{{persona}}

You are the person calling. The company's assistant answers first: let it greet you, then say your
opening line. Keep to the script above, one short line a turn, and answer only what you're asked.
When the conversation is over and you've said goodbye, hang up with end_call."""
MODEL = {"model": "claude-5.5-haiku", "model_temperature": 0.7, "model_high_priority": False}


def llm_body() -> dict:
    return {
        **MODEL,
        "general_prompt": PROMPT,
        "start_speaker": "user",
        "general_tools": [{"type": "end_call", "name": "end_call",
                           "description": "Hang up once you've said goodbye."}],
        "default_dynamic_variables": {"persona": "You are a caller with a broken furnace."},
    }


def agent_body(llm_id: str) -> dict:
    return {
        "agent_name": AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        # A voice unlike Brian's, so the two sides are easy to tell apart in a recording.
        "voice_id": "cartesia-Cleo",
        "max_call_duration_ms": (MAX_WAIT_S - 60) * 1000,
        "end_call_after_silence_ms": 30_000,
        "enable_backchannel": False,
        "language": "en-US",
    }


def ensure(retell: Retell) -> str:
    log: list[str] = []
    agent_id = ensure_agent(retell, "agent", AGENT_NAME, agent_body, llm_body(), log)
    print("\n".join(log) or "nothing to change")
    return agent_id


def call(retell: Retell, persona: str, to: str) -> int:
    agent_id = ensure(retell)
    started = retell.request("POST", "/v2/create-phone-call", json={
        "from_number": FROM_NUMBER, "to_number": to, "override_agent_id": agent_id,
        "retell_llm_dynamic_variables": {"persona": persona},
    })
    call_id = started["call_id"]
    print(f"dialing {to} from {FROM_NUMBER} as {call_id}", flush=True)
    deadline = time.time() + MAX_WAIT_S
    status = started.get("call_status")
    while time.time() < deadline:
        time.sleep(5)
        got = retell.request("GET", f"/v2/get-call/{call_id}")
        status = got.get("call_status")
        if status in ("ended", "error", "not_connected"):
            break
    else:
        retell.request("POST", f"/v2/stop-call/{call_id}")
        print("stopped: the call ran past its time", flush=True)
        got = retell.request("GET", f"/v2/get-call/{call_id}")
    for entry in got.get("transcript_object") or []:
        who = "caller" if entry.get("role") == "agent" else "agent "
        print(f"{who}: {(entry.get('content') or '').strip()}")
    print(f"ended: {got.get('disconnection_reason')} ({status})", flush=True)
    return 0 if status == "ended" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("ensure")
    dial = sub.add_parser("call")
    dial.add_argument("persona")
    dial.add_argument("--to", default=TO_NUMBER)
    args = parser.parse_args()
    retell = Retell(os.environ["RETELL_API_KEY"])
    if args.action == "ensure":
        print(ensure(retell))
        return 0
    return call(retell, Path(args.persona).read_text(), args.to)


if __name__ == "__main__":
    raise SystemExit(main())
