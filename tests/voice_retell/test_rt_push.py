"""The push script: config as code, idempotent, versioned list endpoints only."""

import json
from pathlib import Path

import pytest
import yaml
from fake_retell import FakeRetell

from app.tools import TOOLS
from voice import names as local_names
from voice.retell import build
from voice.retell.api import Retell, RetellError, allowed
from voice.retell.push import PHONE_NUMBER, push

BASE = "https://bridge.example.com"
MODELS_CONFIG = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "config" / "models.yaml").read_text()
)
VOICE = {k: MODELS_CONFIG["voice"]["tts"][k] for k in ("voice_id", "model", "name")}
# The voice on the recorded account (tests/voice_retell/recorded), from before issue #149.
OLD_AGENT = "agent_8e083a54718dcabbbe2c5c4f3d"
MODELS = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


def specs(note: str = "") -> list[dict]:
    """The shape prompts/assemble.tool_specs returns."""
    out = []
    for name, (handler, model, write) in TOOLS.items():
        schema = model.model_json_schema()
        schema["properties"].pop("call_id")
        out.append(
            {
                "name": name,
                "description": (handler.__doc__ or name) + note,
                "parameters": schema,
                "write": write,
            }
        )
    return out


def account() -> FakeRetell:
    """The account as found: the 678 number bound to the earlier practice agent."""
    return FakeRetell(
        {
            "numbers": [
                {
                    "phone_number": PHONE_NUMBER,
                    "phone_number_type": "retell-twilio",
                    "inbound_agents": [
                        {"weight": 1, "agent_id": OLD_AGENT, "agent_version": "latest"}
                    ],
                }
            ],
            "voices": [
                {
                    "voice_id": "cartesia-Katie",
                    "provider": "cartesia",
                    "voice_type": "standard",
                    "voice_name": "Katie",
                }
            ],
        }
    )


def run(fake: FakeRetell, **kwargs) -> list[str]:
    return push(fake.client(), specs(**kwargs), BASE, "claude-haiku-5-5", VOICE)


def test_a_first_push_creates_the_llm_agent_and_voice_and_binds_the_number():
    fake = account()
    log = run(fake)
    assert any(line.startswith("created LLM") for line in log)
    assert any(line.startswith("created agent") for line in log)
    number = fake.numbers[0]
    agent_id = next(iter(fake.agents))
    assert number["inbound_agents"] == [
        {"agent_id": agent_id, "agent_version": "latest", "weight": 1}
    ]
    assert number["inbound_webhook_url"] == f"{BASE}/retell/inbound"
    llm = next(iter(fake.llms.values()))
    assert llm["model_config"]["primary"]["model"] == "claude-5.5-haiku"
    assert llm["general_prompt"] == "{{instructions}}"
    assert {t["name"] for t in llm["general_tools"]} == set(TOOLS)
    assert all(t["url"] == f"{BASE}/retell/tools/{t['name']}" for t in llm["general_tools"])
    agent = fake.agents[agent_id]
    assert agent["voice_id"].startswith("custom_voice")
    assert agent["webhook_url"] == f"{BASE}/retell/webhook"


def test_a_second_push_sends_no_writes():
    fake = account()
    run(fake)
    before = len(fake.writes())
    assert run(fake) == []
    assert len(fake.writes()) == before


def test_a_changed_tool_description_updates_only_the_llm():
    fake = account()
    run(fake)
    before = len(fake.writes())
    log = run(fake, note=" Changed.")
    assert log == [f"updated LLM {next(iter(fake.llms))}: general_tools"]
    assert len(fake.writes()) == before + 1


def test_the_tenants_local_names_reach_the_retell_agent():
    names = local_names.load(Path(__file__).resolve().parents[2] / "tenants" / "summit-air")
    fake = account()
    push(fake.client(), specs(), BASE, "claude-haiku-5-5", VOICE, names=names)
    agent = next(iter(fake.agents.values()))
    assert "Kosciuszko Street" in agent["boosted_keywords"]
    assert "Summit Air" in agent["boosted_keywords"]
    houston = next(e for e in agent["pronunciation_dictionary"] if e["word"] == "Houston Street")
    assert houston == {"word": "Houston Street", "alphabet": "ipa", "phoneme": "ˈhaʊstən ˈstɹit"}


def test_an_agent_without_names_sends_no_pronunciation_dictionary():
    model = yaml.safe_load(MODELS.read_text())["voice"]["tts"]["model"]
    body = build.agent_body("llm_1", BASE, "custom_voice_1", model, local_names.Names([]))
    assert "pronunciation_dictionary" not in body
    assert body["boosted_keywords"] == build.SPEECH["boosted_keywords"]


def test_no_builtin_end_call_or_transfer_tool_is_pushed():
    tools = build.llm_body(specs(), BASE, "claude-haiku-5-5")["general_tools"]
    assert {t["type"] for t in tools} == {"custom"}


def test_push_uses_only_allowed_endpoints_and_versioned_lists():
    fake = account()
    run(fake)
    run(fake)
    for method, path, _ in fake.requests:
        assert allowed(method, path), (method, path)
        if "list-" in path:
            assert path.startswith("/v2/") or path == "/list-voices", path


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/list-agents"),
        ("GET", "/list-retell-llms"),
        ("GET", "/list-phone-numbers"),
        ("POST", "/list-calls"),
        ("POST", "/v3/list-calls/../../delete-agent/x"),
        ("DELETE", "/delete-agent/agent_1"),
    ],
)
def test_removed_and_unused_endpoints_are_refused_before_sending(method, path):
    assert not allowed(method, path)
    with pytest.raises(RetellError):
        Retell("key", client=None).request(method, path)


def test_a_model_retell_does_not_offer_stops_the_push():
    with pytest.raises(ValueError):
        build.llm_body(specs(), BASE, "gpt-5.4-mini")


def test_contains_ignores_fields_retell_adds_but_not_ones_we_set():
    assert build.contains({"a": 1, "b": {"c": 2, "extra": 3}}, {"b": {"c": 2}})
    assert not build.contains({"b": {"c": 2}}, {"b": {"c": 3}})
    assert not build.contains({"l": [1, 2]}, {"l": [1]})
    assert build.changes({"a": 1, "b": 2}, {"a": 1, "b": 3}) == {"b": 3}


def recorded(name: str):
    return json.loads((Path(__file__).parent / "recorded" / f"{name}.json").read_text())


def test_against_retells_recorded_responses_a_push_sends_no_writes():
    """The account as Retell returned it after the first real push, with every field Retell
    fills in on its side (tests/voice_retell/recorded)."""
    llm, agent = recorded("get-retell-llm"), recorded("get-agent")
    fake = FakeRetell(
        {
            "llms": {llm["llm_id"]: llm},
            "agents": {agent["agent_id"]: agent},
            "numbers": recorded("list-phone-numbers")["items"],
            "voices": recorded("list-voices"),
        }
    )
    tools = [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in llm["general_tools"]
    ]
    base = agent["webhook_url"].removesuffix("/retell/webhook")
    max_voice = {"voice_id": "3945925b-9143-4b7d-9ab5-cc4878001106", "model": "sonic-3", "name": "Max"}
    assert push(fake.client(), tools, base, "claude-haiku-5-5", max_voice) == []
    assert fake.writes() == []


def test_parameters_are_compared_in_the_form_retell_stores_them():
    """Retell drops default, minimum, maximum and format (seen on the first push)."""
    from app.schemas import HistoryIn

    stored = next(
        t
        for t in recorded("get-retell-llm")["general_tools"]
        if t["name"] == "get_customer_history"
    )["parameters"]
    ours = HistoryIn.model_json_schema()
    ours["properties"].pop("call_id")
    ours["required"].remove("call_id")
    for prop in ours["properties"].values():
        prop.pop("title", None)
    ours.pop("title")
    assert build.retell_schema(ours) == stored


def test_the_agent_hears_one_word_replies_and_waits_through_a_pause_mid_sentence():
    """Live call 3: fast transcription dropped a quiet "sure" and answered into a pause
    mid-sentence, and the caller waited 10 s for a check-in
    (voice/retell/turn_probe.py, evals/rounds/retell-02.md)."""
    body = build.agent_body("llm_1", BASE, "custom_voice_1", "sonic-3")
    assert body["stt_mode"] == "accurate"
    assert body["responsiveness"] == 1.0 and body["enable_dynamic_responsiveness"] is False
    assert body["reminder_trigger_ms"] == 6000



def test_retell_speaks_with_the_workers_voice_from_config():
    """Both stacks on Max, so the comparison is fair; the name comes from config, never a default."""
    from voice.retell.push import live_profile

    _, voice = live_profile(MODELS_CONFIG)
    assert voice == VOICE and voice["name"] == "Max"
