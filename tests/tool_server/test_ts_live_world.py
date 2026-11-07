"""The live world as the server leaves it after scripts/reset_live_world.py, not the seed alone.

The owner view was tested against the seed, so when the live reset's schedule opener put all 40
technicians on every weekday the tests stayed green (owner view feedback, 7 November). These tests
run the reset exactly as the server does, on a world that already has calls in it, and check what
the owner and the agent see afterwards.
"""

import sqlite3
from datetime import date, timedelta

import pytest
from test_ts_owner import booked_tune_up
from toolkit import TENANT_DIR, Agent, make_server, sign_in

from scripts.reset_live_world import reset
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.config import TenantConfig
from servicetitan_mock.seed import build_world

DAYS = 14


@pytest.fixture
def live(tmp_path, clock):
    """A live world with a call in its history, then reset the way the server resets it."""
    mock_db = tmp_path / "live.db"
    build_world(mock_db, TENANT_DIR, clock().date())
    mock = create_mock(mock_db, TENANT_DIR, tmp_path / "live.log", clock=clock)
    agent = sign_in(Agent(make_server(tmp_path, clock, mock), clock))
    before = booked_tune_up(agent, 1)
    clock.advance(hours=1)
    summary = reset(agent.app.state.services.settings.db_path, mock_db, TENANT_DIR, clock())
    return agent, mock_db, before, summary


def rows(db, sql: str, *args) -> list:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def on_shift(db, day: date) -> int:
    return rows(db, "SELECT COUNT(DISTINCT technician_id) FROM technician_shifts "
                    "WHERE shift_type = 'Normal' AND substr(start, 1, 10) = ?", day.isoformat())[0][0]


def test_every_day_after_a_reset_has_about_20_technicians(live, clock):
    _, mock_db, _, summary = live
    assert summary["schedule"]["shifts_added"] >= 0
    days = [clock().date() + timedelta(days=d) for d in range(DAYS)]
    counts = {day: on_shift(mock_db, day) for day in days}
    assert all(12 <= n <= 20 for n in counts.values()), counts


def test_nobody_in_the_reset_world_is_named_paul_or_jackson(live):
    _, mock_db, _, _ = live
    for table in ("customers", "technicians"):
        names = [r[0] for r in rows(mock_db, f"SELECT name FROM {table}")]
        assert not [n for n in names if {"Paul", "Jackson"} & set(n.split())], table


def test_the_schedule_page_lists_all_32_with_their_own_first_names(live, clock):
    """Everyone who takes jobs has a row, working or off; about 20 work any one day."""
    agent, mock_db, _, _ = live
    for offset in (0, 3, 5, 6):  # today, a weekday, Saturday and Sunday
        day = clock().date() + timedelta(days=offset)
        html = agent.client.get(f"/owner/schedule?day={day.isoformat()}").text
        names = [cell.split("</td>")[0] for cell in html.split('<td class="what">')[1:]]
        firsts = [name.split()[0] for name in names]
        assert len(names) == 32, (day, len(names))
        assert len(set(firsts)) == 32, (day, firsts)
        assert 12 <= on_shift(mock_db, day) <= 20, day
        assert '<span class="w-name">8 to 10 AM</span>' in html, day


def test_after_a_reset_a_new_call_books_and_reaches_the_owner_view(live):
    agent, _, before, _ = live
    after = booked_tune_up(agent, 2)
    today = agent.client.get("/owner").text
    booked = today[today.index("<h2>Jobs booked</h2>"):]
    assert f'href="/owner/calls/{after}"' in booked
    calls = agent.client.get("/owner/calls").text
    assert after in calls and before in calls, "the call history survives the reset"


def test_most_daytime_windows_stay_open_to_book_after_a_reset(live, clock):
    _, mock_db, _, _ = live
    from servicetitan_mock import capacity

    cfg = TenantConfig.load(TENANT_DIR)
    conn = sqlite3.connect(mock_db)
    conn.row_factory = sqlite3.Row
    try:
        for offset in range(1, 8):
            day = clock().date() + timedelta(days=offset)
            for w in cfg.windows:
                if w.after_hours or not cfg.window_offered(day, w):
                    continue
                slot = capacity.slots(conn, cfg, day, w, 11, [])
                assert slot.total and len(slot.free_fit_ids) / slot.total > 0.5, (day, w.id)
    finally:
        conn.close()
