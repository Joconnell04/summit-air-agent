"""The prompt files and tool list both voice layers load (prompts/)."""

import json
import re
from datetime import UTC, datetime

from app.tools import TOOLS
from prompts import assemble
from tests.tool_server.toolkit import TENANT_DIR


def test_instructions_name_the_company_from_the_call_context(agent):
    start = agent.start()["data"]
    text = assemble.instructions(start, datetime(2026, 11, 2, 14, 0, tzinfo=UTC))
    assert "{company}" not in text
    assert start["tenant"]["name"] in text
    assert "Monday 2 November 2026, 9:00 AM" in text
    # The tenant's knowledge comes in through the call context, not the prompt files.
    assert "Stay warm out there" in text


def test_no_company_fact_is_written_into_the_prompt_files():
    for path in assemble.PROMPTS.glob("*.md"):
        if path.name == "README.md":
            continue
        assert "Summit" not in path.read_text(), path.name
        assert not re.search(r"\(\d{3}\) \d{3}-\d{4}", path.read_text()), path.name


def test_every_server_tool_has_a_description_and_its_arguments():
    specs = {s["name"]: s for s in assemble.tool_specs(assemble.job_types(TENANT_DIR))}
    assert set(specs) == set(TOOLS)
    for name, spec in specs.items():
        assert spec["description"], name
        assert "call_id" not in spec["parameters"]["properties"], name
        assert "$ref" not in json.dumps(spec["parameters"]), name
        assert spec["write"] == TOOLS[name][2], name


def test_job_types_are_limited_to_the_tenants_own():
    specs = {s["name"]: s for s in assemble.tool_specs(assemble.job_types(TENANT_DIR))}
    allowed = specs["book_appointment"]["parameters"]["properties"]["job_type"]["enum"]
    assert set(allowed) == {"no_heat", "no_cooling", "tune_up", "safety_check", "estimate"}
