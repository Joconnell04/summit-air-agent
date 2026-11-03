"""The trade layer: trades/<trade>/agent.yaml under each tenant's agent.yaml (docs/template.md)."""

import re
import shutil

import pytest
import yaml
from toolkit import ROOT, TENANT_DIR

from app import priority as prio
from app.tenant import Tenant, load_agent, merge

TRADES_DIR = ROOT / "trades"
# Summit Air's agent config as the server saw it before the trade layer, and as it must stay unless
# a change means to move it. After checking a diff is intended, regenerate with:
#   uv run python -m app.tenant tenants/summit-air > tests/tool_server/golden/summit-air-agent.yaml
GOLDEN = ROOT / "tests" / "tool_server" / "golden" / "summit-air-agent.yaml"


def test_summit_air_merged_over_the_trade_is_the_config_the_server_had():
    merged, trade = load_agent(TENANT_DIR)
    assert trade == "hvac"
    assert merged == yaml.safe_load(GOLDEN.read_text())


def test_summit_air_keeps_its_own_wording_and_local_numbers():
    tenant = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())
    trade = yaml.safe_load((TRADES_DIR / "hvac" / "agent.yaml").read_text())
    # The signed-off script and the NYC-sourced cutoffs are the company's, not the trade's.
    assert "lines" in tenant["safety"]["kinds"]["gas"]
    assert "lines" not in trade["safety"]["kinds"]["gas"]
    assert "brackets" in tenant["priority"] and "brackets" not in trade["priority"]
    assert "question" not in trade["safety"]["vague"]["gas"]


def test_the_trade_folder_names_no_company_or_place():
    config = yaml.safe_load((TENANT_DIR / "config.yaml").read_text())
    agent = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())
    facts = {config["company"]["name"], "Brooklyn", "Queens", "Staten Island", "NYC", "New York"}
    facts |= {z for zone in config["zones"] for z in zone["zips"]}
    numbers = {re.sub(r"\D", "", n)[-7:] for n in agent["phone_numbers"]}
    text = "\n".join(p.read_text() for p in TRADES_DIR.rglob("*.yaml"))
    for fact in facts:
        assert not re.search(rf"\b{re.escape(fact)}\b", text, flags=re.IGNORECASE), fact
    for number in numbers:
        assert number not in re.sub(r"\D", "", text), number


def test_a_tenant_inherits_the_trade_with_one_override(tmp_path):
    folder = tmp_path / "north-heat"
    shutil.copytree(TENANT_DIR, folder)
    agent = yaml.safe_load((folder / "agent.yaml").read_text())
    agent["priority"] = {"rules": [{"id": "pipes_or_only_heat", "raise_to": "Urgent"}]}
    (folder / "agent.yaml").write_text(yaml.safe_dump(agent))

    tenant = Tenant.load(folder, TRADES_DIR)
    trade = yaml.safe_load((TRADES_DIR / "hvac" / "agent.yaml").read_text())
    assert tenant.trade == "hvac"
    # Inherited unchanged: the gas phrases and every other rule.
    assert tenant.safety["kinds"]["gas"]["clear"] == trade["safety"]["kinds"]["gas"]["clear"]
    assert [r["id"] for r in tenant.priority["rules"]] == [r["id"] for r in trade["priority"]["rules"]]
    # The one override, merged into the trade's rule of that id.
    pipes = next(r for r in tenant.priority["rules"] if r["id"] == "pipes_or_only_heat")
    assert pipes["raise_to"] == "Urgent"
    assert "frozen pipes" in pipes["phrases"]
    decided = prio.decide(prio.Facts("tune_up", "our pipes froze", None, False), tenant.priority)
    assert decided.level == "Normal"
    decided = prio.decide(prio.Facts("no_heat", "our pipes froze", None, False), tenant.priority)
    assert decided.level == "Urgent"


def test_mappings_merge_key_by_key_and_the_tenant_wins():
    trade = {"a": 1, "b": {"x": 1, "y": 2}}
    assert merge(trade, {"b": {"y": 3}, "c": 4}) == {"a": 1, "b": {"x": 1, "y": 3}, "c": 4}


def test_lists_of_ids_merge_by_id_and_new_ids_follow_the_trades():
    trade = [{"id": "one", "phrases": ["a"]}, {"id": "two", "phrases": ["b"]}]
    tenant = [{"id": "three", "phrases": ["c"]}, {"id": "one", "raise_to": "High"}]
    assert merge(trade, tenant) == [
        {"id": "one", "phrases": ["a"], "raise_to": "High"},
        {"id": "two", "phrases": ["b"]},
        {"id": "three", "phrases": ["c"]},
    ]


def test_a_tenant_can_add_a_phrase_but_not_drop_one():
    assert merge(["smell gas", "gas leak"], ["propane smell", "gas leak"]) == [
        "smell gas",
        "gas leak",
        "propane smell",
    ]
    assert merge(["smell gas", "gas leak"], []) == ["smell gas", "gas leak"]


def test_a_tenant_without_extends_is_read_as_it_is(tmp_path):
    (tmp_path / "agent.yaml").write_text("lines: {hello: Hi}\n")
    assert load_agent(tmp_path) == ({"lines": {"hello": "Hi"}}, None)


def test_extending_a_trade_that_does_not_exist_fails_at_load(tmp_path):
    (tmp_path / "agent.yaml").write_text("extends: roofing\n")
    with pytest.raises(ValueError, match="roofing"):
        load_agent(tmp_path, TRADES_DIR)


def tenant_with(tmp_path, change) -> Tenant:
    """Summit Air copied with one change to its own agent.yaml, then loaded."""
    folder = tmp_path / "north-heat"
    shutil.copytree(TENANT_DIR, folder)
    agent = yaml.safe_load((folder / "agent.yaml").read_text())
    change(agent)
    (folder / "agent.yaml").write_text(yaml.safe_dump(agent))
    return Tenant.load(folder, TRADES_DIR)


@pytest.mark.parametrize(
    "drop, named",
    [
        (lambda s: s["kinds"]["gas"].pop("lines"), "kinds.gas.lines"),
        (lambda s: s["kinds"]["co"].pop("variants"), "kinds.co.variants"),
        (lambda s: s["vague"]["gas"].pop("question"), "vague.gas.question"),
        (lambda s: s.pop("vague"), "vague.co.question"),
        (lambda s: s.pop("sign_off"), "sign_off"),
        (lambda s: s.pop("flag_reason"), "flag_reason"),
    ],
)
def test_a_tenant_missing_safety_wording_fails_at_load(tmp_path, drop, named):
    with pytest.raises(ValueError, match=re.escape(named)):
        tenant_with(tmp_path, lambda agent: drop(agent["safety"]))


@pytest.mark.parametrize(
    "change, named",
    [
        (
            lambda a: a["priority"]["rules"].append({"id": "medical_equipment", "raise_to": "Normal"}),
            "medical_equipment].raise_to",
        ),
        (
            lambda a: a["priority"]["rules"].append(
                {"id": "bedbound", "raise_to_when_mild": "Normal"}
            ),
            "bedbound].raise_to_when_mild",
        ),
        (
            lambda a: a["priority"]["rules"].append({"id": "safety_signal", "safety_signal": False}),
            "safety_signal].safety_signal",
        ),
        (
            lambda a: a["priority"]["rules"].append({"id": "young_child", "group": False}),
            "young_child].group",
        ),
        (lambda a: a["priority"].update(base={"safety_check": "Normal"}), "base.safety_check"),
        (lambda a: a["safety"].update(denials=["not really"]), "safety.denials"),
    ],
)
def test_a_tenant_cannot_loosen_what_the_trade_sets(tmp_path, change, named):
    with pytest.raises(ValueError, match=re.escape(named)):
        tenant_with(tmp_path, change)


def test_a_tenant_can_raise_a_level_and_add_to_a_rule(tmp_path):
    def change(agent):
        agent["priority"]["rules"].append(
            {"id": "young_child", "raise_to": "Urgent", "phrases": ["twins"]}
        )
        agent["priority"]["base"] = {"tune_up": "High"}

    tenant = tenant_with(tmp_path, change)
    young = next(r for r in tenant.priority["rules"] if r["id"] == "young_child")
    assert young["raise_to"] == "Urgent" and young["phrases"][-1] == "twins"
    assert "toddler" in young["phrases"]
    assert tenant.priority["base"]["tune_up"] == "High"


def test_a_rules_list_where_only_some_entries_have_an_id_fails_at_load(tmp_path):
    with pytest.raises(ValueError, match="some entries have an id"):
        tenant_with(tmp_path, lambda a: a["priority"]["rules"].append({"raise_to": "High"}))


def test_a_tenant_list_without_ids_over_the_trades_rules_fails():
    with pytest.raises(ValueError, match="every entry needs an id"):
        merge({"rules": [{"id": "a"}]}, {"rules": ["b"]})


def test_a_tenant_cannot_replace_a_trade_list_with_another_shape(tmp_path):
    def change(agent):
        agent["safety"]["kinds"]["gas"]["clear"] = "gas"

    with pytest.raises(ValueError, match="safety.kinds.gas.clear"):
        tenant_with(tmp_path, change)
