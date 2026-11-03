"""Call recordings (story O-6). The voice layer records each call into an S3-compatible bucket,
because LiveKit Cloud's recorder can only upload to cloud storage. The jobs loop copies each file
into the server's data folder and deletes it from the bucket, so the bucket only holds calls still
in progress, and the owner view plays the copy here. A recording is deleted with its call's
transcript (spec S-45).
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .settings import Settings
from .tenant import Tenant

log = logging.getLogger("app.recordings")

# A finished file appears in the bucket a few seconds after the room closes; one not there after
# this long never will be.
GIVE_UP_AFTER = timedelta(hours=2)
MEDIA_TYPE = "audio/mp4"
# What S3-compatible stores answer for an object that isn't there yet.
NOT_THERE = {"404", "NoSuchKey", "NotFound"}


class Bucket:
    """The bucket LiveKit uploads to. Only three calls: is a file there, copy it here, delete it."""

    def __init__(self, settings: Settings, client=None):
        self.name = settings.recording_bucket
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=settings.recording_endpoint or None,
                region_name=settings.recording_region or None,
                aws_access_key_id=settings.recording_access_key,
                aws_secret_access_key=settings.recording_secret,
            )
        self.client = client

    def fetch(self, key: str, dest: Path) -> bool:
        """Copy the object to dest. False when it isn't there yet, or the bucket refused, which is
        logged: a wrong key or bucket shows up in the log, not as a recording quietly missing."""
        try:
            self.client.head_object(Bucket=self.name, Key=key)
        except self.client.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in NOT_THERE:
                log.warning("bucket refused %s: %s", key, code or exc)
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(".part")
        self.client.download_file(self.name, key, str(partial))
        partial.replace(dest)
        return True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.name, Key=key)


def bucket(settings: Settings) -> Bucket | None:
    """None when no bucket is set, which turns recording off."""
    return Bucket(settings) if settings.recording_bucket else None


def object_key(tenant: Tenant, call_id: str) -> str:
    return f"{tenant.slug}/{call_id}.mp4"


def inside(folder: Path, path: Path) -> Path | None:
    """The path, resolved, if it is inside the recordings folder. A call id comes from the voice
    layer, so a file name built from it is checked before anything is written, read or deleted."""
    resolved = path.resolve()
    return resolved if resolved.is_relative_to(folder.resolve()) else None


def wanted(tenant: Tenant, configured: bool) -> bool:
    """Whether calls for this tenant are recorded: the company's choice, and a bucket to put them."""
    return configured and bool(tenant.retention.get("record_calls"))


def started(
    conn: sqlite3.Connection, call: sqlite3.Row, egress_id: str, key: str, now: datetime
) -> Result:
    """The voice layer started recording. The key must be the one this server gave the call, so a
    report can't point the owner view at another call's audio."""
    if key != f"{call['tenant']}/{call['id']}.mp4":
        raise Refused("invalid_arguments", "", errors={"object_key": "not this call's key"})
    with tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO recordings (call_id, object_key, egress_id, started_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?)",
            (call["id"], key, egress_id, iso(now), iso(now)),
        )
    return ok()


def collect(
    conn: sqlite3.Connection, tenant: Tenant, store: Bucket | None, folder: Path, now: datetime
) -> dict:
    """Copy finished recordings from the bucket into the data folder, and remove them there."""
    if store is None:
        return {"stored": 0, "missing": 0}
    rows = conn.execute(
        "SELECT r.*, c.ended_at FROM recordings r JOIN calls c ON c.id = r.call_id "
        "WHERE c.tenant = ? AND r.status = 'recording' AND c.ended_at IS NOT NULL",
        (tenant.slug,),
    ).fetchall()
    stored = missing = 0
    for row in rows:
        dest = inside(folder, folder / row["object_key"])
        if dest is None:
            log.warning("recording for call %s has a name outside the folder", row["call_id"])
            with tx(conn):
                conn.execute(
                    "UPDATE recordings SET status = 'missing', updated_at = ? WHERE call_id = ?",
                    (iso(now), row["call_id"]),
                )
            missing += 1
            continue
        if store.fetch(row["object_key"], dest):
            store.delete(row["object_key"])
            with tx(conn):
                conn.execute(
                    "UPDATE recordings SET status = 'stored', path = ?, bytes = ?, updated_at = ? "
                    "WHERE call_id = ?",
                    (str(dest), dest.stat().st_size, iso(now), row["call_id"]),
                )
            stored += 1
        elif parse_iso(row["ended_at"]) < now - GIVE_UP_AFTER:
            log.warning("recording for call %s never reached the bucket", row["call_id"])
            with tx(conn):
                conn.execute(
                    "UPDATE recordings SET status = 'missing', updated_at = ? WHERE call_id = ?",
                    (iso(now), row["call_id"]),
                )
            missing += 1
    return {"stored": stored, "missing": missing}


def purge(
    conn: sqlite3.Connection, tenant: Tenant, folder: Path, before: datetime, now: datetime
) -> int:
    """Delete the audio of calls that started before `before`, the transcript cutoff. The row stays
    as 'deleted' until the call record itself goes."""
    rows = conn.execute(
        "SELECT r.* FROM recordings r JOIN calls c ON c.id = r.call_id "
        "WHERE c.tenant = ? AND c.started_at < ? AND r.status != 'deleted'",
        (tenant.slug, iso(before)),
    ).fetchall()
    for row in rows:
        path = inside(folder, Path(row["path"])) if row["path"] else None
        if path is not None:
            path.unlink(missing_ok=True)
        with tx(conn):
            conn.execute(
                "UPDATE recordings SET status = 'deleted', path = NULL, updated_at = ? "
                "WHERE call_id = ?",
                (iso(now), row["call_id"]),
            )
    return len(rows)


def stored_path(conn: sqlite3.Connection, call_id: str, folder: Path) -> Path | None:
    """The call's audio file on this server, or None. Only ever a file inside the folder."""
    row = conn.execute(
        "SELECT path FROM recordings WHERE call_id = ? AND status = 'stored'", (call_id,)
    ).fetchone()
    if row is None or not row["path"]:
        return None
    path = inside(folder, Path(row["path"]))
    return path if path is not None and path.exists() else None
