"""The hold line on a Retell number, for the owner's Hold line switch (app/owner_settings.py).

While the switch is on, every transfer rings TRANSFER_HOLD_NUMBER instead of a person. This puts a
Retell agent on that number that answers with a hold message and repeats it every 40 seconds for
about six minutes, then says someone will call back and hangs up. It replaces the Twilio hold line
(scripts/phone/hold_line.py, Twilio) while the Twilio account's voice is suspended.

The number is a Retell-owned one we already hold, +1 678 336 6730, which the scripted test caller
also dials out from; an inbound agent doesn't change its outbound calls. Idempotent: it finds the
agent by name and sends only what differs.

    uv run --env-file .env python -m voice.retell.hold_line

Then set TRANSFER_HOLD_NUMBER=+16783366730 in the server's .env and restart the live tool server.
"""

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from voice.retell import build
from voice.retell.api import Retell
from voice.retell.push import find_agent

NUMBER = "+16783366730"
AGENT_NAME = "Summit Air hold line"
# The voice of Jackson's agent, so the hold line sounds like the same company.
JACKSON_AGENT = "agent_a3e38e6aa3d4e75dfac12aafc8"
FIRST = "Thanks for holding. Someone from Summit Air will be with you shortly."
AGAIN = "Thanks for holding, someone will be with you shortly."
GOODBYE = "Sorry, nobody is free to take your call right now. Someone from Summit Air will call you back."
PROMPT = f"""You are the hold line for Summit Air, a home heating and cooling company. The caller is
waiting for a team member. You never help, ask questions, give information or make anything up.

Each time you speak, say exactly one of these, word for word, and nothing else:
- "{AGAIN}"
- If the caller asks something: "I'm just the hold line, so I can't help with that, but someone will be with you shortly."

If the call has gone on for about six minutes, say "{GOODBYE}" and end the call."""
# ASSUMED: a reminder every 40 s, as the Twilio hold line did; Retell hangs up after six minutes of
# silence from the caller.
REMINDER_MS = 40_000
REMINDERS = 8
SILENCE_HANG_UP_MS = 360_000


def model() -> str:
    config = yaml.safe_load((ROOT / "config/models.yaml").read_text())
    return build.retell_model(config["retell"]["hold_line"]["model"])


def llm_body() -> dict:
    return {
        "model": model(),
        "general_prompt": PROMPT,
        "begin_message": FIRST,
        "start_speaker": "agent",
        "general_tools": [{"type": "end_call", "name": "end_call", "description": "End the call after the goodbye line."}],
    }


def agent_body(llm_id: str, voice_id: str) -> dict:
    return {
        "agent_name": AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm_id},
        "voice_id": voice_id,
        "reminder_trigger_ms": REMINDER_MS,
        "reminder_max_count": REMINDERS,
        "end_call_after_silence_ms": SILENCE_HANG_UP_MS,
        "enable_dnc_detection": False,
    }


def main() -> int:
    retell = Retell(os.environ["RETELL_API_KEY"])
    voice_id = retell.request("GET", f"/get-agent/{JACKSON_AGENT}")["voice_id"]
    listed = find_agent(retell, AGENT_NAME)
    if listed is None:
        llm_id = retell.request("POST", "/create-retell-llm", json=llm_body())["llm_id"]
        agent_id = retell.request("POST", "/create-agent", json=agent_body(llm_id, voice_id))["agent_id"]
        print(f"created agent {agent_id} on {llm_id}")
    else:
        agent_id = listed["agent_id"]
        current = retell.request("GET", f"/get-agent/{agent_id}")
        llm_id = current["response_engine"]["llm_id"]
        llm_diff = build.changes(retell.request("GET", f"/get-retell-llm/{llm_id}"), llm_body())
        if llm_diff:
            retell.request("PATCH", f"/update-retell-llm/{llm_id}", json=llm_diff)
        diff = build.changes(current, agent_body(llm_id, voice_id))
        if diff:
            retell.request("PATCH", f"/update-agent/{agent_id}", json=diff)
        print(f"agent {agent_id}: {', '.join(sorted({*diff, *llm_diff})) or 'nothing to change'}")
    number = next(n for n in retell.list_all("GET", "/v2/list-phone-numbers")
                  if n["phone_number"] == NUMBER)
    wanted = {"inbound_agents": [{"agent_id": agent_id, "agent_version": "latest", "weight": 1}]}
    diff = build.changes(number, wanted)
    if diff:
        retell.request("PATCH", f"/update-phone-number/{NUMBER}", json=diff)
    print(f"{NUMBER}: {', '.join(sorted(diff)) or 'nothing to change'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
