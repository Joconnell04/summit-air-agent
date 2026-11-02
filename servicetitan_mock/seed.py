"""Build the fake world: python -m servicetitan_mock.seed --db data/servicetitan_mock.db

The tenant's own script (tenants/<name>/seed.py) decides what is in the world. This module only
loads it, wraps it in one transaction and records what it planted.
"""
import argparse
import importlib.util
import json
from datetime import date, datetime
from pathlib import Path

from .config import DEFAULT_TENANT_DIR, TenantConfig
from .db import connect, create_schema


def build_world(db_path: str | Path, tenant_dir: str | Path, start: date | None = None) -> dict:
    """Create the database at db_path from scratch. Returns the scenarios the seed planted."""
    tenant_dir = Path(tenant_dir)
    cfg = TenantConfig.load(tenant_dir)
    start = start or datetime.now(cfg.tz).date()

    spec = importlib.util.spec_from_file_location("tenant_seed", tenant_dir / "seed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    create_schema(db_path)
    conn = connect(db_path)
    conn.execute("BEGIN")
    try:
        scenarios = module.build(conn, cfg, start)
        conn.execute("INSERT INTO meta VALUES ('scenarios', ?)", (json.dumps({"startDate": start.isoformat(), **scenarios}),))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the mock ServiceTitan database.")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_DIR)
    parser.add_argument("--db", default="data/servicetitan_mock.db")
    parser.add_argument("--start-date", help="First day of capacity, YYYY-MM-DD. Default: today.")
    args = parser.parse_args()
    start = date.fromisoformat(args.start_date) if args.start_date else None
    build_world(args.db, args.tenant, start)
    Path(args.db).with_suffix(".log").unlink(missing_ok=True)

    conn = connect(args.db)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("customers", "locations", "memberships", "technicians", "jobs", "appointments", "calls")}
    print("seeded", args.db, counts)


if __name__ == "__main__":
    main()
