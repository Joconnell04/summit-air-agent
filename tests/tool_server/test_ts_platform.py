"""The envelope, logging and traces, retention, tenants and the contract's tool list (spec S-24,
S-30, S-37b, S-39, S-43, S-45, S-46)."""

import ast
import json
import re
import shutil
import subprocess
import sys
from datetime import timedelta

import yaml
from toolkit import ROOT, UNKNOWN, Agent, book, count, first_open, make_server, ready_to_book

from app import jobs
from app.tools import TOOLS

DEBUG = {"X-Debug-Token": "test-token"}
CONTRACT = ROOT / "docs" / "tools.md"


def test_the_tools_are_exactly_the_ones_in_the_contract(agent):
    """No delete, bulk, free query or outside call: the routes are the contract's list."""
    table = CONTRACT.read_text().split("## Tools the model can call")[1].split("##")[0]
    named = set()
    for row in re.findall(r"^\| `([^|]+)` \|", table, flags=re.MULTILINE):
        named |= {n.strip(" `") for n in row.split("/")}
    routes = {
        r.path.removeprefix("/tools/") for r in agent.app.routes if r.path.startswith("/tools/")
    }
    assert routes == named == set(TOOLS)
    lifecycle = {r.path for r in agent.app.routes if r.path.startswith(("/calls", "/healthz"))}
    assert lifecycle == {
        "/calls/start",
        "/calls/{call_id}/turn",
        "/calls/{call_id}/end",
        "/healthz",
        "/healthz/heartbeat",
    }


def test_every_answer_is_http_200_with_the_envelope(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    cases = [
        ("/tools/book_appointment", {"call_id": call}),
        ("/tools/find_slots", {"call_id": call, "job_type": "tune_up", "surprise": 1}),
        (
            "/tools/read_back",
            {
                "call_id": "no-such-call",
                "address": {"street": "1 A St", "zip": "11201"},
                "phone": "caller_id",
            },
        ),
        ("/calls/no-such-call/turn", {"speaker": "caller", "text": "hi"}),
        ("/calls/start", {}),
    ]
    for path, body in cases:
        response = agent.client.post(path, json=body)
        assert response.status_code == 200, path
        payload = response.json()
        assert set(payload) == {"ok", "say", "data", "refused"}, path
        assert payload["ok"] is False and payload["refused"], path
    raw = agent.client.post(
        "/tools/find_slots", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert raw.status_code == 200 and raw.json()["refused"] == "invalid_arguments"


def test_a_reused_idempotency_key_with_different_arguments_is_refused(agent):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    assert book(agent, call, slot, key="k")["ok"]
    other = book(agent, call, {"date": "2026-11-06", "window": "morning"}, key="k")
    assert other["refused"] == "idempotency_key_reused"


def test_a_tool_on_an_ended_call_is_refused(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.end(call)
    assert agent.tool("find_slots", call, job_type="tune_up")["refused"] == "call_ended"
    assert agent.end(call)["data"]["already_ended"] is True


def test_the_call_never_ends_in_the_same_turn_as_the_confirmation(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    too_soon = agent.tool("end_call", call, reason="resolved")
    assert too_soon["refused"] == "anything_else_first"
    assert too_soon["say"] == "Is there anything else I can help with?"
    agent.turn(call, "No, that's everything, thanks.")
    assert agent.tool("end_call", call, reason="resolved")["data"]["hang_up"] is True


def test_every_request_and_turn_is_one_json_line_with_ids_gate_and_timings(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat is out", stt_final_ms=180.0, llm_first_token_ms=420.0)
    agent.tool(
        "book_appointment",
        call,
        job_type="tune_up",
        slot={"date": "2026-11-03", "window": "morning"},
        details={"issue": "x", "since": "x", "tried": "x", "who_home": "x", "approver": "x"},
    )
    lines = [
        json.loads(line)
        for line in agent.app.state.services.settings.log_path.read_text().splitlines()
    ]
    assert [line["tool"] for line in lines] == ["calls.start", "turn", "book_appointment"]
    for line in lines:
        assert {
            "ts",
            "request_id",
            "call_id",
            "tool",
            "args",
            "ok",
            "refused",
            "gate",
            "result",
            "ms",
            "fsm",
            "error",
        } <= set(line)
        assert line["call_id"] == call
    assert lines[1]["args"]["timings"] == {"stt_final_ms": 180.0, "llm_first_token_ms": 420.0}
    assert lines[2]["gate"] == "gate_read_back"
    assert lines[0]["fsm"], "the FSM requests inside a tool call are timed"


def test_debug_endpoints_need_the_token(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    assert agent.client.get(f"/debug/calls/{call}").status_code == 401
    assert agent.client.get("/debug/logs", headers={"X-Debug-Token": "wrong"}).status_code == 401
    assert agent.client.get(f"/debug/calls/{call}", headers=DEBUG).status_code == 200
    assert agent.client.get("/debug/calls/nope", headers=DEBUG).status_code == 404


def test_the_call_timeline_shows_turns_tools_gates_refusals_and_outcome(agent):
    call = ready_to_book(agent)
    agent.turn(call, "Oh, and it smells like gas a little.")
    refused = book(agent, call, first_open(agent, call))
    assert refused["refused"] == "gate_safety"
    agent.end(call, "caller_hangup")
    timeline = agent.client.get(f"/debug/calls/{call}", headers=DEBUG).json()
    assert timeline["call"]["outcome"] == "safety"
    kinds = [(e["type"], e.get("tool") or e.get("speaker")) for e in timeline["events"]]
    assert ("turn", "caller") in kinds and ("tool", "book_appointment") in kinds
    tool_events = [e for e in timeline["events"] if e["type"] == "tool"]
    assert any(e["refused"] == "gate_safety" for e in tool_events)
    assert {f["kind"] for f in timeline["flags"]} >= {"safety", "safety_dropped"}

    errors = agent.client.get(
        "/debug/logs", params={"call_id": call, "errors": "true"}, headers=DEBUG
    ).json()
    assert {row["refused"] for row in errors["data"]} >= {"gate_safety"}
    by_tool = agent.client.get("/debug/logs", params={"tool": "read_back"}, headers=DEBUG).json()
    assert by_tool["count"] == 2
    one = agent.client.get(
        "/debug/logs", params={"request_id": by_tool["data"][0]["request_id"]}, headers=DEBUG
    )
    assert one.json()["count"] == 1


def test_make_trace_prints_the_timeline_as_plain_text(agent):
    call = ready_to_book(agent)
    agent.end(call)
    db = str(agent.app.state.services.settings.db_path)
    out = subprocess.run(
        [sys.executable, "-m", "app.trace", call, "--db", db],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert out.returncode == 0, out.stderr
    assert f"call {call}" in out.stdout
    assert "caller: Hi, the heat's out in my house." in out.stdout
    assert "tool read_back -> ok" in out.stdout
    missing = subprocess.run(
        [sys.executable, "-m", "app.trace", "nope", "--db", db],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert missing.returncode == 1


def test_retention_deletes_old_transcripts_and_records_outside_development(tmp_path, clock, mock):
    agent = Agent(make_server(tmp_path, clock, mock[0], environment="production"), clock)
    old = agent.start(UNKNOWN, "old")["data"]["call_id"]
    agent.turn(old, "My heat is out.")
    agent.end(old)
    clock.advance(days=31)
    recent = agent.start("(212) 555-0903", "recent")["data"]["call_id"]
    agent.turn(recent, "Booking a tune-up.")
    agent.end(recent)
    jobs.run_once(agent.app.state.services)
    assert count(agent, "SELECT COUNT(*) FROM turns WHERE call_id = 'old'") == 0
    assert count(agent, "SELECT COUNT(*) FROM turns WHERE call_id = 'recent'") == 1
    assert count(agent, "SELECT COUNT(*) FROM calls WHERE id = 'old'") == 1
    clock.advance(days=340)
    jobs.run_once(agent.app.state.services)
    assert count(agent, "SELECT COUNT(*) FROM calls WHERE id = 'old'") == 0
    assert count(agent, "SELECT COUNT(*) FROM tool_calls WHERE call_id = 'old'") == 0
    assert count(agent, "SELECT COUNT(*) FROM calls WHERE id = 'recent'") == 1


def test_development_keeps_everything(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "hello")
    agent.end(call)
    clock.advance(days=400)
    jobs.run_once(agent.app.state.services)
    assert count(agent, "SELECT COUNT(*) FROM turns") == 1


def test_a_second_tenant_is_served_from_a_copied_folder(tmp_path, clock, mock):
    tenants = tmp_path / "tenants"
    shutil.copytree(ROOT / "tenants" / "summit-air", tenants / "summit-air")
    shutil.copytree(ROOT / "tenants" / "summit-air", tenants / "peak-roofing")
    config = yaml.safe_load((tenants / "peak-roofing" / "config.yaml").read_text())
    config["company"]["name"] = "Peak Roofing"
    (tenants / "peak-roofing" / "config.yaml").write_text(yaml.safe_dump(config))
    agent_cfg = yaml.safe_load((tenants / "peak-roofing" / "agent.yaml").read_text())
    agent_cfg["phone_numbers"] = ["(555) 010-2000"]
    agent_cfg["lines"]["commercial"] = "Peak Roofing only does homes."
    (tenants / "peak-roofing" / "agent.yaml").write_text(yaml.safe_dump(agent_cfg))
    (tenants / "peak-roofing" / "knowledge" / "sign-off.md").write_text("Stay dry.")

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings

    settings = Settings(
        db_path=tmp_path / "t.db", tenants_dir=tenants, debug_token="t", jobs_interval_seconds=0
    )
    fsm = TestClient(mock[0])
    app = create_app(settings, clock=clock, fsm_clients={"summit-air": fsm, "peak-roofing": fsm})
    agent = Agent(app, clock)
    peak = agent.start(UNKNOWN, dialed="(555) 010-2000")["data"]
    assert peak["tenant"]["name"] == "Peak Roofing"
    assert peak["knowledge"]["sign-off"] == "Stay dry."
    lead = agent.tool(
        "record_lead",
        peak["call_id"],
        kind="commercial",
        details={"contact_name": "A", "need": "Office roof leak"},
    )
    assert lead["say"] == "Peak Roofing only does homes."
    summit = agent.start("(212) 555-0904", dialed="(646) 624-0418")["data"]
    assert summit["tenant"]["name"] == "Summit Air"


def _code_strings(path) -> str:
    """String literals and names in a module, without docstrings: what the code does, not the
    examples its comments give."""
    tree = ast.parse(path.read_text())
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    parts = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
    return "\n".join(parts)


def test_no_summit_air_fact_lives_in_the_template_code():
    config = yaml.safe_load((ROOT / "tenants" / "summit-air" / "config.yaml").read_text())
    agent_cfg = yaml.safe_load((ROOT / "tenants" / "summit-air" / "agent.yaml").read_text())
    facts = {
        config["company"]["name"],
        "Brooklyn",
        "Queens",
        "Staten Island",
        "HVAC",
        "gas",
        "furnace",
    }
    facts |= {z for zone in config["zones"] for z in zone["zips"]}
    facts |= {j["code"] for j in config["job_types"]}
    # safety_signal is a template condition a rule can use, so only the tenant's own rule ids count.
    facts |= {r["id"] for r in agent_cfg["priority"]["rules"]} - {"safety_signal"}
    numbers = {re.sub(r"\D", "", n)[-7:] for n in agent_cfg["phone_numbers"]}
    sources = [p for p in (ROOT / "app").rglob("*.py")]
    code = "\n".join(_code_strings(p) for p in sources) + (ROOT / "app" / "schema.sql").read_text()
    digits = re.sub(r"\D", "", code)
    for fact in facts:
        assert not re.search(rf"\b{re.escape(fact)}\b", code, flags=re.IGNORECASE), fact
    for number in numbers:
        assert number not in digits, number


def test_healthz_reports_database_fsm_and_worker_heartbeat(agent, clock):
    health = agent.client.get("/healthz").json()
    assert health["ok"] is True
    assert health["data"]["database"] is True
    assert health["data"]["fsm"] == {"summit-air": True}
    agent.client.post("/healthz/heartbeat", json={"worker": "livekit"})
    assert agent.client.get("/healthz").json()["data"]["workers"]["livekit"]["stale"] is False
    clock.now += timedelta(minutes=5)
    assert agent.client.get("/healthz").json()["data"]["workers"]["livekit"]["stale"] is True
