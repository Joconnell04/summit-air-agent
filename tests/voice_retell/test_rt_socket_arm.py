"""The socket arm's run tools (retell/arms/socket, issue #176): its own line, never Jackson's,
and persona variants that are varied but can be made again."""

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


def test_the_arm_runs_on_its_own_line_and_never_repoints_a_number():
    line = load("line")
    assert line.NUMBER == "+15162129051"
    slate = (ROOT / "retell" / "arms" / "socket" / "run_slate.sh").read_text()
    # Jackson's (646) is never dialed or repointed.
    assert "6466240418" not in slate and "update-phone-number" not in slate
    assert "line.py busy" in slate and "line.py hangup" in slate


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
    assert line.ours({"agent_id": agent, "from_number": line.TEST_CALLER}, agent)
    # A person calling the line, or any call on another agent, is never ended.
    assert not line.ours({"agent_id": agent, "from_number": "+16784774024"}, agent)
    assert not line.ours({"agent_id": "agent_a3e38e6aa3d4e75dfac12aafc8",
                          "from_number": line.TEST_CALLER}, agent)


def test_perceived_wait_and_hold_phrase_talk_over_from_word_timings():
    waits = load("waits")

    def entry(role, text, start, end):
        words = text.split()
        step = (end - start) / len(words)
        return {"role": role, "content": text, "words": [
            {"word": w, "start": start + i * step, "end": start + (i + 1) * step}
            for i, w in enumerate(words)]}

    call = {"transcript_object": [
        entry("agent", "Hi there.", 0.0, 1.0),
        entry("user", "My heat is out.", 2.0, 3.0),
        entry("agent", "Sorry to hear that.", 4.2, 5.0),
        entry("user", "It's 45 Forest Avenue.", 6.0, 7.0),
        entry("agent", "One sec.", 7.8, 8.2),
        entry("user", "And it's cold.", 8.3, 9.0),
        entry("agent", "One sec. So that's forty-five Forest Avenue.", 9.9, 12.0),
    ]}
    found = waits.turns(call, ["one sec."])
    assert [t["wait_s"] for t in found] == [1.2, 0.8, 0.9]
    # The caller spoke over the hold phrase; the last one led straight into the line.
    assert [t["hold"] for t in found] == [False, True, True]
    assert [t["talk_over"] for t in found] == [False, True, False]
    assert "line_wait_s" in found[2]
