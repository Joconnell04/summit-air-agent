"""Builds what the model sees on a call from the files in this folder, so every voice layer runs
the same conversation: the instructions (the prompt files plus the call context the server returned
from /calls/start) and the tool list (descriptions from tools.yaml, arguments from the tool
server's own request models)."""

import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from app.tools import TOOLS

PROMPTS = Path(__file__).resolve().parent
# Order matters: who you are, how every call goes, the journeys, the tools, then safety last so it
# sits closest to the call context.
PARTS = ("identity.md", "spine.md", "journeys.md", "tool-use.md", "safety.md")


def instructions(start: dict, now: datetime | None = None) -> str:
    """The system prompt for one call. `start` is the data of the /calls/start result."""
    tenant = start["tenant"]
    text = "\n\n".join((PROMPTS / p).read_text().strip() for p in PARTS)
    text = text.replace("{company}", tenant["name"])
    local = (now or datetime.now(ZoneInfo(tenant["timezone"]))).astimezone(
        ZoneInfo(tenant["timezone"])
    )
    knowledge = "\n\n".join(body.strip() for body in start.get("knowledge", {}).values())
    context = {k: v for k, v in start.items() if k not in ("knowledge", "tenant", "call_id")}
    return (
        f"{text}\n\n# Company knowledge\n\n{knowledge}\n\n# This call\n\n"
        f"Now: {local:%A %-d %B %Y, %-I:%M %p} ({tenant['timezone']}).\n\n"
        f"What the server found before hello:\n{json.dumps(context, default=str)}\n"
    )


def tool_specs(job_types: list[str]) -> list[dict]:
    """One entry per model tool: name, description, JSON-schema parameters and whether it writes.
    `call_id` is left out; the voice layer adds it to every request. `job_types` are the tenant's
    job type codes, so the model can only name one the company has."""
    descriptions = yaml.safe_load((PROMPTS / "tools.yaml").read_text())
    specs = []
    for name, (_handler, model, write) in TOOLS.items():
        schema = _inline(model.model_json_schema())
        schema["properties"].pop("call_id", None)
        if "job_type" in schema["properties"]:
            schema["properties"]["job_type"]["enum"] = job_types
        schema["required"] = [r for r in schema.get("required", []) if r != "call_id"]
        specs.append(
            {
                "name": name,
                "description": descriptions[name],
                "parameters": schema,
                "write": write,
            }
        )
    return specs


def job_types(tenant_dir: Path) -> list[str]:
    """Active job type codes from the tenant's config.yaml."""
    config = yaml.safe_load((tenant_dir / "config.yaml").read_text())
    return [j["code"] for j in config["job_types"] if j["active"]]


def _inline(schema: dict) -> dict:
    """Resolve $ref into the schema itself and drop titles: some providers refuse $defs, and titles
    only add tokens."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("title", "$defs")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)
