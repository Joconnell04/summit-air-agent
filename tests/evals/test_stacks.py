from app.tools import TOOLS
from evals.stacks import (
    STANDIN_PROMPT,
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


def test_system_prompt_ends_with_the_call_context(tmp_path):
    (tmp_path / "10-voice.md").write_text("Be kind.")
    text = system_prompt({"caller": {"known_number": False}}, tmp_path)
    assert text.startswith("Be kind.")
    assert '"known_number": false' in text


def test_idempotency_key_is_stable_and_depends_on_the_arguments():
    a = idempotency_key("c1", "book_appointment", {"x": 1, "y": 2})
    assert a == idempotency_key("c1", "book_appointment", {"y": 2, "x": 1})
    assert a != idempotency_key("c1", "book_appointment", {"x": 2, "y": 2})
