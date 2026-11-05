"""Create or update the native Retell agent, its LLM and its knowledge base, and the chat agent
used to review it in text. Safe to run again: everything is found by name, and only what differs
is sent. The baseline agent (voice/retell/push.py) is never touched.

    RETELL_BRIDGE_URL=https://... TRANSFER_OFFICE_NUMBER=+1... \\
      uv run --env-file .env python -m voice.retell_native.push [--eval-url https://.../eval]

The voice agent's functions and webhooks go to the bridge at RETELL_BRIDGE_URL. With --eval-url, a
chat agent on a twin LLM whose functions go to the eval bridge, so review chats never touch the
live schedule.
"""

import argparse
import os
from pathlib import Path

import yaml

from prompts import assemble
from voice import names as local_names
from voice.retell.api import Retell
from voice.retell.push import ensure_agent, ensure_voice, live_profile

from . import build

ROOT = Path(__file__).resolve().parents[2]
TENANT = ROOT / "tenants" / "summit-air"
CHAT_AGENT_NAME = "Summit Air chat (Retell native, review)"


def ensure_knowledge(retell: Retell, texts: list[dict], log: list[str]) -> str:
    name = build.knowledge_name(texts)
    existing = next((kb for kb in retell.request("GET", "/list-knowledge-bases")
                     if kb.get("knowledge_base_name") == name), None)
    if existing:
        return existing["knowledge_base_id"]
    created = retell.create_knowledge_base(name, texts)
    log.append(f"created knowledge base {created['knowledge_base_id']} ({name})")
    return created["knowledge_base_id"]


def push(retell: Retell, base_url: str, office_number: str, eval_url: str | None = None,
         next_copy: bool = False) -> list[str]:
    """next_copy builds "Summit Air (Retell native, next)" with the changes not yet in the slate
    (the letter tools), leaving the slate's agent as it is."""
    config = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    model_id, voice = live_profile(config)
    specs = assemble.tool_specs(assemble.job_types(TENANT))
    names = local_names.load(TENANT)
    log: list[str] = []
    kb = ensure_knowledge(retell, build.knowledge(TENANT), log)
    voice_id = ensure_voice(retell, voice["voice_id"], voice["name"])
    name = build.NEXT_NAME if next_copy else build.AGENT_NAME
    agent_id = ensure_agent(
        retell, "agent", name,
        lambda llm_id: build.agent_body(llm_id, base_url, voice_id, voice["model"], names, name),
        build.llm_body(TENANT, specs, base_url, model_id, office_number, kb, next_copy), log,
    )
    log.append(f"voice agent {agent_id}")
    if eval_url:
        chat_id = ensure_agent(
            retell, "chat-agent", CHAT_AGENT_NAME,
            lambda llm_id: {"agent_name": CHAT_AGENT_NAME,
                            "response_engine": {"type": "retell-llm", "llm_id": llm_id},
                            "webhook_url": f"{eval_url}/retell/webhook",
                            "webhook_events": ["chat_started", "chat_ended", "transcript_updated"]},
            build.llm_body(TENANT, specs, eval_url, model_id, office_number, kb, next_copy), log,
        )
        log.append(f"review chat agent {chat_id}")
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-url", help="public base URL of the eval bridge, for review chats")
    parser.add_argument("--next", action="store_true", help="build the copy with the next changes")
    args = parser.parse_args()
    base_url = os.environ.get("RETELL_BRIDGE_URL", "").rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("set RETELL_BRIDGE_URL to the bridge's public https URL")
    office = os.environ.get("TRANSFER_OFFICE_NUMBER", "")
    if not office.startswith("+"):
        raise SystemExit("set TRANSFER_OFFICE_NUMBER (E.164) for the built-in transfer")
    retell = Retell(os.environ["RETELL_API_KEY"])
    for line in push(retell, base_url, office, args.eval_url, args.next):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
