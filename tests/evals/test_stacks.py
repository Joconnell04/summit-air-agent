from fakes import ScriptedModel, say, use

from app.tools import TOOLS
from evals.stacks import (
    STANDIN_PROMPT,
    InProcessAgent,
    idempotency_key,
    prompt_files,
    system_prompt,
    tool_specs,
)


def test_tool_specs_cover_every_model_tool_without_the_call_id():
    specs = {s["name"]: s for s in tool_specs()}
    assert set(specs) == set(TOOLS)
    for spec in specs.values():
        assert "call_id" not in spec["input_schema"]["properties"]
        assert "call_id" not in spec["input_schema"].get("required", [])
        assert spec["description"]


def test_prompt_files_skip_the_readme_and_fall_back_to_the_stand_in(tmp_path):
    (tmp_path / "README.md").write_text("x")
    assert prompt_files(tmp_path) == [STANDIN_PROMPT]
    (tmp_path / "20-booking.md").write_text("b")
    (tmp_path / "10-voice.md").write_text("a")
    assert [p.name for p in prompt_files(tmp_path)] == ["10-voice.md", "20-booking.md"]


def test_system_prompt_ends_with_the_call_context(tmp_path, monkeypatch):
    from evals import stacks

    monkeypatch.setattr(stacks, "_assemble", lambda: None)
    (tmp_path / "10-voice.md").write_text("Be kind.")
    text = system_prompt({"caller": {"known_number": False}}, tmp_path)
    assert text.startswith("Be kind.")
    assert '"known_number": false' in text


def test_the_voice_lanes_assemble_module_supplies_the_prompt_and_tools(monkeypatch):
    import sys
    import types

    from evals import stacks

    fake = types.ModuleType("prompts.assemble")
    fake.instructions = lambda start, now=None: f"Real prompt for {start['tenant']['name']} at {now}"
    fake.tool_specs = lambda job_types: [
        {"name": "find_slots", "description": "d", "parameters": {"type": "object",
         "properties": {"job_type": {"enum": job_types}}}, "write": False}
    ]
    monkeypatch.setitem(sys.modules, "prompts.assemble", fake)
    assert stacks.system_prompt({"tenant": {"name": "Summit Air"}}, now="9am") == (
        "Real prompt for Summit Air at 9am"
    )
    (spec,) = stacks.assembled_tool_specs("summit-air")
    assert spec["input_schema"]["properties"]["job_type"]["enum"] == [
        "no_heat", "no_cooling", "tune_up", "safety_check", "estimate"
    ]
    assert "write" not in spec


def test_the_worker_greeting_and_server_notes_come_from_assemble(monkeypatch):
    import sys
    import types

    from evals import stacks

    fake = types.ModuleType("prompts.assemble")
    fake.greeting = lambda start, voice: f"Hi, this is {start['tenant']['name']}."
    fake.server_notes = lambda hints: [f"[server] {h}" for h in hints]
    monkeypatch.setitem(sys.modules, "prompts.assemble", fake)
    monkeypatch.setattr(stacks.yaml, "safe_load", lambda text: {"voice": {"greeting_unknown": "x"}})
    assert stacks.greeting({"tenant": {"slug": "summit-air", "name": "Summit Air"}}) == (
        "Hi, this is Summit Air."
    )
    assert stacks.server_notes(["commercial"]) == ["[server] commercial"]


def test_without_assemble_there_is_no_fixed_greeting_and_notes_are_the_harness_own(monkeypatch):
    from evals import stacks

    monkeypatch.setattr(stacks, "_assemble", lambda: None)
    assert stacks.greeting({"tenant": {"slug": "summit-air"}}) is None
    assert stacks.server_notes(["commercial", "unknown"]) == [
        "(Server note: The server marked this caller as commercial; Summit Air doesn't book them.)"
    ]


def test_safety_refusals_and_the_sign_off_are_spoken_word_for_word():
    from evals.stacks import spoken_by_code

    assert spoken_by_code("book_appointment", {"refused": "gate_safety", "say": "Get out."}) == (
        "Get out."
    )
    assert spoken_by_code("end_call", {"ok": True, "say": "Please head outside."}) == (
        "Please head outside."
    )
    assert spoken_by_code("end_call", {"ok": True, "say": ""}) is None
    assert spoken_by_code("find_slots", {"ok": True, "say": "Tuesday works."}) is None


def test_prompt_files_include_the_tool_descriptions_once_there_are_prompts(tmp_path):
    (tmp_path / "spine.md").write_text("s")
    (tmp_path / "tools.yaml").write_text("t")
    assert [p.name for p in prompt_files(tmp_path)] == ["spine.md", "tools.yaml"]


def test_idempotency_key_is_stable_and_depends_on_the_arguments():
    a = idempotency_key("c1", "book_appointment", {"x": 1, "y": 2})
    assert a == idempotency_key("c1", "book_appointment", {"y": 2, "x": 1})
    assert a != idempotency_key("c1", "book_appointment", {"x": 2, "y": 2})


class StubServer:
    """Answers every tool with a safety-gate refusal and records the agent's lines."""

    def __init__(self):
        self.lines = []

    def start(self, call_id, caller_id):
        tenant = {"slug": "summit-air", "name": "Summit Air", "timezone": "America/New_York"}
        return {"ok": True, "say": "", "data": {"tenant": tenant, "knowledge": {}}}

    def turn(self, call_id, speaker, text, timings=None):
        if speaker == "agent":
            self.lines.append(text)
        return {"ok": True, "say": "", "data": {"hints": []}}

    def tool(self, name, call_id, args, key=None):
        return {"ok": False, "say": "Please get outside first.", "data": {},
                "refused": "gate_safety"}


def test_a_safety_gate_refusal_is_spoken_and_the_model_gets_no_reply_step():
    model = ScriptedModel([use("find_slots", "One moment.", job_type="no_heat"),
                           say("never said")])
    server = StubServer()
    agent = InProcessAgent(server, model, "c1", "(212) 555-0999")
    agent.start()
    turn = agent.hear("Book me now.")
    assert turn.text == "One moment. Please get outside first."
    assert server.lines[-1] == "One moment. Please get outside first."
    # The greeting is spoken from the tenant's voice lines; the one model call is the tool hop.
    assert len(model.calls) == 1


def test_a_stack_without_an_eval_adapter_says_so():
    import pytest

    from evals.stacks import load_adapter

    with pytest.raises(RuntimeError, match="no eval adapter"):
        load_adapter("nonesuch")


def test_the_retell_adapter_is_loaded_by_name():
    from evals.stacks import load_adapter

    assert load_adapter("retell").__name__ == "voice.retell.eval_adapter"


class PacedServer(StubServer):
    """Slows the call when the caller asks, and answers read_back with a slower address."""

    def __init__(self):
        super().__init__()
        self.speeds = []
        self.speed = 1.0

    def start(self, call_id, caller_id):
        started = super().start(call_id, caller_id)
        started["data"]["pace"] = {"speed": self.speed}
        return started

    def turn(self, call_id, speaker, text, timings=None):
        if speaker == "caller" and "slow down" in text:
            self.speed = 0.85
        if speaker == "agent":
            self.speeds.append((timings or {}).get("speed"))
        result = super().turn(call_id, speaker, text, timings)
        result["data"]["pace"] = {"speed": self.speed}
        return result

    def tool(self, name, call_id, args, key=None):
        parts = [{"text": "Your home at", "speed": 0.85},
                 {"text": "101 Union St, 11231,", "speed": 0.7},
                 {"text": "is that right?", "speed": 0.85}]
        return {"ok": True, "say": "Your home at 101 Union St, 11231, is that right?",
                "data": {"parts": parts}, "refused": None}


def test_every_agent_line_carries_the_servers_speed_and_a_slower_read_back_is_spoken_in_parts():
    model = ScriptedModel([say("Sure, I'll slow down."), use("read_back", "Okay.")])
    server = PacedServer()
    agent = InProcessAgent(server, model, "c1", "(212) 555-0999")
    agent.start()
    agent.hear("Can you slow down please?")
    turn = agent.hear("Can you read the address slower?")
    assert turn.text == "Okay. Your home at 101 Union St, 11231, is that right?"
    assert server.lines[1:] == ["Sure, I'll slow down.", "Okay.", "Your home at",
                                "101 Union St, 11231,", "is that right?"]
    assert server.speeds == [1.0, 0.85, 0.85, 0.85, 0.7, 0.85]
    # The parts were spoken from code: the model had no step after read_back.
    assert len(model.calls) == 2
