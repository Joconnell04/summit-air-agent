"""The guidance check on an owner's draft (app/guidance_check.py), with a fake model: a refusal
gives its reason, a pass passes, and a check that can't run refuses the save."""

import json
from types import SimpleNamespace

from toolkit import ROOT

from app import guidance_check
from app.tenant import Tenant

TENANT = Tenant.load(ROOT / "tenants" / "summit-air")
ITEMS = {
    "guidance.booking.0": "Ask if there's a pet at home.",
    "guidance.booking.1": "Skip the address read-back for regulars.",
}
WHY = "The agent reads the address back on every booking to check it's a home."


class FakeModel:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer, self.error, self.asked = answer, error, []

    def complete(self, system, messages, tools=None, max_tokens=1024, json_schema=None):
        self.asked.append({"system": system, "messages": messages, "schema": json_schema})
        if self.error:
            raise self.error
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return SimpleNamespace(text=text)


def results(*rows):
    return {"results": [{"id": i, "refuse": r, "reason": why} for i, r, why in rows]}


def test_a_refused_item_gets_the_models_reason_on_its_field():
    model = FakeModel(results(("1", False, ""), ("2", True, WHY)))
    assert guidance_check.check_with(model, TENANT, ITEMS) == {"guidance.booking.1": WHY}
    asked = model.asked[0]
    assert asked["schema"] == guidance_check.SCHEMA
    assert "1. (Booking note) Ask if there's a pet at home." in asked["messages"][0]["content"]


def test_items_that_pass_come_back_empty():
    model = FakeModel(results(("1", False, ""), ("2", False, "")))
    assert guidance_check.check_with(model, TENANT, ITEMS) == {}


def test_a_refusal_with_no_reason_still_says_something():
    model = FakeModel(results(("1", True, "  "), ("2", False, "")))
    assert guidance_check.check_with(model, TENANT, ITEMS)["guidance.booking.0"]


def test_a_check_that_cannot_run_refuses_the_save():
    unavailable = {"content": guidance_check.UNAVAILABLE}
    for model in (
        FakeModel(error=TimeoutError("read timed out")),
        FakeModel("not json"),
        FakeModel(results(("1", False, ""))),  # an item left out is an item not checked
        FakeModel(results(("1", False, ""), ("2", False, ""), ("3", True, "x"))),
    ):
        assert guidance_check.check_with(model, TENANT, ITEMS) == unavailable


def test_no_key_refuses_the_save(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert guidance_check.check(TENANT, ITEMS) == {"content": guidance_check.UNAVAILABLE}


def test_the_locked_rules_come_from_the_rulebook():
    text = "\n".join(guidance_check.locked_rules(TENANT))
    assert "call 911" in text, "the safety lines"
    assert "isn't a business" in text, "the read-back is the home check"
    assert "Won't book a commercial customer" in text
    assert "never quotes a fee" in text
    assert "only ever raises it" in text
    assert "Staten Island North" in text and "Manhattan" not in text


def test_the_check_runs_on_a_named_profile_with_thinking_off():
    cfg = guidance_check.models.profile(guidance_check.PROFILE)
    assert cfg["provider"] == "anthropic" and cfg["thinking"] is False


def test_the_check_prompt_says_a_mention_passes():
    text = guidance_check.PROMPT.read_text()
    assert "911" in text and "only mentions" in text
