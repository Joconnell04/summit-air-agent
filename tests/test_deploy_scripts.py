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


def test_the_live_schedule_is_opened_by_hand_at_deploy_not_on_every_start():
    """PR #155 review: run on every mock start, it could cancel a visit a live caller had moved."""
    for stack in ("summit-air", "retell"):
        assert "open_live_schedule" not in (ROOT / "deploy" / stack / "run.sh").read_text()


def test_the_watchdog_leaves_the_numbers_routing_alone_unless_fallback_is_on():
    """The number rings Retell now (DECISIONS.md 112); the fallback's way back is the LiveKit trunk."""
    script = (Path(__file__).resolve().parents[1] / "deploy" / "summit-air" / "watchdog.sh").read_text()
    assert script.count("scripts.phone.fallback") == 2
    assert script.count('[ "${PHONE_FALLBACK:-off}" = on ]') == 2


def test_the_server_led_instance_never_reaches_the_live_services():
    """Issue #171: the arm's test instance runs beside the live services and must never share their
    ports, folders or Caddy site, or restart Caddy."""
    folder = ROOT / "deploy" / "server-led"
    run, deploy = (folder / "run.sh").read_text(), (folder / "deploy.sh").read_text()
    for live in (":8100", ":8101", ":8102", ":8110", ":8111", ":8112", ":8113"):
        assert live not in run
    assert "SERVER_LED=1" in run and "--if-stale" in run
    # The clock can move in this world only, and the socket arm speaks the server's sentences.
    assert run.count("SUMMIT_WORLD=server-led") == 2
    assert "RETELL_LLM_SERVER_LED=1" in run and "--port 8133" in run
    assert "summit-air/app" not in deploy and "summit-air/retell/" not in deploy
    assert "restart caddy" not in deploy and "reload caddy" in deploy
    site = (folder / "Caddyfile").read_text()
    assert site.count("server-led.45-77-105-247.sslip.io {") == 1


def test_the_live_socket_serves_the_live_world_on_its_own_port():
    """docs/ai-process/briefs/socket-cutover.md: the socket beside the live bridge, on 8114, against
    the live tool server, behind its own Caddy route."""
    run = (ROOT / "deploy" / "retell" / "run.sh").read_text()
    llm = run[run.index("  llm)"):run.index(";;", run.index("  llm)"))]
    assert "TOOL_SERVER_URL=http://127.0.0.1:8100" in llm and "--port 8114" in llm
    assert "RETELL_LLM_SERVER_LED=1" in llm
    assert "handle /retell-llm/*" in (ROOT / "deploy" / "retell" / "Caddyfile").read_text()
    assert " llm; do" in (ROOT / "deploy" / "retell" / "deploy.sh").read_text()


def test_make_live_stops_before_deploying_when_a_call_is_on_the_line_and_pushes_the_live_agent():
    """make live is the one deploy command: the line check comes before any deploy, and push.py
    runs with --live, because without it the demo number is pointed at the test agent."""
    script = (ROOT / "deploy" / "summit-air" / "live.sh").read_text()
    check = script.index("line.py busy")
    assert check < script.index("deploy/summit-air/deploy.sh")
    assert check < script.index("deploy/retell/deploy.sh")
    assert "push.py --live" in script
    assert "git status --porcelain" in script and "= main ]" in script
    assert "sh deploy/summit-air/live.sh" in (ROOT / "Makefile").read_text()
