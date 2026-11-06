"""Retell tool definitions built from the tool server's own schemas, and drift against them.

    uv run python -m voice.retell.tool_schemas build [--base URL]     print the definitions as JSON
    uv run python -m voice.retell.tool_schemas diff <tools.json>      drift of a saved tool list
    uv run python -m voice.retell.tool_schemas sync <tools.json>      its parameters from the server,
                                                                 keeping its descriptions
    uv run --env-file .env python -m voice.retell.tool_schemas check  drift of the live Retell agents

The definitions come from `prompts.assemble.tool_specs` (app/schemas.py `TOOLS`: names, parameters,
nested objects, enums, required fields, and the descriptions in prompts/tools.yaml) through
`voice.retell.build.custom_tools`, the same path the Retell push uses. A tool list made by hand
drifted from the server three times (read_back's job_type, callback_day's weekdays, facts.since and
details.also_noted), and each time the model couldn't send what the server needed.

Drift is structural: a tool missing or extra, a parameter missing or extra at any depth, a
different enum, a different required list. Descriptions are reported apart: the hosted agent's
are Jackson's own wording and may differ on purpose. The socket uses the server's specs directly,
so it can't drift.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from prompts.assemble import job_types, tool_specs
from voice.retell import build

ROOT = Path(__file__).resolve().parents[2]
TENANT = ROOT / "tenants" / "summit-air"
BASE_URL = "https://45-77-105-247.sslip.io"
# The live agents whose tools must match: Jackson's hosted agent's LLM.
LIVE_LLMS = {"Summit Air Jackson": "llm_12eba5f1d4a3b067f0635097de30"}


def generated(base_url: str = BASE_URL) -> list[dict]:
    return build.custom_tools(tool_specs(job_types(TENANT)), base_url)


def _resolve(schema: dict) -> dict:
    """The non-null branch of an optional field."""
    for key in ("anyOf", "oneOf"):
        if key in schema:
            branches = [b for b in schema[key] if b.get("type") != "null"]
            if len(branches) == 1:
                return _resolve(branches[0])
    return schema


def _walk(tool: str, want: dict, have: dict, path: str, out: list[str]) -> None:
    want, have = _resolve(want), _resolve(have)
    if want.get("type") == "array" or have.get("type") == "array":
        _walk(tool, want.get("items") or {}, have.get("items") or {}, f"{path}[]", out)
        return
    if set(want.get("enum") or []) != set(have.get("enum") or []):
        out.append(f"{tool}{path}: enum {sorted(have.get('enum') or [])} should be "
                   f"{sorted(want.get('enum') or [])}")
    wp, hp = want.get("properties") or {}, have.get("properties") or {}
    for name in wp.keys() - hp.keys():
        out.append(f"{tool}{path}: missing parameter '{name}'")
    for name in hp.keys() - wp.keys():
        out.append(f"{tool}{path}: unknown parameter '{name}'")
    if set(want.get("required") or []) != set(have.get("required") or []):
        out.append(f"{tool}{path}: required {sorted(have.get('required') or [])} should be "
                   f"{sorted(want.get('required') or [])}")
    for name in wp.keys() & hp.keys():
        _walk(tool, wp[name], hp[name], f"{path}.{name}", out)


def drift(tools: list[dict], expected: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """Structural drift of `tools` against the generated definitions, and the tools whose
    description differs."""
    expected = expected or generated()
    want = {t["name"]: t for t in expected}
    have = {t["name"]: t for t in tools}
    out = [f"missing tool '{n}'" for n in want.keys() - have.keys()]
    out += [f"unknown tool '{n}'" for n in have.keys() - want.keys()]
    for name in sorted(want.keys() & have.keys()):
        _walk(name, want[name].get("parameters") or {}, have[name].get("parameters") or {}, "", out)
    described = sorted(n for n in want.keys() & have.keys()
                       if want[n].get("description") != have[n].get("description"))
    return out, described


def _node(schema: dict) -> dict:
    """The dict to edit for an optional field: its one non-null anyOf branch, or itself."""
    for key in ("anyOf", "oneOf"):
        branches = [b for b in schema.get(key, []) if b.get("type") != "null"]
        if len(branches) == 1:
            return _node(branches[0])
    return schema


def _patch(want: dict, have: dict) -> None:
    """Fix in `have`, in place, exactly what drift() reports against `want`: missing and unknown
    parameters, enums and required lists, at every depth. Everything else in `have` stays."""
    want, node = _resolve(want), _node(have)
    if want.get("type") == "array" or node.get("type") == "array":
        _patch(want.get("items") or {}, node.setdefault("items", {}))
        return
    for key in ("enum", "required"):
        if set(want.get(key) or []) != set(node.get(key) or []):
            if want.get(key):
                node[key] = list(want[key])
            else:
                node.pop(key, None)
    wp = want.get("properties") or {}
    hp = node.get("properties")
    if hp is None and not wp:
        return
    hp = node.setdefault("properties", {})
    for name in list(hp):
        if name not in wp:
            del hp[name]
    for name, sub in wp.items():
        if name in hp:
            _patch(sub, hp[name])
        else:
            hp[name] = sub


def synced(tools: list[dict], base_url: str = BASE_URL) -> list[dict]:
    """A saved tool list with its drift fixed (and any tool it lacks added from the server),
    keeping its own descriptions, wording and settings."""
    have = {t["name"]: t for t in tools}
    out = []
    for want in generated(base_url):
        mine = have.get(want["name"])
        if mine is None:
            out.append(want)
            continue
        mine = json.loads(json.dumps(mine))
        _patch(want["parameters"], mine.setdefault("parameters", {}))
        out.append(mine)
    return out


def _report(label: str, tools: list[dict]) -> int:
    found, described = drift(tools)
    for line in found:
        print(f"{label}: {line}")
    print(f"{label}: {len(found)} drift, {len(described)} descriptions differ"
          + (f" ({', '.join(described)})" if described else ""))
    return len(found)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    built = sub.add_parser("build")
    built.add_argument("--base", default=BASE_URL)
    diffed = sub.add_parser("diff")
    diffed.add_argument("path")
    syncing = sub.add_parser("sync")
    syncing.add_argument("path")
    sub.add_parser("check")
    args = parser.parse_args()
    if args.action == "build":
        print(json.dumps({"general_tools": generated(args.base)}, indent=2))
        return 0
    if args.action == "sync":
        path = Path(args.path)
        saved = json.loads(path.read_text())
        saved["general_tools"] = synced(saved["general_tools"])
        path.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")
        return _report(args.path, saved["general_tools"])
    if args.action == "diff":
        tools = json.loads(Path(args.path).read_text())["general_tools"]
        return 1 if _report(args.path, tools) else 0
    from voice.retell.api import Retell

    retell = Retell(os.environ["RETELL_API_KEY"])
    total = 0
    for label, llm_id in LIVE_LLMS.items():
        llm = retell.request("GET", f"/get-retell-llm/{llm_id}")
        total += _report(label, llm.get("general_tools") or [])
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
