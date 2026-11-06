"""Retell's warm transfer through the bridge (issue #184): the server says whether the call goes
to the office, and only then does the call get the number its transfer_call tool dials. Retell's
transfer webhooks report back how it went."""

import pytest
from retell_kit import SPOKEN, call_obj, event, inbound, tool, utterance

OFFICE = "+12125550100"
GREETING = utterance("agent", SPOKEN["greeting_unknown"], start=0.5)
ASKS = utterance("user", "Can I just talk to a real person, please?", start=5.0)


@pytest.fixture
def numbers(monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", OFFICE)


def status(tool_app, call_id: str) -> str:
    conn = tool_app.state.services.connect()
    try:
        return conn.execute(
            "SELECT status FROM transfers WHERE call_id = ?", (call_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def asked(client, rid: str = "tx1") -> dict:
    inbound(client, rid)
    return tool(
        client, "request_transfer", call_obj(rid, GREETING, ASKS),
        reason="person", summary="Wants a person",
    )


def test_an_allowed_transfer_gives_the_call_its_number_and_not_the_model(client, retell, numbers):
    found = asked(client)
    assert found["ok"], found
    assert found["say"] == "Got it, transferring you now."
    assert retell.variables == [("tx1", {"transfer_number": OFFICE})]
    # The model reads the sentence and the fallback line, never the number.
    assert "transfer" not in found["data"] and OFFICE not in str(found)
    assert found["data"]["fallback"].startswith("I couldn't reach anyone just now")


def test_a_refused_transfer_sets_no_number(client, retell, monkeypatch):
    monkeypatch.delenv("TRANSFER_OFFICE_NUMBER", raising=False)
    found = asked(client)
    assert found["refused"] == "transfer_unavailable"
    assert retell.variables == []


def test_if_retell_cannot_be_told_the_caller_hears_the_callback(
    client, retell, tool_app, numbers
):
    retell.down = True
    found = asked(client)
    assert found["ok"] is False and found["refused"] == "transfer_failed"
    assert found["say"].startswith("I couldn't reach anyone just now")
    assert status(tool_app, "retell_tx1") == "failed"


@pytest.mark.parametrize(
    ("webhook", "outcome"),
    [("transfer_bridged", "connected"), ("transfer_cancelled", "no_answer")],
)
def test_retells_transfer_webhooks_report_the_outcome(
    client, tool_app, numbers, webhook, outcome
):
    assert asked(client)["ok"]
    event(client, "transfer_started", call_obj("tx1", GREETING, ASKS))
    assert status(tool_app, "retell_tx1") == "dialing"
    event(client, webhook, call_obj("tx1", GREETING, ASKS))
    assert status(tool_app, "retell_tx1") == outcome
