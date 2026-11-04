"""Call recordings (story O-6). The voice layer records each call into an S3-compatible bucket,
because LiveKit Cloud's recorder can only upload to cloud storage. The jobs loop copies each file
into the server's data folder and deletes it from the bucket, so the bucket only holds calls still
in progress, and the owner view plays the copy here. A recording is deleted with its call's
transcript (spec S-45).

With the owner's two-party consent setting on (app/owner_settings.py), every greeting says the call
may be recorded, and a caller who objects isn't: the server reads their words, tells the voice layer
to stop, and deletes the file from the bucket instead of keeping it, so the audio is gone even if
the voice layer never stopped.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db import iso, parse_iso, tx
from .envelope import Refused, Result, ok
from .settings import Settings
from .tenant import Tenant
from .text import has_phrase, words

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

    def discard(self, key: str) -> bool:
        """Delete the object without copying it. False when it isn't there yet."""
        try:
            self.client.head_object(Bucket=self.name, Key=key)
        except self.client.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in NOT_THERE:
                log.warning("bucket refused %s: %s", key, code or exc)
            return False
        self.delete(key)
        return True


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


def wanted(values: dict[str, bool], configured: bool) -> bool:
    """Whether a call is recorded: the owner's setting the call started under (its default is the
    tenant's record_calls), and a bucket to put it in."""
    return configured and bool(values.get("call_recordings"))


# What is said under two-party consent, unless the tenant's `voice:` lines set their own
# (recording_notice, recording_stopped). Plain defaults here, so turning the setting on needs no
# tenant file change; a tenant file change moves the eval fingerprint (evals/fingerprint.py).
NOTICE = "This call may be recorded."
STOPPED = "Okay, I've turned the recording off."


def notice(tenant: Tenant) -> str:
    """Said just before the greeting."""
    return tenant.agent["voice"].get("recording_notice") or NOTICE


def stopped_line(tenant: Tenant) -> str:
    """Said by the voice layer when it stops the recording, before the agent answers."""
    return tenant.agent["voice"].get("recording_stopped") or STOPPED


# A caller saying they don't want the call recorded. Matched on whole words, so "record" in "my
# records say" or "a record cold snap" doesn't count.
OBJECTIONS = (
    "dont record",
    "do not record",
    "stop recording",
    "stop the recording",
    "turn off the recording",
    "turn the recording off",
    "not be recorded",
    "not to be recorded",
    "not being recorded",
    "dont want to be recorded",
    "rather not be recorded",
    "no recording",
    "dont consent",
    "do not consent",
)


def objected(text: str) -> bool:
    said = words(text)
    return any(has_phrase(said, phrase) for phrase in OBJECTIONS)


def declined(conn: sqlite3.Connection, call_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM recording_declines WHERE call_id = ?", (call_id,)
    ).fetchone() is not None


def decline(conn: sqlite3.Connection, call_id: str, seq: int, now: datetime) -> None:
    """The caller objected on turn `seq`. Inside the turn's transaction."""
    conn.execute(
        "INSERT OR IGNORE INTO recording_declines (call_id, seq, at) VALUES (?, ?, ?)",
        (call_id, seq, iso(now)),
    )


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
    """Copy finished recordings from the bucket into the data folder, and remove them there. The
    recording of a caller who objected is deleted from the bucket and never copied."""
    if store is None:
        return {"stored": 0, "missing": 0, "declined": 0}
    rows = conn.execute(
        "SELECT r.*, c.ended_at, d.call_id AS declined FROM recordings r "
        "JOIN calls c ON c.id = r.call_id LEFT JOIN recording_declines d ON d.call_id = r.call_id "
        "WHERE c.tenant = ? AND r.status = 'recording' AND c.ended_at IS NOT NULL",
        (tenant.slug,),
    ).fetchall()
    stored = missing = refused = 0
    for row in rows:
        if row["declined"]:
            # The file lands a few seconds after the call ends, so the row waits until it has been
            # seen and deleted, or until it never will be.
            gone = store.discard(row["object_key"])
            if gone or parse_iso(row["ended_at"]) < now - GIVE_UP_AFTER:
                with tx(conn):
                    conn.execute(
                        "UPDATE recordings SET status = 'deleted', path = NULL, updated_at = ? "
                        "WHERE call_id = ?",
                        (iso(now), row["call_id"]),
                    )
                refused += 1
            continue
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
    return {"stored": stored, "missing": missing, "declined": refused}


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
