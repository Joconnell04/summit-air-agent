"""The socket arm's run tools (retell/arms/socket, issue #176): the line always goes back to
Jackson's agent, and the persona variants are varied but can be made again."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str):
    path = ROOT / "retell" / "arms" / "socket" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"socket_arm_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_line_goes_back_to_jacksons_agent_and_the_live_bridge():
    line = load("line")
    assert line.NUMBER == "+16466240418"
    assert line.JACKSON["inbound_agents"][0]["agent_id"] == "agent_a3e38e6aa3d4e75dfac12aafc8"
    assert line.JACKSON["inbound_webhook_url"] == "https://45-77-105-247.sslip.io/retell/inbound"
    # A run points the line at the server-led world only, never the live bridge.
    assert line.setup("agent_x")["inbound_webhook_url"].startswith("https://server-led.")


def test_the_slate_restores_the_line_on_any_exit():
    slate = (ROOT / "retell" / "arms" / "socket" / "run_slate.sh").read_text()
    assert "trap restore EXIT" in slate
    assert "line.py busy" in slate and "line.py count" in slate


def test_persona_variants_cover_every_journey_and_repeat_with_the_seed(tmp_path):
    personas = load("personas")
    sys.argv = ["personas.py", str(tmp_path / "a"), "--per-journey", "3"]
    personas.main()
    sys.argv = ["personas.py", str(tmp_path / "b"), "--per-journey", "3"]
    personas.main()
    first = sorted(p.name for p in (tmp_path / "a").glob("*.txt") if p.name != "slate.txt")
    assert len(first) == 12
    assert {n.rsplit("-", 1)[0] for n in first} == set(personas.JOURNEYS)
    for name in first:
        assert (tmp_path / "a" / name).read_text() == (tmp_path / "b" / name).read_text()
    texts = [(tmp_path / "a" / f"routine-0{n}.txt").read_text() for n in (1, 2, 3)]
    assert len(set(texts)) == 3
    slate = (tmp_path / "a" / "slate.txt").read_text().splitlines()
    assert len(slate) == 12 and all(line.endswith(" reset") for line in slate)


def test_only_our_own_scripted_calls_are_hung_up():
    line = load("line")
    agent = "agent_socket"
    assert line.ours({"agent_id": agent, "from_number": line.NUMBER}, agent)
    # Jackson calling the armed line, or any call on his agent, is never ended.
    assert not line.ours({"agent_id": agent, "from_number": "+16784774024"}, agent)
    assert not line.ours({"agent_id": "agent_a3e38e6aa3d4e75dfac12aafc8",
                          "from_number": line.NUMBER}, agent)
    assert "line.py hangup" in (ROOT / "retell" / "arms" / "socket" / "run_slate.sh").read_text()
