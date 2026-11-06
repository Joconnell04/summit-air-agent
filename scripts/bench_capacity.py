"""Times the mock's capacity answer for a 16-day range, the call find_slots waits on: the
per-block queries it used to make against the one read of the range (servicetitan_mock.capacity
Snapshot). Runs on a freshly seeded world, so its numbers are comparable from run to run.

    uv run python -m scripts.bench_capacity [--runs 20]
"""

import argparse
import sqlite3
import statistics
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from servicetitan_mock import capacity
from servicetitan_mock.config import DEFAULT_TENANT_DIR, TenantConfig
from servicetitan_mock.seed import build_world


def blocks(cfg: TenantConfig, start_day: date, begin: datetime, end: datetime):
    for offset in range(16):
        day = start_day + timedelta(days=offset)
        for window in [*cfg.blocks(day), *(w for w in cfg.windows if w.after_hours)]:
            start, stop = cfg.window_bounds(day, window)
            if begin <= start and stop <= end and cfg.window_offered(day, window):
                for unit in [b["id"] for b in cfg.business_units if b["active"]]:
                    yield day, window, unit


def per_block(conn, cfg, start_day, begin, end) -> None:
    for day, window, unit in blocks(cfg, start_day, begin, end):
        capacity.slots(conn, cfg, day, window, unit, [])


def snapshot(conn, cfg, start_day, begin, end) -> None:
    once = capacity.Snapshot(conn, begin, end)
    for day, window, unit in blocks(cfg, start_day, begin, end):
        once.slots(cfg, day, window, unit, [])


def timed(fn, runs: int, *args) -> list[float]:
    out = []
    for _ in range(runs):
        started = time.perf_counter()
        fn(*args)
        out.append((time.perf_counter() - started) * 1000)
    return out


def summary(ms: list[float]) -> str:
    ms = sorted(ms)
    return (f"p50 {statistics.median(ms):6.1f} ms  p90 {ms[int(len(ms) * 0.9) - 1]:6.1f} ms  "
            f"max {ms[-1]:6.1f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    cfg = TenantConfig.load(DEFAULT_TENANT_DIR)
    start_day = date(2026, 11, 2)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "world.db"
        build_world(path, DEFAULT_TENANT_DIR, start_day)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        begin = datetime.combine(start_day, datetime.min.time(), cfg.tz)
        end = begin + timedelta(days=16)
        print("per-block queries:", summary(timed(per_block, args.runs, conn, cfg, start_day,
                                                   begin, end)))
        print("one read (Snapshot):", summary(timed(snapshot, args.runs, conn, cfg, start_day,
                                                     begin, end)))
        conn.close()


if __name__ == "__main__":
    main()
