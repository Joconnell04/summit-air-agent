"""Settings an owner switches on the Settings page: a fixed list of on/off choices, each read by the
server code it applies to. The agent never sees or sets one.

Every change is a new row in setting_changes, so the page can show who changed what and when, and
nothing is ever overwritten. A call reads the values once, when it starts (pin), and keeps them to
the end, so a change mid-call can't flip a call halfway: the greeting, the recording, a transfer,
the technician summary and the on-call alert all read the same values.

None of these loosens a gate. Turning one off only drops something extra (a recording, a transfer,
a summary, an alert email), and safety alerts are sent whatever the setting.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .db import iso, tx
from .tenant import Tenant


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    # One short line on the page under the label.
    detail: str
    # Where the setting is read, for the engineer reading the code.
    where: str


SETTINGS = (
    Setting(
        "two_party_consent",
        "Two-party consent",
        "Each greeting says the call may be recorded.",
        "app/lifecycle.py start and turn, app/recordings.py collect",
    ),
    Setting(
        "call_recordings",
        "Call recordings",
        "Record calls to play back on the call page.",
        "app/lifecycle.py start (recordings.wanted)",
    ),
    Setting(
        "warm_transfer",
        "Warm transfer",
        "Put a caller through to the office or the on-call technician.",
        "app/transfer.py role_for",
    ),
    Setting(
        "hold_line",
        "Hold line",
        "Transfers ring the hold line instead of a person.",
        "app/main.py _ctx (transfer.held)",
    ),
    Setting(
        "technician_summaries",
        "Technician summaries",
        "Write a job note for the technician after each booking.",
        "app/summary.py run",
    ),
    Setting(
        "on_call_alerts",
        "On-call alerts",
        "Email the on-call person about urgent calls after hours. Safety alerts always send.",
        "app/jobs.py alert_on_call",
    ),
)
BY_KEY = {s.key: s for s in SETTINGS}


class SettingsError(ValueError):
    pass


def defaults(tenant: Tenant) -> dict[str, bool]:
    """What a company starts with. Two-party consent starts off, which suits a one-party state such
    as New York, where one party's consent is enough to record (N.Y. Penal Law 250.00 and 250.05);
    a company in a two-party state turns it on."""
    return {
        "two_party_consent": False,
        "call_recordings": bool(tenant.retention.get("record_calls")),
        "warm_transfer": bool(tenant.transfer),
        # On by default, so a test call or a demo never rings the office phone unless the owner
        # turns it off (Jackson, 2026-11-06). With no hold number set it changes nothing.
        "hold_line": True,
        "technician_summaries": True,
        "on_call_alerts": True,
    }


def shown(tenant: Tenant) -> tuple[Setting, ...]:
    """The settings that mean something for this company: warm transfer only where it is set up,
    and call recordings only where the tenant's config allows recording at all."""
    hidden = set()
    if not tenant.transfer:
        hidden.update(("warm_transfer", "hold_line"))
    if not tenant.retention.get("record_calls"):
        hidden.add("call_recordings")
    return tuple(s for s in SETTINGS if s.key not in hidden)


def current(conn: sqlite3.Connection, tenant: Tenant) -> dict[str, bool]:
    values = defaults(tenant)
    rows = conn.execute(
        "SELECT key, value FROM setting_changes WHERE id IN "
        "(SELECT MAX(id) FROM setting_changes WHERE tenant = ? GROUP BY key)",
        (tenant.slug,),
    ).fetchall()
    for row in rows:
        if row["key"] in values:
            values[row["key"]] = bool(row["value"])
    return values


def change(
    conn: sqlite3.Connection, tenant: Tenant, wanted: dict[str, bool], author: str, now: datetime
) -> list[str]:
    """Store the values that differ from the current ones, one row each, and return their keys.
    A key that isn't on the list is refused, whatever sent it."""
    unknown = sorted(set(wanted) - set(BY_KEY))
    if unknown:
        raise SettingsError(f"No such setting: {', '.join(unknown)}")
    with tx(conn):
        now_values = current(conn, tenant)
        changed = [k for k in BY_KEY if k in wanted and bool(wanted[k]) != now_values[k]]
        for key in changed:
            conn.execute(
                "INSERT INTO setting_changes (tenant, key, value, author, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tenant.slug, key, int(bool(wanted[key])), author, iso(now)),
            )
    return changed


def history(conn: sqlite3.Connection, tenant: Tenant, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM setting_changes WHERE tenant = ? ORDER BY id DESC LIMIT ?",
        (tenant.slug, limit),
    ).fetchall()


def pin(conn: sqlite3.Connection, tenant: Tenant, call_id: str) -> dict[str, bool]:
    """The values this call keeps to its end, stored the first time it asks. Called inside the
    call's start; a second start for the same call gets the first values back."""
    conn.execute(
        "INSERT OR IGNORE INTO call_settings (call_id, settings_json) VALUES (?, ?)",
        (call_id, json.dumps(current(conn, tenant), sort_keys=True)),
    )
    return for_call(conn, tenant, call_id)


def for_call(conn: sqlite3.Connection, tenant: Tenant, call_id: str) -> dict[str, bool]:
    """The values a call started under, or the current ones for a call from before settings."""
    row = conn.execute(
        "SELECT settings_json FROM call_settings WHERE call_id = ?", (call_id,)
    ).fetchone()
    if row is None:
        return current(conn, tenant)
    # A setting added after the call started reads as its default.
    return {**defaults(tenant), **json.loads(row["settings_json"])}
