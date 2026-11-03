"""Outside temperature for priority (docs/explorations/007-priority-brackets.md): the National
Weather Service's latest observation and the lowest temperature forecast for the next 12 hours.
The background jobs read it at most hourly; a caller's turn only reads the stored value, and a
value older than three hours counts as unknown."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from .db import iso, parse_iso, tx
from .tenant import Tenant

BASE_URL = "https://api.weather.gov"
REFRESH_AFTER = timedelta(hours=1)
FRESH_FOR = timedelta(hours=3)
FORECAST_HOURS = 12


@dataclass
class Outside:
    now_f: float | None
    low_f: float | None

    @property
    def coldest_f(self) -> float | None:
        known = [t for t in (self.now_f, self.low_f) if t is not None]
        return min(known) if known else None


def current(conn: sqlite3.Connection, tenant: Tenant, now: datetime) -> Outside:
    row = conn.execute("SELECT * FROM weather WHERE tenant = ?", (tenant.slug,)).fetchone()
    if not row or now - parse_iso(row["fetched_at"]) > FRESH_FOR:
        return Outside(None, None)
    return Outside(row["now_f"], row["low_f"])


def refresh(conn: sqlite3.Connection, tenant: Tenant, http: httpx.Client, now: datetime) -> str:
    """Store a new reading when the last one is over an hour old. A failed read keeps the old
    value, which ages out on its own."""
    cfg = tenant.weather
    if not cfg:
        return "off"
    row = conn.execute("SELECT fetched_at FROM weather WHERE tenant = ?", (tenant.slug,)).fetchone()
    if row and now - parse_iso(row["fetched_at"]) < REFRESH_AFTER:
        return "fresh"
    try:
        now_f = _observed_f(http, cfg["station"])
        low_f = _forecast_low_f(http, cfg["gridpoint"], now)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return "failed"
    if now_f is None and low_f is None:
        return "failed"
    with tx(conn):
        conn.execute(
            "INSERT INTO weather (tenant, fetched_at, now_f, low_f) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (tenant) DO UPDATE SET fetched_at = excluded.fetched_at, "
            "now_f = excluded.now_f, low_f = excluded.low_f",
            (tenant.slug, iso(now), now_f, low_f),
        )
    return "refreshed"


def _observed_f(http: httpx.Client, station: str) -> float | None:
    r = http.get(f"/stations/{station}/observations/latest")
    r.raise_for_status()
    celsius = r.json()["properties"]["temperature"]["value"]
    return None if celsius is None else round(celsius * 9 / 5 + 32, 1)


def _forecast_low_f(http: httpx.Client, gridpoint: str, now: datetime) -> float | None:
    r = http.get(f"/gridpoints/{gridpoint}/forecast/hourly", params={"units": "us"})
    r.raise_for_status()
    until = now + timedelta(hours=FORECAST_HOURS)
    temps = [
        float(p["temperature"])
        for p in r.json()["properties"]["periods"]
        if datetime.fromisoformat(p["endTime"]) > now
        and datetime.fromisoformat(p["startTime"]) < until
    ]
    return min(temps) if temps else None


def client(tenant_weather: dict | None) -> httpx.Client | None:
    if not tenant_weather:
        return None
    return httpx.Client(
        base_url=BASE_URL, timeout=10, headers={"User-Agent": tenant_weather["user_agent"]}
    )
