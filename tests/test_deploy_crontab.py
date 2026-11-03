"""deploy/summit-air/install-crontab.sh: our lines are replaced, everything else stays, and an
empty crontab still ends up with the watchdog (it used to install an empty one)."""

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "summit-air" / "install-crontab.sh"


def install(tmp_path: Path, existing: str | None) -> str:
    store = tmp_path / "crontab.txt"
    if existing is not None:
        store.write_text(existing)
    fake = tmp_path / "bin" / "crontab"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text(
        f'#!/bin/sh\nif [ "$1" = "-l" ]; then cat "{store}" 2>/dev/null || exit 1; '
        f'else cat > "{store}.new" && mv "{store}.new" "{store}"; fi\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}"}
    subprocess.run(["sh", str(SCRIPT)], check=True, env=env)
    return store.read_text()


def test_an_empty_crontab_gets_the_watchdog(tmp_path):
    assert "watchdog.sh" in install(tmp_path, None)


def test_a_crontab_holding_only_our_lines_is_replaced_not_emptied(tmp_path):
    ours = (ROOT / "deploy" / "summit-air" / "crontab").read_text()
    result = install(tmp_path, ours)
    assert result.count("watchdog.sh") == 1 and result.count("@reboot") == 1


def test_other_lines_stay(tmp_path):
    result = install(tmp_path, "0 3 * * * echo other\n")
    assert "echo other" in result and "watchdog.sh" in result
