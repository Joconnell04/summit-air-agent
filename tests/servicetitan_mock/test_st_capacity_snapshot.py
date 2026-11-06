"""The capacity call reads its range once (capacity.Snapshot) and answers every block as the
per-block queries did: same technicians, same order, same busy and fit sets."""

import sqlite3
from datetime import datetime, time, timedelta

from conftest import CFG, START

from servicetitan_mock import capacity


def test_the_snapshot_answers_every_block_as_the_per_block_queries_do(seeded_db):
    conn = sqlite3.connect(seeded_db)
    conn.row_factory = sqlite3.Row
    try:
        begin = datetime.combine(START, time(0), CFG.tz)
        end = begin + timedelta(days=16)
        snapshot = capacity.Snapshot(conn, begin, end)
        checked = 0
        for offset in range(15):
            day = START + timedelta(days=offset)
            for window in [*CFG.blocks(day), *(w for w in CFG.windows if w.after_hours)]:
                start, stop = CFG.window_bounds(day, window)
                if not (begin <= start and stop <= end):
                    continue
                for unit in [b["id"] for b in CFG.business_units if b["active"]]:
                    for skills in ([], ["heating"]):
                        old = capacity.slots(conn, CFG, day, window, unit, skills)
                        new = snapshot.slots(CFG, day, window, unit, skills)
                        assert [t["id"] for t in new.staff] == [t["id"] for t in old.staff]
                        assert (new.busy, new.fit) == (old.busy, old.fit), (day, window.id)
                        checked += 1
        assert checked > 500
    finally:
        conn.close()
