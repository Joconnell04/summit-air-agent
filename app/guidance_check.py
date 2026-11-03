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
from pathlib import Path
from typing import Protocol

from evals import models

from . import rulebook, rules
from .tenant import Tenant

PROFILE = "owner_edits.guidance_check"
PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "guidance-check.md"
UNAVAILABLE = "Couldn't check this right now, try again."
# The owner is waiting on the save. One retry inside this, then the save is refused.
TIMEOUT_SECONDS = 20
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


def check_with(model: models.Model, tenant: Tenant, items: dict[str, str]) -> dict[str, str]:
    """What's wrong with each item, by field; empty when all pass. An answer that doesn't cover
    every item, or any failure, refuses the save as unchecked."""
    try:
        reply = model.complete(
            PROMPT.read_text(), [{"role": "user", "content": message(tenant, items)}],
            max_tokens=600, json_schema=SCHEMA,
        )
        results = {str(r["id"]): r for r in json.loads(reply.text)["results"]}
        fields = dict(zip((str(n) for n in range(1, len(items) + 1)), items, strict=True))
        if set(results) != set(fields):
            return {"content": UNAVAILABLE}
        return {
            fields[n]: " ".join(r["reason"].split()) or "That runs into a locked rule."
            for n, r in results.items() if r["refuse"]
        }
    except Exception:  # noqa: BLE001 - any failure means the draft wasn't checked
        return {"content": UNAVAILABLE}


def real_model() -> models.Model:
    import anthropic

    cfg = models.profile(PROFILE)
    key = models.load_config()["providers"][cfg["provider"]]["key_env"]
    client = anthropic.Anthropic(
        api_key=os.environ.get(key), timeout=TIMEOUT_SECONDS, max_retries=1
    )
    return models.AnthropicModel(cfg, client)


def check(tenant: Tenant, items: dict[str, str]) -> dict[str, str]:
    """The check the server runs, on the named profile in config/models.yaml."""
    try:
        model = real_model()
    except Exception:  # noqa: BLE001 - no key or no client: refuse, never skip
        return {"content": UNAVAILABLE}
    return check_with(model, tenant, items)
