"""config/models.yaml is the only place model ids live, so it has to be internally consistent."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())


def test_default_profile_exists():
    assert CONFIG["default"] in CONFIG["profiles"]


def test_every_profile_uses_a_known_provider():
    every = {**CONFIG["profiles"], **CONFIG["offline"], **CONFIG["post_call"], **CONFIG["side"]}
    for name, profile in every.items():
        assert profile["provider"] in CONFIG["providers"], name
        assert profile["model"], name


def test_live_profiles_keep_thinking_off():
    for name, profile in {**CONFIG["profiles"], **CONFIG["side"]}.items():
        assert profile["thinking"] is False, name


def test_every_provider_key_is_listed_in_env_example():
    example = (ROOT / ".env.example").read_text()
    for provider in CONFIG["providers"].values():
        assert f"{provider['key_env']}=" in example


def test_the_default_brain_is_anthropic():
    assert CONFIG["profiles"][CONFIG["default"]]["provider"] == "anthropic"


def test_thinking_is_on_for_the_grader_and_the_technician_summary():
    assert CONFIG["offline"]["grader"]["thinking"] is True
    assert CONFIG["post_call"]["summary"]["thinking"] is True
