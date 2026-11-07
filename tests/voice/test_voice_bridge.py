"""Bridge-line rules (docs/latency.md, spec S-37a), with a fake slow tool and a fake player."""

import asyncio
import time

import yaml

from voice.livekit import clips
from voice.livekit.bridge import MAX_CLIP_SECONDS, Bridge
from voice.livekit.settings import ROOT

CLIP_S = 0.2


class Player:
    def __init__(self):
        self.started: list[tuple[str, float]] = []
        self.ended_at: float | None = None

    async def play(self, clip: str) -> None:
        self.started.append((clip, time.perf_counter()))
        await asyncio.sleep(CLIP_S)
        self.ended_at = time.perf_counter()


async def tool(seconds: float, answer: str = "done") -> str:
    await asyncio.sleep(seconds)
    return answer


def make(player: Player, silent: bool = True, **kwargs) -> Bridge:
    kwargs.setdefault("after_s", 0.05)
    kwargs.setdefault("min_turns_between", 3)
    return Bridge(["a", "b", "c"], player.play, lambda: silent, **kwargs)


def test_a_fast_tool_plays_nothing():
    player = Player()

    async def run():
        bridge = make(player)
        assert await bridge.run(tool(0.01)) == "done"
        await bridge.wait_clear()

    asyncio.run(run())
    assert player.started == []


def test_a_slow_tool_plays_one_clip_after_the_delay():
    player = Player()

    async def run():
        bridge = make(player)
        began = time.perf_counter()
        assert await bridge.run(tool(0.3)) == "done"
        await bridge.wait_clear()
        return began

    began = asyncio.run(run())
    assert [clip for clip, _ in player.started] == ["a"]
    assert player.started[0][1] - began >= 0.05


def test_no_clip_while_the_agent_or_caller_is_speaking():
    player = Player()

    async def run():
        bridge = make(player, silent=False)
        await bridge.run(tool(0.2))

    asyncio.run(run())
    assert player.started == []


def test_the_reply_waits_for_a_clip_the_tool_answered_during():
    player = Player()

    async def run():
        bridge = make(player)
        # The tool answers 0.05 s into a 0.2 s clip.
        result = await bridge.run(tool(0.1))
        answered = time.perf_counter()
        assert bridge.playing()
        await bridge.wait_clear()
        return result, answered, time.perf_counter()

    result, answered, cleared = asyncio.run(run())
    assert result == "done"
    # The answer came back at once (the model's next step can start), but the reply's audio
    # waits until the clip has ended.
    assert player.ended_at is not None and player.ended_at > answered
    assert cleared >= player.ended_at


def test_at_most_once_every_few_turns_and_never_the_same_clip_twice_in_a_row():
    player = Player()

    async def run():
        bridge = make(player, min_turns_between=3)
        for _turn in range(13):
            bridge.caller_turn()
            await bridge.run(tool(0.08))
            await bridge.wait_clear()

    asyncio.run(run())
    played = [clip for clip, _ in player.started]
    # Turns 1, 4, 7, 10 and 13; the clips in turn, so a long call never runs out of them.
    assert played == ["a", "b", "c", "a", "b"]


def test_one_clip_is_never_played_twice_in_a_row():
    player = Player()

    async def run():
        bridge = Bridge(["a"], player.play, lambda: True, after_s=0.05, min_turns_between=1)
        for _turn in range(3):
            bridge.caller_turn()
            await bridge.run(tool(0.08))
            await bridge.wait_clear()

    asyncio.run(run())
    assert [clip for clip, _ in player.started] == ["a"]


def test_two_slow_tools_in_one_turn_play_one_clip():
    player = Player()

    async def run():
        bridge = make(player)
        bridge.caller_turn()
        await asyncio.gather(bridge.run(tool(0.3)), bridge.run(tool(0.3)))
        await bridge.wait_clear()

    asyncio.run(run())
    assert len(player.started) == 1


def test_a_clip_that_fails_does_not_hold_the_reply():
    async def broken(_clip):
        raise RuntimeError("no audio track")

    async def run():
        bridge = Bridge(["a"], broken, lambda: True, after_s=0.01)
        assert await bridge.run(tool(0.05)) == "done"
        await bridge.wait_clear()

    asyncio.run(run())


def test_every_bridge_clip_is_recorded_and_under_about_one_second():
    tenant = ROOT / "tenants" / "summit-air"
    names = yaml.safe_load((tenant / "agent.yaml").read_text())["voice"]["clips"]
    bridge = [n for n in names if n.startswith("bridge_")]
    assert len(bridge) >= 3
    for name in bridge:
        assert clips.seconds(clips.folder(tenant) / f"{name}.wav") <= MAX_CLIP_SECONDS, name
    assert len(clips.bridge_clips(tenant)) == len(bridge)


def test_the_busy_line_message_is_recorded_and_starts_with_the_safety_line():
    tenant = ROOT / "tenants" / "summit-air"
    text = yaml.safe_load((tenant / "agent.yaml").read_text())["voice"]["clips"]["at_capacity"]
    assert (clips.folder(tenant) / "at_capacity.wav").exists()
    assert text.index("9-1-1") < text.index("busy")
