"""`make owner-demo` seeds a day of calls and the Rules page history from saved gate runs. Nothing
else runs the seed script, and a change on main once broke it unseen (PR #65)."""

import sqlite3

from scripts import seed_owner_day


def test_the_demo_seeds_two_blocked_versions_and_a_published_one(tmp_path):
    seed_owner_day.main(["--dir", str(tmp_path / "demo"), "--day", "2026-11-03"])
    conn = sqlite3.connect(tmp_path / "demo" / "tool.db")
    versions = conn.execute("SELECT number, status FROM rule_versions ORDER BY number").fetchall()
    assert versions == [(1, "blocked"), (2, "blocked"), (3, "published")]
