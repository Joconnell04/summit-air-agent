"""The live demo's schedule (issue #149, DECISIONS.md 095): most windows open, weekends staffed, the
named customers' visits kept, and the seeded world untouched by the seven-day hours."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml

from scripts.open_live_schedule import open_schedule
from servicetitan_mock import capacity
from servicetitan_mock.config import TenantConfig
from servicetitan_mock.seed import build_world

TENANT = Path(__file__).resolve().parent.parent / "tenants" / "summit-air"
START = date(2026, 11, 2)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> tuple[Path, dict]:
    db = tmp_path_factory.mktemp("world") / "mock.db"
    return db, build_world(db, TENANT, START)


def free(db: Path, day: date) -> dict[str, tuple[int, int]]:
    cfg = TenantConfig.load(TENANT)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {
            w.id: (len(s.free_fit_ids), s.total)
            for w in cfg.windows
            if not w.after_hours and cfg.window_offered(day, w)
            for s in [capacity.slots(conn, cfg, day, w, 11, [])]
        }
    finally:
        conn.close()


def on_shift(db: Path, day: date) -> int:
    """Technicians with a daytime shift on the day: the rows its schedule shows."""
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT COUNT(DISTINCT technician_id) FROM technician_shifts "
            "WHERE shift_type = 'Normal' AND substr(start, 1, 10) = ?",
            (day.isoformat(),),
        ).fetchone()[0]
    finally:
        conn.close()


def test_most_windows_open_weekends_included_and_named_visits_stay(seeded, tmp_path):
    db = tmp_path / "live.db"
    db.write_bytes(seeded[0].read_bytes())
    scenarios = seeded[1]
    first = open_schedule(db, TENANT, date(2026, 11, 4))
    assert first["fillers_cancelled"] > 0 and first["shifts_added"] > 0
    # Friday the 6th was nearly full in the seed; Sunday the 8th had nobody on.
    for day in (date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8)):
        windows = free(db, day)
        assert set(windows) == {"0800-1000", "1000-1200", "1200-1400", "1400-1600", "1600-1800",
                                "1800-2000"}, day
        for opened, total in windows.values():
            assert total and opened / total > 0.6, (day, windows)
    conn = sqlite3.connect(db)
    try:
        maria = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (scenarios["repeatCallerOpenJob"]["jobId"],)
        ).fetchone()
        kept = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE summary = 'Booked by phone.' AND status = 'Scheduled'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert maria == ("Scheduled",)
    assert kept > 0, "a few filler visits stay for Jackson's own runs"
    assert on_shift(db, date(2026, 11, 6)) <= 20 and on_shift(db, date(2026, 11, 8)) <= 20
    assert open_schedule(db, TENANT, date(2026, 11, 4)) == {
        "shifts_added": 0, "fillers_cancelled": 0
    }


def test_the_seven_day_hours_leave_the_seeded_world_as_it_was(seeded, tmp_path):
    """The eval world's appointments are what the scenarios need: opening the weekend in the
    company hours must not move one of them."""
    old = tmp_path / "tenant"
    old.mkdir()
    for path in TENANT.iterdir():
        if path.is_file():
            (old / path.name).write_bytes(path.read_bytes())
    (old / "knowledge").mkdir()
    for path in (TENANT / "knowledge").iterdir():
        (old / "knowledge" / path.name).write_bytes(path.read_bytes())
    config = yaml.safe_load((TENANT / "config.yaml").read_text())
    config["company"]["hours"]["sat"] = ["08:00", "16:00"]
    config["company"]["hours"]["sun"] = None
    (old / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    before = tmp_path / "before.db"
    build_world(before, old, START)

    def rows(db: Path) -> list:
        conn = sqlite3.connect(db)
        try:
            return conn.execute(
                "SELECT id, job_id, technician_id, window_date, window_id, status "
                "FROM appointments ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    assert rows(before) == rows(seeded[0])


def test_a_filler_visit_a_live_caller_changed_is_left_alone(seeded, tmp_path):
    db = tmp_path / "live.db"
    db.write_bytes(seeded[0].read_bytes())
    conn = sqlite3.connect(db)
    try:
        touched = conn.execute(
            "SELECT j.id FROM jobs j JOIN appointments a ON a.job_id = j.id WHERE j.summary = "
            "'Booked by phone.' AND a.window_date >= '2026-11-04' AND j.id % 6 <> 0 LIMIT 1"
        ).fetchone()[0]
        with conn:
            conn.execute(
                "UPDATE appointments SET modified_on = '2026-11-04T15:00:00Z' WHERE job_id = ?",
                (touched,),
            )
    finally:
        conn.close()
    open_schedule(db, TENANT, date(2026, 11, 4))
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT status FROM jobs WHERE id = ?", (touched,)).fetchone() == (
            "Scheduled",
        )
    finally:
        conn.close()
