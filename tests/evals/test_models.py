import pytest

from evals.models import AnthropicModel, ModelError, cost, for_profile, load_config, profile


def test_profiles_resolve_live_and_dotted_offline_names():
    assert profile("fast")["model"] == load_config()["profiles"]["fast"]["model"]
    assert profile("offline.grader")["thinking"] is True
    with pytest.raises(ModelError, match="no model profile"):
        profile("offline.nope")


def test_every_profile_has_a_price():
    config = load_config()
    for section in ("profiles", "offline", "post_call"):
        for name, p in config[section].items():
            assert p["price"]["input"] >= 0 and p["price"]["output"] >= 0, name


def test_cost_counts_cache_writes_and_reads():
    price = {"input": 1.0, "output": 5.0}
    assert cost(price, 1_000_000, 0) == 1.0
    assert cost(price, 0, 1_000_000) == 5.0
    assert cost(price, 0, 0, cache_write=1_000_000, cache_read=1_000_000) == pytest.approx(1.35)


def test_thinking_follows_the_profile():
    assert AnthropicModel({"thinking": True}, client=object()).thinking() == {"type": "adaptive"}
    assert AnthropicModel({"thinking": False}, client=object()).thinking() == {"type": "disabled"}
    off = {"thinking": False, "thinking_off": "between_tools"}
    assert AnthropicModel(off, client=object()).thinking() == {"type": "between_tools"}


def test_a_provider_the_harness_cannot_call_is_refused():
    with pytest.raises(ModelError, match="can't call yet"):
        for_profile("openai-fast")
