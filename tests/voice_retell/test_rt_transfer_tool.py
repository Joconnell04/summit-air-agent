"""The script that adds the warm transfer to a Retell agent (voice/retell/transfer_tool.py): it
keeps every other tool, dials only the variable the bridge sets, asks for the transfer webhooks,
and a second run sends nothing."""

from fake_retell import FakeRetell

from voice.retell import transfer_tool

BASE = "https://bridge.example"
OTHER = {"type": "custom", "name": "flag_person", "url": f"{BASE}/retell/tools/flag_person"}


def world(**llm) -> FakeRetell:
    return FakeRetell({
        "llms": {"llm_1": {"llm_id": "llm_1", "general_tools": [OTHER], **llm}},
        "agents": {"agent_1": {
            "agent_id": "agent_1",
            "agent_name": "Copy",
            "response_engine": {"type": "retell-llm", "llm_id": "llm_1"},
            "webhook_events": ["call_started", "call_ended", "transcript_updated"],
        }},
    })


def send(fake: FakeRetell) -> None:
    llm, agent, _ = transfer_tool.plan(fake.client(), "agent_1", BASE)
    llm_id = llm.pop("llm_id")
    if llm:
        fake.client().request("PATCH", f"/update-retell-llm/{llm_id}", json=llm)
    if agent:
        fake.client().request("PATCH", "/update-agent/agent_1", json=agent)


def test_it_adds_both_tools_and_the_transfer_webhooks_once():
    fake = world()
    send(fake)
    tools = fake.llms["llm_1"]["general_tools"]
    assert [t["name"] for t in tools] == ["flag_person", "request_transfer", "transfer_call"]
    assert tools[1]["url"] == f"{BASE}/retell/tools/request_transfer"
    dial = tools[2]
    assert dial["transfer_destination"] == {"type": "predefined", "number": "{{transfer_number}}"}
    assert dial["transfer_option"]["type"] == "warm_transfer"
    assert fake.agents["agent_1"]["webhook_events"] == [
        "call_started", "call_ended", "transcript_updated", "transfer_bridged",
        "transfer_cancelled",
    ]
    writes = len(fake.writes())
    send(fake)
    assert len(fake.writes()) == writes


def test_a_default_transfer_number_is_removed():
    fake = world(default_dynamic_variables={"transfer_number": "+12125550100", "greeting": "Hi"})
    send(fake)
    assert fake.llms["llm_1"]["default_dynamic_variables"] == {"greeting": "Hi"}


def test_an_agent_with_no_event_list_keeps_retells_defaults():
    assert transfer_tool.with_transfer_events(None) == [
        "call_started", "call_ended", "call_analyzed", "transfer_bridged", "transfer_cancelled",
    ]
