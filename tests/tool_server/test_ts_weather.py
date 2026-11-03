"""The outside temperature read (app/weather.py): hourly at most, never stale, fails quietly."""

from datetime import UTC, datetime, timedelta

import httpx
from toolkit import TENANT_DIR

from app import db, weather
from app.tenant import Tenant

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def nws(celsius=-5.0, hourly=(20, 18, 15)):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/observations/latest"):
            return httpx.Response(200, json={"properties": {"temperature": {"value": celsius}}})
        periods = [
            {
                "startTime": (NOW + timedelta(hours=i)).isoformat(),
                "endTime": (NOW + timedelta(hours=i + 1)).isoformat(),
                "temperature": t,
            }
            for i, t in enumerate(hourly)
        ]
        return httpx.Response(200, json={"properties": {"periods": periods}})

    client = httpx.Client(base_url=weather.BASE_URL, transport=httpx.MockTransport(handler))
    return client, calls


def setup(tmp_path):
    path = tmp_path / "t.db"
    db.init(path)
    return db.connect(path), Tenant.load(TENANT_DIR)


def test_a_reading_is_stored_and_read_back_in_fahrenheit(tmp_path):
    conn, tenant = setup(tmp_path)
    client, _ = nws(celsius=-5.0, hourly=(20, 18, 15))
    assert weather.refresh(conn, tenant, client, NOW) == "refreshed"
    outside = weather.current(conn, tenant, NOW)
    assert outside.now_f == 23.0 and outside.low_f == 15 and outside.coldest_f == 15


def test_it_reads_at_most_hourly(tmp_path):
    conn, tenant = setup(tmp_path)
    client, calls = nws()
    weather.refresh(conn, tenant, client, NOW)
    assert weather.refresh(conn, tenant, client, NOW + timedelta(minutes=30)) == "fresh"
    assert len(calls) == 2


def test_a_reading_over_three_hours_old_is_unknown(tmp_path):
    conn, tenant = setup(tmp_path)
    client, _ = nws()
    weather.refresh(conn, tenant, client, NOW)
    assert weather.current(conn, tenant, NOW + timedelta(hours=4)).coldest_f is None


def test_a_failed_read_keeps_the_last_value(tmp_path):
    conn, tenant = setup(tmp_path)
    good, _ = nws()
    weather.refresh(conn, tenant, good, NOW)
    down = httpx.Client(
        base_url=weather.BASE_URL, transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    assert weather.refresh(conn, tenant, down, NOW + timedelta(hours=2)) == "failed"
    assert weather.current(conn, tenant, NOW + timedelta(hours=2)).now_f == 23.0


def test_a_tenant_without_a_weather_block_is_off(tmp_path):
    conn, tenant = setup(tmp_path)
    tenant.weather = None
    client, calls = nws()
    assert weather.refresh(conn, tenant, client, NOW) == "off" and calls == []
