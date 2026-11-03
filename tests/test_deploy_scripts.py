"""The deploy scripts must at least parse. The deploy script wraps a whole remote script in single
quotes, so one apostrophe in a comment there ends the quoting and breaks the deploy."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "deploy").glob("*/*.sh"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_deploy_script_parses(script):
    checked = subprocess.run(["sh", "-n", str(script)], check=False, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr


def test_the_summit_air_deploy_restarts_the_mock_and_seeds_only_when_stale():
    deploy = (ROOT / "deploy" / "summit-air" / "deploy.sh").read_text()
    assert "[s]ervicetitan_mock.app" in deploy and "--port 8101" in deploy
    assert "--if-stale" in (ROOT / "deploy" / "summit-air" / "run.sh").read_text()
    assert "--if-stale" in (ROOT / "deploy" / "retell" / "run.sh").read_text()
