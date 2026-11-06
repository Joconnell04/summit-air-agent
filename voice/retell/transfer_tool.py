"""Add the warm transfer to a Retell agent: our `request_transfer` custom function, Retell's
`transfer_call` tool, and the agent's transfer webhooks (issue #184).

The server decides; Retell only dials. `transfer_call` dials `{{transfer_number}}`, a dynamic
variable nothing sets until the bridge gets the server's yes from `request_transfer` and sets it
through Update Live Call (voice/retell/bridge.py). A `transfer_call` the model makes on its own has
no number, so it fails and the agent keeps the caller.

Dry run by default: it prints what would change. --apply sends it. Run again and it sends nothing.

    uv run --env-file .env python -m voice.retell.transfer_tool --agent agent_xxx
    uv run --env-file .env python -m voice.retell.transfer_tool --agent agent_xxx --apply

Only endpoints in api.ALLOWED are used. The LLM and agent changes land on their draft; publishing
is left to whoever owns the agent.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from prompts.assemble import job_types, tool_specs

from . import build
from .api import Retell
from .bridge import TRANSFER_VARIABLE

BRIDGE_URL = "https://45-77-105-247.sslip.io"
TENANT = Path(__file__).resolve().parents[2] / "tenants" / "summit-air"
TRANSFER_TOOL = "transfer_call"
TRANSFER_EVENTS = ("transfer_bridged", "transfer_cancelled")
# Retell's default when an agent has no webhook_events (SOURCE:
# https://docs.retellai.com/api-references/update-agent.md, webhook_events).
DEFAULT_EVENTS = ["call_started", "call_ended", "call_analyzed"]
# How long the office line rings before the transfer counts as unanswered, from the tenant's
# transfer.ring_seconds (ASSUMED 20 s there).
RING_SECONDS = 20
# How long the agent waits for a person to speak once the line picks up. Retell's floor is 10 s.
ANSWER_WAIT_SECONDS = 30


def transfer_call_tool(ring_seconds: int = RING_SECONDS) -> dict:
    """Retell's warm transfer: the caller hears hold music while the office line rings, the
    agent waits for a person to speak, tells them privately what the call is about, then bridges.
    Nobody answering, or no number set, hands the caller back to the agent."""
    return {
        "type": "transfer_call",
        "name": TRANSFER_TOOL,
        "description": (
            "Put the caller through to the office. Call it only right after request_transfer "
            "said yes and you said its sentence; never on your own."
        ),
        "transfer_destination": {"type": "predefined", "number": "{{" + TRANSFER_VARIABLE + "}}"},
        "transfer_option": {
            "type": "warm_transfer",
            "on_hold_music": "relaxing_sound",
            "transfer_ring_duration_ms": ring_seconds * 1000,
            "agent_detection_timeout_ms": ANSWER_WAIT_SECONDS * 1000,
            "private_handoff_option": {
                "type": "prompt",
                "prompt": (
                    "In one short sentence, tell the Summit Air team member who is calling and "
                    "what they need. Give no account details."
                ),
            },
        },
        "speak_during_execution": False,
    }


def request_transfer_tool(base_url: str) -> dict:
    spec = next(s for s in tool_specs(job_types(TENANT)) if s["name"] == "request_transfer")
    return build.custom_tools([spec], base_url)[0]


def with_transfer(tools: list[dict], base_url: str, ring_seconds: int = RING_SECONDS) -> list[dict]:
    """The agent's tools with the two transfer tools in place: replaced if they are there, added
    at the end if not. Every other tool is kept as it is."""
    wanted = {
        "request_transfer": request_transfer_tool(base_url),
        TRANSFER_TOOL: transfer_call_tool(ring_seconds),
    }
    kept = [wanted.pop(t["name"]) if t.get("name") in wanted else t for t in tools]
    return kept + list(wanted.values())


def with_transfer_events(events: list[str] | None) -> list[str]:
    current = list(events) if events else list(DEFAULT_EVENTS)
    return current + [e for e in TRANSFER_EVENTS if e not in current]


def plan(retell: Retell, agent_id: str, base_url: str) -> tuple[dict, dict, dict]:
    """What to send: (llm_id and LLM fields, agent fields), each empty when nothing differs, plus
    the agent as read."""
    agent = retell.request("GET", f"/get-agent/{agent_id}")
    engine = agent.get("response_engine") or {}
    if engine.get("type") != "retell-llm":
        raise SystemExit(f"{agent_id} doesn't run on a Retell LLM ({engine.get('type')})")
    llm = retell.request("GET", f"/get-retell-llm/{engine['llm_id']}")
    llm_fields = {}
    tools = with_transfer(llm.get("general_tools") or [], base_url)
    if not build.contains(llm.get("general_tools") or [], tools):
        llm_fields["general_tools"] = tools
    defaults = dict(llm.get("default_dynamic_variables") or {})
    # A default number would let transfer_call dial without the server's yes.
    if TRANSFER_VARIABLE in defaults:
        defaults.pop(TRANSFER_VARIABLE)
        llm_fields["default_dynamic_variables"] = defaults
    agent_fields = {}
    events = with_transfer_events(agent.get("webhook_events"))
    if events != agent.get("webhook_events"):
        agent_fields["webhook_events"] = events
    return {"llm_id": engine["llm_id"], **llm_fields}, agent_fields, agent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--agent", required=True, help="the Retell agent id")
    parser.add_argument("--bridge-url", default=os.environ.get("RETELL_BRIDGE_URL", BRIDGE_URL))
    parser.add_argument("--apply", action="store_true", help="send the changes")
    args = parser.parse_args()
    retell = Retell(os.environ["RETELL_API_KEY"])
    llm_change, agent_change, agent = plan(retell, args.agent, args.bridge_url.rstrip("/"))
    llm_id = llm_change.pop("llm_id")
    name = agent.get("agent_name", args.agent)
    if not llm_change and not agent_change:
        print(f"{name}: nothing to send")
        return 0
    for field in llm_change:
        print(f"{name}: LLM {llm_id} {field} changes")
    for field, value in agent_change.items():
        print(f"{name}: agent {field} -> {json.dumps(value)}")
    if not args.apply:
        print("dry run; --apply sends it")
        return 0
    if llm_change:
        retell.request("PATCH", f"/update-retell-llm/{llm_id}", json=llm_change)
    if agent_change:
        retell.request("PATCH", f"/update-agent/{args.agent}", json=agent_change)
    print("sent; publish the agent for the phone number to use it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
