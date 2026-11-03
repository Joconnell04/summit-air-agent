"""What the Retell agent reads and says first on a call, from the same sources as the LiveKit
worker: the instructions from prompts/assemble.py and the spoken lines in the tenant's agent.yaml
(`voice:`). Retell takes them per call as dynamic variables: {{instructions}} is the whole prompt
and {{greeting}} the first thing it says."""

import json
from collections.abc import Callable
from pathlib import Path

import yaml


def spoken_lines(tenants_dir: Path, slug: str) -> dict:
    return yaml.safe_load((tenants_dir / slug / "agent.yaml").read_text())["voice"]


def first_name(full_name: str) -> str:
    return full_name.split()[0] if full_name.strip() else ""


def greeting(start: dict, lines: dict) -> str:
    """The same rule as the LiveKit worker: a first name only when exactly one home customer
    matched the number."""
    customers = [c for c in start.get("customers", []) if c.get("residential")]
    if len(customers) == 1 and first_name(customers[0]["name"]):
        return lines["greeting_known"].format(first_name=first_name(customers[0]["name"]))
    return lines["greeting_unknown"]


def opening(start: dict, say: str, lines: dict) -> str:
    """The first line. A call that carries on a dropped safety call opens with the safety lines;
    anything else the server wants said (repeated drops) follows the greeting."""
    safety = start.get("safety") or {}
    if safety.get("text"):
        return safety["text"]
    return f"{greeting(start, lines)} {say}".strip()


def variables(call_id: str, instructions: str, first_line: str) -> dict[str, str]:
    return {"summit_call_id": call_id, "instructions": instructions, "greeting": first_line}


def for_call(
    call_id: str,
    start: dict,
    instructions: Callable[[dict], str],
    spoken: Callable[[str], dict],
) -> dict[str, str]:
    """Dynamic variables for a call or chat, from its /calls/start answer."""
    data = start["data"]
    first = opening(data, start.get("say") or "", spoken(data["tenant"]["slug"]))
    return variables(call_id, instructions(data), first)


def server_down(call_id: str, lines: dict) -> dict[str, str]:
    """No lookup and no tools: say the server-down line and nothing else (the bridge then ends
    the call, as the LiveKit worker does)."""
    line = " ".join(lines["server_down"].split())
    return variables(call_id, f"Say only this, then stop: {json.dumps(line)}", line)
