import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ratchet", ROOT / "scripts" / "ratchet.py")
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)


def test_ratchet_passes_at_baseline():
    ok, _ = ratchet.check(4, {"min_tests": 4})
    assert ok


def test_ratchet_fails_below_baseline():
    ok, msg = ratchet.check(3, {"min_tests": 4})
    assert not ok
    assert "below" in msg


def test_model_profiles_name_a_default():
    profiles = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())
    assert profiles["default"] in profiles["profiles"]


def test_every_profile_has_provider_and_model():
    profiles = yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["profiles"]
    for name, p in profiles.items():
        assert p.get("provider") and p.get("model"), name
