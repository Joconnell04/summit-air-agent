"""Retell tool definitions come from the tool server's schemas (voice/retell/tool_schemas.py), so no tool
list can drift from what the server takes: read_back's job_type, callback_day's weekdays, facts.since
and details.also_noted each went missing from a hand-made list, and the model couldn't send them."""

import json
from pathlib import Path

from app.tools import TOOLS
from prompts.assemble import job_types, tool_specs
from voice.retell import build
from voice.retell import tool_schemas as retell_tools
from voice.retell_llm.llm_socket import arm_tools

ROOT = Path(__file__).resolve().parents[1]
SAVED = (
    ROOT / "retell" / "summit-air-jackson" / "tools.json",
    ROOT / "retell" / "archive" / "server-led" / "tools.json",
)


def test_every_server_tool_is_generated():
    assert {t["name"] for t in retell_tools.generated()} == set(TOOLS)


def test_the_saved_retell_tool_lists_have_no_drift():
    for path in SAVED:
        found, _ = retell_tools.drift(json.loads(path.read_text())["general_tools"])
        assert not found, f"{path.name}: {found}; run python -m voice.retell.tool_schemas sync"


def test_the_socket_uses_the_servers_own_specs():
    specs = tool_specs(job_types(ROOT / "tenants" / "summit-air"))
    socket = arm_tools(ROOT / "retell" / "socket" / "tools.yaml", specs)
    found, described = retell_tools.drift(build.custom_tools(socket, retell_tools.BASE_URL))
    assert not found
    # Its own descriptions only for the tools whose steps the flow runs.
    assert set(described) <= {"read_back", "book_appointment", "end_call"}


def _tool(**params):
    return {"name": "t", "description": "d", "parameters": {
        "type": "object", "properties": params, "required": sorted(params)}}


def test_drift_catches_a_missing_parameter_an_enum_and_a_required_list():
    want = [_tool(day={"type": "string", "enum": ["today", "monday"]}, job={"type": "string"})]
    have = [{"name": "t", "description": "mine", "parameters": {
        "type": "object", "properties": {"day": {"type": "string", "enum": ["today"]}},
        "required": ["day", "job"]}}]
    found, described = retell_tools.drift(have, want)
    assert any("missing parameter 'job'" in f for f in found)
    assert any(".day: enum" in f for f in found)
    assert described == ["t"]


def test_sync_fixes_the_drift_and_keeps_the_wording(monkeypatch):
    want = [_tool(day={"type": "string", "enum": ["today", "monday"]}, job={"type": "string"})]
    monkeypatch.setattr(retell_tools, "generated", lambda base_url=None: want)
    have = [{"name": "t", "description": "mine", "parameters": {
        "type": "object",
        "properties": {"day": {"type": "string", "description": "my words", "enum": ["today"]},
                       "old": {"type": "string"}},
        "required": ["day"]}}]
    fixed = retell_tools.synced(have)
    assert not retell_tools.drift(fixed, want)[0]
    assert fixed[0]["description"] == "mine"
    assert fixed[0]["parameters"]["properties"]["day"]["description"] == "my words"
    assert "old" not in fixed[0]["parameters"]["properties"]
