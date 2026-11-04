"""Owner view accounts: named users with a role, password sign-in and browser sessions.

Two roles. An admin can change settings, publish rules and manage users; staff can open every page
and send a report to Revin, but can't change a setting, a rule or a user. Every route that
changes something checks the role here, on the server (app/owner_view.py).

Passwords are hashed with scrypt from the standard library (hashlib.scrypt), with a random salt
per password. A session is a random token in an HttpOnly cookie; the database keeps only the
token's SHA-256. The first admin is created from the server's environment (OWNER_ADMIN_*), and
more users are added on the Settings page or with `make owner-user`.

    uv run python -m app.accounts add dana --name "Dana Ruiz" --role staff
    uv run python -m app.accounts password dana
    uv run python -m app.accounts list
"""

import argparse
import base64
import getpass
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

from .db import iso, parse_iso, tx

log = logging.getLogger("app.accounts")

ROLES = ("admin", "staff")
ROLE_LABELS = {"admin": "Admin", "staff": "Staff"}
# Sessions last as long as browsers allow a cookie to (Chrome caps it at 400 days), as the old
# shared-token logins did, so the demo never logs anyone out.
SESSION_DAYS = 400
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,39}")
NAME_MAX_CHARS = 60
PASSWORD_MIN_CHARS = 10
PASSWORD_MAX_CHARS = 200
# scrypt's interactive-login cost from its paper: 16 MB and about 22 ms a check (MEASURED, laptop).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
# The variables the owner view used before accounts. They are no longer read.
LEGACY_VARIABLES = ("OWNER_TOKEN", "RULES_EDITORS")


class AccountError(ValueError):
    """A change that can't be made, with the reason for each field it concerns."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        given = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(given, expected)


# Checked against when a username doesn't exist, so a wrong name takes as long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def clean_username(value: str) -> str:
    return (value or "").strip().lower()


def _check_fields(username: str | None, name: str | None, role: str | None,
                  password: str | None) -> dict[str, str]:
    errors = {}
    if username is not None and not USERNAME.fullmatch(username):
        errors["username"] = "2 to 40 lowercase letters, digits, dots, dashes or underscores."
    if name is not None and not (0 < len(name) <= NAME_MAX_CHARS):
        errors["name"] = f"A name up to {NAME_MAX_CHARS} characters."
    if role is not None and role not in ROLES:
        errors["role"] = "Admin or staff."
    if password is not None and not (PASSWORD_MIN_CHARS <= len(password) <= PASSWORD_MAX_CHARS):
        errors["password"] = f"At least {PASSWORD_MIN_CHARS} characters."
    return errors


def get(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE id = ? AND removed_at IS NULL", (user_id,)
    ).fetchone()


def by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username = ? AND removed_at IS NULL", (clean_username(username),)
    ).fetchone()


def live_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE removed_at IS NULL ORDER BY role, name COLLATE NOCASE"
    ).fetchall()


def _admins(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND removed_at IS NULL"
    ).fetchone()[0]


def _log(conn, user_id: int, action: str, detail: str, by: str, now: datetime) -> None:
    conn.execute(
        "INSERT INTO user_changes (user_id, action, detail, by_name, at) VALUES (?, ?, ?, ?, ?)",
        (user_id, action, detail, by, iso(now)),
    )


def add(conn: sqlite3.Connection, username: str, name: str, role: str, password: str, by: str,
        now: datetime) -> int:
    username, name = clean_username(username), " ".join((name or "").split())
    errors = _check_fields(username, name, role, password)
    if not errors and by_username(conn, username) is not None:
        errors["username"] = "That username is taken."
    if errors:
        raise AccountError(errors)
    hashed = hash_password(password)
    with tx(conn):
        user_id = conn.execute(
            "INSERT INTO users (username, name, role, password_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, name, role, hashed, iso(now), iso(now)),
        ).lastrowid
        _log(conn, user_id, "added", role, by, now)
    return user_id


def set_role(conn: sqlite3.Connection, user_id: int, role: str, by: str, now: datetime) -> None:
    if role not in ROLES:
        raise AccountError({"role": "Admin or staff."})
    with tx(conn):
        user = get(conn, user_id)
        if user is None:
            raise AccountError({"user": "No such user."})
        if user["role"] == role:
            return
        # Read inside the write lock, so two admins demoting each other can't leave none.
        if user["role"] == "admin" and _admins(conn) == 1:
            raise AccountError({"role": "Someone has to stay an admin."})
        conn.execute(
            "UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, iso(now), user_id)
        )
        _log(conn, user_id, "role", role, by, now)


def remove(conn: sqlite3.Connection, user_id: int, by_id: int, by: str, now: datetime) -> None:
    """Remove a user and sign them out everywhere. An admin can't remove themselves, and the last
    admin can't be removed."""
    if user_id == by_id:
        raise AccountError({"user": "You can't remove yourself."})
    with tx(conn):
        user = get(conn, user_id)
        if user is None:
            raise AccountError({"user": "No such user."})
        if user["role"] == "admin" and _admins(conn) == 1:
            raise AccountError({"user": "Someone has to stay an admin."})
        conn.execute(
            "UPDATE users SET removed_at = ?, updated_at = ? WHERE id = ?",
            (iso(now), iso(now), user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        _log(conn, user_id, "removed", "", by, now)


def set_password(conn: sqlite3.Connection, user_id: int, password: str, by: str,
                 now: datetime) -> None:
    """A new password signs the user out of every browser."""
    errors = _check_fields(None, None, None, password)
    if errors:
        raise AccountError(errors)
    hashed = hash_password(password)
    with tx(conn):
        if get(conn, user_id) is None:
            raise AccountError({"user": "No such user."})
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hashed, iso(now), user_id),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        _log(conn, user_id, "password", "", by, now)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> sqlite3.Row | None:
    user = by_username(conn, username)
    if user is None:
        check_password(password or "", _DUMMY_HASH)
        return None
    return user if check_password(password or "", user["password_hash"]) else None


def start_session(conn: sqlite3.Connection, user_id: int, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    with tx(conn):
        # Expired sessions are cleared as new ones start, so the table can't grow without end.
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(now),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, iso(now), iso(now + timedelta(days=SESSION_DAYS))),
        )
    return token


def session_user(conn: sqlite3.Connection, token: str | None, now: datetime) -> sqlite3.Row | None:
    """The live user behind a session cookie, or None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND u.removed_at IS NULL",
        (_token_hash(token),),
    ).fetchone()
    if row is None or parse_iso(row["expires_at"]) < now:
        return None
    return row


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        with tx(conn):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def changes(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.*, u.name AS user_name FROM user_changes c JOIN users u ON u.id = c.user_id "
        "ORDER BY c.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def seed_admin(conn: sqlite3.Connection, username: str, name: str, password: str,
               now: datetime, environ=os.environ) -> int | None:
    """Create the first admin from the server's environment, once. An existing user of that name
    is left alone, so a password changed since isn't reset by a restart. Variables from before
    accounts are reported, since they no longer open anything."""
    for variable in LEGACY_VARIABLES:
        if environ.get(variable):
            log.warning("%s is no longer read; sign in with an account (app/accounts.py)", variable)
    if not (username and password):
        return None
    if by_username(conn, username) is not None:
        return None
    try:
        return add(conn, username, name or "Owner", "admin", password, "Server setup", now)
    except AccountError as err:
        log.error("seed admin not created: %s", err)
        return None


def _password(prompt: str = "Password: ") -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(prompt)
    if getpass.getpass("Again: ") != first:
        raise SystemExit("The two passwords differ.")
    return first


def main(argv: list[str] | None = None) -> int:
    from .db import connect, init
    from .settings import Settings

    parser = argparse.ArgumentParser(prog="python -m app.accounts")
    parser.add_argument("--db", help="the tool server's database (default: TOOL_DB)")
    sub = parser.add_subparsers(dest="command", required=True)
    added = sub.add_parser("add", help="add a user; the password is read from the terminal")
    added.add_argument("username")
    added.add_argument("--name", required=True)
    added.add_argument("--role", choices=ROLES, default="staff")
    changed = sub.add_parser("password", help="set a user's password and sign them out")
    changed.add_argument("username")
    sub.add_parser("list", help="list users")
    args = parser.parse_args(argv)

    path = args.db or Settings().db_path
    init(path)
    conn = connect(path)
    now = datetime.now(UTC)
    try:
        if args.command == "list":
            for user in live_users(conn):
                print(f"{user['username']:<20} {ROLE_LABELS[user['role']]:<6} {user['name']}")
            return 0
        if args.command == "add":
            add(conn, args.username, args.name, args.role, _password(), "Command line", now)
            print(f"Added {clean_username(args.username)} as {args.role}.")
            return 0
        user = by_username(conn, args.username)
        if user is None:
            print(f"No user {args.username}.", file=sys.stderr)
            return 1
        set_password(conn, user["id"], _password("New password: "), "Command line", now)
        print(f"Password set for {user['username']}; their browsers are signed out.")
        return 0
    except AccountError as err:
        print(str(err), file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
