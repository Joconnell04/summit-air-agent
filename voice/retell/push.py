"""Push the Retell agent: its LLM (prompt slot, model, custom functions), the voice agent, the 678
number's binding, and optionally the chat agent the eval harness drives.

Idempotent: it finds what exists by agent name, compares field by field (only the fields we set,
build.contains), and writes only what differs, so a second run sends no writes. Only the endpoints
in api.ALLOWED are used.

Run: make retell-push                         (RETELL_BRIDGE_URL is the bridge's public base URL)
     make retell-push EVAL_URL=https://...    (also the chat agent, pointed at an eval bridge)
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

from prompts.assemble import job_types, tool_specs
from voice import names as local_names

from . import build
from .api import Retell

PHONE_NUMBER = "+16783366730"


def find_agent(retell: Retell, name: str) -> dict | None:
    matches = [a for a in retell.list_all("POST", "/v2/list-agents") if a.get("agent_name") == name]
    if len(matches) > 1:
        raise SystemExit(f"more than one Retell agent is named {name!r}; remove the extra")
    return matches[0] if matches else None


def ensure_voice(retell: Retell, provider_voice_id: str, name: str) -> str:
    """The LiveKit stack's Cartesia voice, added to Retell by its Cartesia id once."""
    for voice in retell.request("GET", "/list-voices"):
        if (
            voice.get("provider") == "cartesia"
            and voice.get("voice_type") == "custom"
            and voice.get("voice_name") == name
        ):
            return voice["voice_id"]
    body = {
        "voice_provider": "cartesia",
        "provider_voice_id": provider_voice_id,
        "voice_name": name,
    }
    return retell.request("POST", "/add-community-voice", json=body)["voice_id"]


def ensure_llm(retell: Retell, llm_id: str | None, desired: dict, log: list[str]) -> str:
    if llm_id is None:
        llm = retell.request("POST", "/create-retell-llm", json=desired)
        log.append(f"created LLM {llm['llm_id']}")
        return llm["llm_id"]
    current = retell.request("GET", f"/get-retell-llm/{llm_id}")
    diff = build.changes(current, desired)
    if diff:
        retell.request("PATCH", f"/update-retell-llm/{llm_id}", json=diff)
        log.append(f"updated LLM {llm_id}: {', '.join(sorted(diff))}")
    return llm_id


def ensure_agent(retell: Retell, kind: str, name: str, body_for, llm_body: dict, log: list[str]):
    """kind is "agent" (voice) or "chat-agent"."""
    listed = find_agent(retell, name)
    # The list leaves out the response engine; the agent itself has it.
    current = retell.request("GET", f"/get-{kind}/{listed['agent_id']}") if listed else None
    llm_id = ensure_llm(
        retell, current["response_engine"]["llm_id"] if current else None, llm_body, log
    )
    desired = body_for(llm_id)
    if current is None:
        agent = retell.request("POST", f"/create-{kind}", json=desired)
        log.append(f"created {kind} {agent['agent_id']}")
        return agent["agent_id"]
    agent = current
    diff = build.changes(current, desired)
    if diff:
        retell.request("PATCH", f"/update-{kind}/{agent['agent_id']}", json=diff)
        log.append(f"updated {kind} {agent['agent_id']}: {', '.join(sorted(diff))}")
    return agent["agent_id"]


def ensure_number(retell: Retell, agent_id: str, base_url: str, log: list[str]) -> None:
    numbers = retell.list_all("GET", "/v2/list-phone-numbers")
    number = next((n for n in numbers if n["phone_number"] == PHONE_NUMBER), None)
    if number is None:
        raise SystemExit(f"{PHONE_NUMBER} is not on this Retell account")
    diff = build.changes(number, build.phone_body(agent_id, base_url))
    if diff:
        retell.request("PATCH", f"/update-phone-number/{PHONE_NUMBER}", json=diff)
        log.append(f"bound {PHONE_NUMBER}: {', '.join(sorted(diff))}")


def push(
    retell: Retell,
    specs: list[dict],
    base_url: str,
    model_id: str,
    voice: dict,
    eval_url: str | None = None,
    names: local_names.Names | None = None,
) -> list[str]:
    """Bring Retell in line with the code. Returns what changed; empty when nothing did."""
    log: list[str] = []
    voice_id = ensure_voice(retell, voice["voice_id"], voice["name"])
    agent_id = ensure_agent(
        retell,
        "agent",
        build.AGENT_NAME,
        lambda llm_id: build.agent_body(llm_id, base_url, voice_id, voice["model"], names),
        build.llm_body(specs, base_url, model_id),
        log,
    )
    ensure_number(retell, agent_id, base_url, log)
    if eval_url:
        ensure_agent(
            retell,
            "chat-agent",
            build.CHAT_AGENT_NAME,
            lambda llm_id: build.chat_agent_body(llm_id, eval_url),
            build.llm_body(specs, eval_url, model_id),
            log,
        )
    return log


def live_profile(config: dict) -> tuple[str, dict]:
    """The live model the LiveKit stack runs, and the voice for Retell's agents: `retell.voice`
    when set (Katie, kept for Jackson's comparison call), else the LiveKit stack's, all from
    config/models.yaml."""
    profile = config["profiles"][os.environ.get("MODEL_PROFILE") or config["default"]]
    if profile["provider"] != "anthropic":
        raise SystemExit("the Retell baseline runs the Anthropic profiles only")
    tts = (config.get("retell") or {}).get("voice") or config["voice"]["tts"]
    voice = {"voice_id": tts["voice_id"], "model": tts["model"], "name": tts["name"]}
    return profile["model"], voice


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-url", help="public base URL of a bridge for eval runs")
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
    log = push(retell, specs, base_url, model_id, voice, eval_url, local_names.load(tenant))
    print("\n".join(log) if log else "Retell already matches the code; nothing sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
