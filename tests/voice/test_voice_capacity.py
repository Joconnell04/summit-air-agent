"""The capacity cap (spec S-47): one setting, shared by every call process on the server."""

import os
import time

from voice.livekit.capacity import Lines


def test_calls_up_to_the_cap_get_a_line_and_the_next_does_not(tmp_path):
    lines = Lines(tmp_path, cap=3)
    assert [lines.take(f"call-{n}") for n in range(4)] == [True, True, True, False]
    assert lines.in_use() == 3


def test_a_freed_line_takes_the_next_call(tmp_path):
    lines = Lines(tmp_path, cap=1)
    assert lines.take("call-a")
    assert not lines.take("call-b")
    lines.free("call-a")
    assert lines.take("call-b")


def test_two_workers_share_one_count(tmp_path):
    # Each call runs in its own process; they meet in the same folder.
    assert Lines(tmp_path, cap=2).take("call-a")
    assert Lines(tmp_path, cap=2).take("call-b")
    assert not Lines(tmp_path, cap=2).take("call-c")


def test_a_line_left_by_a_crashed_call_is_cleared(tmp_path):
    lines = Lines(tmp_path, cap=1, stale_s=60)
    assert lines.take("call-a")
    old = time.time() - 120
    os.utime(next(tmp_path.glob("call-*")), (old, old))
    assert lines.take("call-b")


def test_room_names_with_odd_characters_stay_inside_the_folder(tmp_path):
    lines = Lines(tmp_path / "lines", cap=2)
    assert lines.take("../../etc/passwd")
    assert [p.parent for p in (tmp_path / "lines").glob("call-*")] == [tmp_path / "lines"]
