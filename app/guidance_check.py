"""The check on an owner's new or changed notes and lines when a draft is saved (app/rules.py).

One model call, given the locked rules from the rulebook, answers whether any item contradicts a
locked rule or tells the agent to skip or override one, and why. Only that refuses a draft: a
note that merely mentions a subject passes. If the check can't run, the save is refused rather
than let through unchecked. This check is for the owner's benefit, not the containment: the
server gates hold whatever the prompt says, and the eval gate runs the must-pass calls on every
draft (docs/template.md, "Owner edits").
"""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
import yaml

from . import rulebook, rules
from .tenant import Tenant

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
PROFILE = "guidance_check"  # under owner_edits in config/models.yaml
PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "guidance-check.md"
UNAVAILABLE = "Couldn't check this right now, try again."
# The owner is waiting on the save (ASSUMED budget; the live check measured about 1 to 2 s an item,
# with one at 5.9 s). A request is tried at most twice, then the save is refused.
TIMEOUT_SECONDS = 15
ATTEMPTS = 2
SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "refuse": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "refuse", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


class Check(Protocol):
    def __call__(self, tenant: Tenant, items: dict[str, str]) -> dict[str, str]: ...


# Given the system prompt and the user message, the model's JSON answer as text.
Ask = Callable[[str, str], str]


def load_profile(path: Path = CONFIG_PATH) -> dict:
    config = yaml.safe_load(path.read_text())
    cfg = dict(config["owner_edits"][PROFILE])
    cfg["key_env"] = config["providers"][cfg["provider"]]["key_env"]
    return cfg


def locked_rules(tenant: Tenant) -> list[str]:
    """The locked rules in plain lines, from the rulebook: every server check, the read-back, the
    safety lines, the fees the agent may say and where the company works."""
    shown = {"line.read_back", "setting.fees"}
    found = []
    for rule in rulebook.rulebook(tenant):
        safety_lines = rule.id.startswith("safety.") and rule.id.endswith(".lines")
        if rule.kind == "server" or rule.id in shown or safety_lines:
            found.append(f"{rule.title}: {rule.detail}" if rule.detail else rule.title)
    found.append(rules.LOCKED["read_back"])
    found.append(rules.LOCKED["priority"])
    zones = sorted({zone["name"] for zone in tenant.service_area.values()})
    found.append(f"Works only on homes, and only in these areas: {', '.join(zones)}")
    return found


def _label(field: str) -> str:
    kind, _, rest = field.partition(".")
    if kind == "lines":
        return f"{rules.EDITABLE_LINES[rest][2]} line"
    stage = rest.split(".")[0]
    return f"{rules.STAGES[stage]} note"


def message(tenant: Tenant, items: dict[str, str]) -> str:
    locked = "\n".join(f"- {r}" for r in locked_rules(tenant))
    listed = "\n".join(
        f"{n}. ({_label(field)}) {text}" for n, (field, text) in enumerate(items.items(), 1)
    )
    return f"Locked rules:\n{locked}\n\nItems:\n{listed}"


def check_with(ask: Ask, tenant: Tenant, items: dict[str, str]) -> dict[str, str]:
    """What's wrong with each item, by field; empty when all pass. An answer that doesn't cover
    every item, or any failure, refuses the save as unchecked."""
    try:
        results = {str(r["id"]): r for r in json.loads(ask(PROMPT.read_text(),
                                                          message(tenant, items)))["results"]}
        fields = {str(n): field for n, field in enumerate(items, 1)}
        if set(results) != set(fields):
            return {"content": UNAVAILABLE}
        return {
            fields[n]: " ".join(r["reason"].split()) or "That runs into a locked rule."
            for n, r in results.items() if r["refuse"]
        }
    except Exception:  # noqa: BLE001 - any failure means the draft wasn't checked
        return {"content": UNAVAILABLE}


def anthropic_ask(cfg: dict, key: str, http: httpx.Client) -> Ask:
    """One Messages API call with thinking off and the answer held to SCHEMA."""

    def ask(system: str, user: str) -> str:
        body = {
            "model": cfg["model"],
            "max_tokens": 600,
            "thinking": {"type": "disabled"},
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        }
        for attempt in range(ATTEMPTS):
            try:
                r = http.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
                r.raise_for_status()
                break
            except httpx.HTTPError:
                if attempt == ATTEMPTS - 1:
                    raise
        return "".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text")

    return ask


def check(tenant: Tenant, items: dict[str, str]) -> dict[str, str]:
    """The check the server runs, on the named profile in config/models.yaml."""
    cfg = load_profile()
    key = os.environ.get(cfg["key_env"], "")
    if cfg["provider"] != "anthropic" or not key:
        return {"content": UNAVAILABLE}
    with httpx.Client(timeout=TIMEOUT_SECONDS) as http:
        return check_with(anthropic_ask(cfg, key, http), tenant, items)
