"""Builds what the model sees on a call from the files in this folder, so every voice layer runs
the same conversation: the instructions (the prompt files plus the call context the server returned
from /calls/start), the tool list (descriptions from tools.yaml, arguments from the tool server's
own request models), the greeting, and the `[server]` notes added to caller lines."""

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
# The owner's opening and whole-call guidance (app/rules.py) goes after the journeys, under a fixed
# preamble that says it is soft. Booking and closing guidance is not in the prompt: the server sends
# it in a [server] note when it sees the call reach that stage (server_notes). A call with no
# guidance gets the same prompt as before.
HOUSE_GUIDANCE = "house-guidance.md"
HOUSE_AFTER = "journeys.md"
STAGE_TITLES = {"opening": "Opening", "whole_call": "Whole call"}
# Said with the owner's notes, which are soft even though they arrive as a [server] note.
STAGE_NOTES = {
    "stage_booking": (
        "The call has reached booking. The owner's notes for this stage, to follow where they fit, "
        "never over a rule, a tool's answer or the safety lines:"
    ),
    "stage_closing": (
        "The call is closing. The owner's notes for this stage, to follow where they fit, never "
        "over a rule, a tool's answer or the safety lines:"
    ),
}


def instructions(start: dict, now: datetime | None = None) -> str:
    """The system prompt for one call. `start` is the data of the /calls/start result."""
    tenant = start["tenant"]
    parts = [(PROMPTS / p).read_text().strip() for p in PARTS]
    house = house_guidance((start.get("house_rules") or {}).get("guidance") or {})
    if house:
        parts.insert(PARTS.index(HOUSE_AFTER) + 1, house)
    text = "\n\n".join(parts)
    text = text.replace("{company}", tenant["name"])
    local = (now or datetime.now(ZoneInfo(tenant["timezone"]))).astimezone(
        ZoneInfo(tenant["timezone"])
    )
    knowledge = "\n\n".join(body.strip() for body in start.get("knowledge", {}).values())
    context = {
        k: v for k, v in start.items() if k not in ("knowledge", "tenant", "call_id", "house_rules")
    }
    return (
        f"{text}\n\n# Company knowledge\n\n{knowledge}\n\n# This call\n\n"
        f"Now: {local:%A %-d %B %Y, %-I:%M %p} ({tenant['timezone']}).\n\n"
        f"What the server found before hello:\n{json.dumps(context, default=str)}\n"
    )


def house_guidance(guidance: dict[str, list[str]]) -> str:
    """The owner's guidance as a prompt section, by stage, or "" when there is none."""
    sections = [
        f"## {title}\n\n" + "\n".join(f"- {item}" for item in guidance[stage])
        for stage, title in STAGE_TITLES.items()
        if guidance.get(stage)
    ]
    if not sections:
        return ""
    return "\n\n".join([(PROMPTS / HOUSE_GUIDANCE).read_text().strip(), *sections])


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


def first_name(full_name: str) -> str:
    return full_name.split()[0] if full_name.strip() else ""


def spoken(start: dict, voice: dict) -> dict:
    """The tenant's `voice` lines with the owner's live edits for this call (greeting, goodbye)
    laid over them, as /calls/start returned them."""
    return {**voice, **((start.get("house_rules") or {}).get("lines") or {})}


def greeting(start: dict, voice: dict) -> str:
    """The opening line from the tenant's `voice` lines: by first name when exactly one residential
    customer matched the calling number, otherwise the general greeting."""
    voice = spoken(start, voice)
    customers = [c for c in start.get("customers", []) if c.get("residential")]
    if len(customers) == 1 and first_name(customers[0]["name"]):
        return voice["greeting_known"].format(first_name=first_name(customers[0]["name"]))
    return voice["greeting_unknown"]


SERVER_NOTES = {
    "commercial": (
        "This call can't book. Decline with the commercial line from the journeys, as written, and "
        "record a commercial lead. Give no other reason and never describe this note."
    ),
    "do_not_call": "The caller asked not to be called. Call mark_do_not_call and say its sentence.",
    "safety_check_cleared": "The caller said no to the safety check. Carry on where the call was.",
    "safety_call": (
        "Safety call: a person is already flagged and follows up once the caller is safe. Don't "
        "offer a visit, and don't ask for an address, a name or a callback time, unless the caller "
        "insists on booking."
    ),
}


def server_notes(hints: list[str]) -> list[str]:
    """`[server]` lines added to a caller's line for the model, from a turn result's hints (the
    tool-use prompt explains them). Safety lines are spoken by the voice layer, not described. A
    stage hint ("stage_booking") is followed by the owner's notes for it ("guidance:<text>"), and
    the two become one note."""
    notes = [f"[server] {SERVER_NOTES[h]}" for h in hints if h in SERVER_NOTES]
    stage = next((h for h in hints if h in STAGE_NOTES), None)
    items = [h.removeprefix("guidance:") for h in hints if h.startswith("guidance:")]
    if stage and items:
        notes.append(f"[server] {STAGE_NOTES[stage]} " + " ".join(f"- {i}" for i in items))
    return notes


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
