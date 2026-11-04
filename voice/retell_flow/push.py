"""Push the Retell Conversation Flow agent: the voice flow and its voice agent, and optionally the
chat flow and chat agent the eval harness drives, and the 678 number's binding.

Idempotent like voice/retell/push.py: it finds each agent by name, reads the flow the agent uses,
and writes only what differs. A flow has no name of its own; the agent holding it is how it is
found. Only the endpoints in voice/retell/api.ALLOWED are used.

The number stays where it is unless --bind-number is given; `make retell-push` binds it back to the
single-prompt agent.

Run: make retell-flow-push                          (RETELL_BRIDGE_URL is the bridge's public base)
     make retell-flow-push EVAL_URL=https://...     (also the chat agent, for eval runs)
     make retell-flow-push BIND=1                   (and put the flow agent on the 678 number)
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

from prompts.assemble import job_types, tool_specs
from voice import names as local_names
from voice.retell import build as single
from voice.retell.api import Retell
from voice.retell.push import ensure_number, ensure_voice, find_agent, live_profile

from . import build


def ensure_flow(retell: Retell, flow_id: str | None, desired: dict, log: list[str]) -> str:
    if flow_id is None:
        flow = retell.request("POST", "/create-conversation-flow", json=desired)
        log.append(f"created flow {flow['conversation_flow_id']}")
        return flow["conversation_flow_id"]
    current = retell.request("GET", f"/get-conversation-flow/{flow_id}")
    diff = single.changes(current, desired)
    if diff:
        retell.request("PATCH", f"/update-conversation-flow/{flow_id}", json=diff)
        log.append(f"updated flow {flow_id}: {', '.join(sorted(diff))}")
    return flow_id


def ensure_agent(retell: Retell, kind: str, name: str, body_for, flow: dict, log: list[str]) -> str:
    """kind is "agent" (voice) or "chat-agent"."""
    listed = find_agent(retell, name)
    current = retell.request("GET", f"/get-{kind}/{listed['agent_id']}") if listed else None
    engine = (current or {}).get("response_engine") or {}
    flow_id = ensure_flow(retell, engine.get("conversation_flow_id"), flow, log)
    desired = body_for(flow_id)
    if current is None:
        agent = retell.request("POST", f"/create-{kind}", json=desired)
        log.append(f"created {kind} {agent['agent_id']}")
        return agent["agent_id"]
    diff = single.changes(current, desired)
    if diff:
        retell.request("PATCH", f"/update-{kind}/{current['agent_id']}", json=diff)
        log.append(f"updated {kind} {current['agent_id']}: {', '.join(sorted(diff))}")
    return current["agent_id"]


def push(
    retell: Retell,
    specs: list[dict],
    base_url: str,
    model_id: str,
    voice: dict,
    ring_ms: int,
    eval_url: str | None = None,
    names: local_names.Names | None = None,
    bind_number: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Bring Retell in line with the code. Returns what changed and the agent ids."""
    log: list[str] = []
    ids = {}
    voice_id = ensure_voice(retell, voice["voice_id"], voice["name"])
    ids["agent"] = ensure_agent(
        retell,
        "agent",
        build.AGENT_NAME,
        lambda flow_id: build.agent_body(flow_id, base_url, voice_id, voice["model"], names),
        build.flow_body(specs, base_url, model_id, ring_ms, chat=False),
        log,
    )
    if bind_number:
        ensure_number(retell, ids["agent"], base_url, log)
    if eval_url:
        ids["chat_agent"] = ensure_agent(
            retell,
            "chat-agent",
            build.CHAT_AGENT_NAME,
            lambda flow_id: build.chat_agent_body(flow_id, eval_url),
            build.flow_body(specs, eval_url, model_id, ring_ms, chat=True),
            log,
        )
    return log, ids


def ring_ms(tenant: Path) -> int:
    """How long a transfer rings before the call comes back, from the tenant's agent.yaml."""
    config = yaml.safe_load((tenant / "agent.yaml").read_text())
    return int(config["transfer"]["ring_seconds"]) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-url", help="public base URL of a bridge for eval runs")
    parser.add_argument("--bind-number", action="store_true", help="put the flow agent on 678")
    args = parser.parse_args()
    base_url = os.environ.get("RETELL_BRIDGE_URL", "").rstrip("/")
    if not base_url.startswith("https://"):
        print("set RETELL_BRIDGE_URL to the bridge's public https base URL", file=sys.stderr)
        return 2
    config = yaml.safe_load(Path("config/models.yaml").read_text())
    model_id, voice = live_profile(config)
    tenant = Path(os.environ.get("TENANTS_DIR", "tenants")) / "summit-air"
    specs = tool_specs(job_types(tenant))
    retell = Retell(os.environ["RETELL_API_KEY"])
    eval_url = args.eval_url.rstrip("/") if args.eval_url else None
    log, ids = push(
        retell,
        specs,
        base_url,
        model_id,
        voice,
        ring_ms(tenant),
        eval_url,
        local_names.load(tenant),
        args.bind_number,
    )
    print("\n".join(log) if log else "Retell already matches the code; nothing sent.")
    for kind, agent_id in ids.items():
        print(f"{kind}: {agent_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
