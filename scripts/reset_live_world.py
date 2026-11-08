"""Resets the live world between calls: reseeds the mock ServiceTitan, clears what the tool server
holds that points into the old world, then opens the live schedule again. Run on the server from
the app checkout, with the services running:

    uv run --env-file ../.env python -m scripts.reset_live_world

Moving servicetitan_mock.db aside and letting run.sh reseed left the tool server holding the old
world's customer, location and job ids: a scripted call linked to an earlier call, carried its
customer 301 (gone from the new world), skipped the name question, and ServiceTitan refused the new
customer with no name (call-_+16466240418_ECUCgf3cyyQP).

What it does:
- Reseeds the mock in place (servicetitan_mock.seed.reseed: built beside it, copied in with
  SQLite's backup API, so a request sees the old world or the new one, never a mix).
- Records the reset, so no later call picks up one from before it (lifecycle._linked), and clears
  the rows that name old-world ids: the customers matched on each call, name checks and moves.
- Stops the work queued against the old world: bookings waiting to send, earlier-slot offers and
  their seeking, heads-up calls, leads waiting to send, notes not yet posted and summaries not yet
  pinned to a job.
- Keeps the call history the owner view shows: calls, turns, bookings, flags and callbacks.
- Clears the do-not-call list, so a test number marked on one demo call isn't blocked on the next.
- Runs open_live_schedule on the new world.

It refuses while a call is live (no end in the last LIVE_MINUTES), since the call would keep ids
from the old world: call_7255ab47e30637f207e49f5bdeb was reset one second in, and its visit 548 then
came back "not booked for you". --force skips that check.

The tool server's slot cache (SLOT_CACHE_SECONDS, 60 by default) clears on its own within a minute.
"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db
from scripts.open_live_schedule import open_schedule
from servicetitan_mock.config import DEFAULT_TENANT_DIR, TenantConfig
from servicetitan_mock.seed import reseed

WHY = "world reset"
LIVE_MINUTES = 30


def live_calls(tool_db: Path, now: datetime) -> list[str]:
    """Calls with no end that started in the last LIVE_MINUTES."""
    since = db.iso(now - timedelta(minutes=LIVE_MINUTES))
    conn = db.connect(tool_db)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT id FROM calls WHERE ended_at IS NULL AND started_at >= ?", (since,)
            )
        ]
    finally:
        conn.close()


def clear_tool_server(tool_db: Path, now: datetime) -> dict[str, int]:
    """Forget the old world's ids and stop the work queued against it. Returns rows changed."""
    stamp = db.iso(now)
    statements = {
        "resets": ("INSERT INTO world_resets (at) VALUES (?)", (stamp,)),
        "call_customers": ("DELETE FROM call_customers", ()),
        "identity_checks": ("DELETE FROM identity_checks", ()),
        "address_moves": ("DELETE FROM address_moves", ()),
        "do_not_call": ("DELETE FROM do_not_call", ()),
        "bookings_queued": (
            ("UPDATE bookings SET status = 'failed', last_error = ?, updated_at = ? "
            "WHERE status IN ('queued', 'sending')"),
            (WHY, stamp),
        ),
        "seek_earlier": (
            "UPDATE bookings SET seek_earlier = 0, updated_at = ? WHERE seek_earlier = 1",
            (stamp,),
        ),
        "offers": (
            ("UPDATE outbound SET status = 'skipped', result = ?, updated_at = ? "
            "WHERE kind = 'earlier_slot' AND status IN ('waiting', 'queued')"),
            (WHY, stamp),
        ),
        "heads_ups": (
            ("UPDATE heads_ups SET status = 'skipped', result = ?, updated_at = ? "
            "WHERE status = 'queued'"),
            (WHY, stamp),
        ),
        "leads": ("UPDATE leads SET status = 'failed' WHERE status = 'queued'", ()),
        "notes": ("DELETE FROM notes WHERE fsm_note_id IS NULL", ()),
        "summaries": (
            ("UPDATE calls SET summary_status = 'skipped' "
            "WHERE summary_status IN ('pending', 'written')"),
            (),
        ),
    }
    db.init(tool_db)  # adds world_resets to a database made before it existed
    conn = db.connect(tool_db)
    try:
        with db.tx(conn):
            return {name: conn.execute(sql, args).rowcount for name, (sql, args) in statements.items()}
    finally:
        conn.close()


def reset(tool_db: Path, mock_db: Path, tenant_dir: Path, now: datetime, force: bool = False) -> dict:
    """The whole reset, as the server runs it: the same steps a test drives."""
    live = live_calls(tool_db, now)
    if live and not force:
        raise SystemExit(f"not reset: {len(live)} call(s) live ({', '.join(live)}); --force to")
    today = now.astimezone(TenantConfig.load(tenant_dir).tz).date()
    scenarios = reseed(mock_db, tenant_dir, today)
    cleared = clear_tool_server(tool_db, now)
    opened = open_schedule(mock_db, tenant_dir, today, 14, 6)
    return {"reseeded": len(scenarios), "cleared": cleared, "schedule": opened}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tool-db", default="data/tool_server.db")
    parser.add_argument("--mock-db", default="data/servicetitan_mock.db")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_DIR)
    parser.add_argument("--force", action="store_true", help="reset even while a call is live")
    args = parser.parse_args()
    found = reset(
        Path(args.tool_db), Path(args.mock_db), Path(args.tenant), datetime.now(UTC), args.force
    )
    print(json.dumps(found))


if __name__ == "__main__":
    main()
