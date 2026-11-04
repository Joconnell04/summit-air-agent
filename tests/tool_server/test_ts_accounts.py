"""Owner view accounts (app/accounts.py): named users with a role, hashed passwords, sessions, the
seed admin from the server's environment, the command line, and the move from the shared read
token and rules edit codes to accounts. Every route that changes something refuses staff."""

import io
import logging
import sqlite3
from datetime import timedelta

import pytest
from test_ts_rules import PETS, publish
from toolkit import PASSWORD, Agent, add_user, make_server, sign_in

from app import accounts, db, owner_settings, rules
from app.settings import Settings


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


def conn_of(agent: Agent) -> sqlite3.Connection:
    return agent.app.state.services.connect()


def user_named(agent: Agent, username: str):
    conn = conn_of(agent)
    try:
        return accounts.by_username(conn, username)
    finally:
        conn.close()


# ---- passwords and users ----------------------------------------------------------------------


def test_a_password_is_kept_as_a_salted_scrypt_hash():
    first, second = accounts.hash_password(PASSWORD), accounts.hash_password(PASSWORD)
    assert first.startswith("scrypt$") and PASSWORD not in first
    assert first != second, "each hash has its own salt"
    assert accounts.check_password(PASSWORD, first)
    assert not accounts.check_password("wrong-password", first)


@pytest.mark.parametrize("stored", ["", "plain", "md5$x$y$z$a$b", "scrypt$16$8$1$!!$??"])
def test_a_stored_hash_that_isnt_scrypt_never_matches(stored):
    assert not accounts.check_password(PASSWORD, stored)


@pytest.mark.parametrize("field, value", [
    ("username", "A"),
    ("username", "has space"),
    ("username", "x" * 41),
    ("name", ""),
    ("name", "x" * 61),
    ("role", "owner"),
    ("password", "short"),
])
def test_a_user_with_a_bad_field_is_refused(agent, field, value):
    fields = {"username": "dana", "name": "Dana Ruiz", "role": "staff", "password": PASSWORD}
    fields[field] = value
    conn = conn_of(agent)
    try:
        with pytest.raises(accounts.AccountError) as err:
            accounts.add(conn, by="Test", now=agent.clock(), **fields)
        assert field in err.value.errors
    finally:
        conn.close()


def test_a_username_is_taken_until_its_user_is_removed(agent):
    first = add_user(agent, "dana", "staff")
    admin = add_user(agent, "pat", "admin")
    conn = conn_of(agent)
    try:
        with pytest.raises(accounts.AccountError):
            accounts.add(conn, "Dana", "Dana Two", "staff", PASSWORD, "Test", agent.clock())
        accounts.remove(conn, first, admin, "Pat", agent.clock())
        again = accounts.add(conn, "dana", "Dana Two", "staff", PASSWORD, "Test", agent.clock())
        assert again != first
        row = conn.execute("SELECT removed_at FROM users WHERE id = ?", (first,)).fetchone()
        assert row["removed_at"], "the old row stays, so its name still reads in the history"
    finally:
        conn.close()


def test_the_last_admin_cannot_be_made_staff_or_removed(agent):
    admin = add_user(agent, "pat", "admin")
    other = add_user(agent, "sam", "staff")
    conn = conn_of(agent)
    try:
        with pytest.raises(accounts.AccountError, match="stay an admin"):
            accounts.set_role(conn, admin, "staff", "Pat", agent.clock())
        with pytest.raises(accounts.AccountError, match="stay an admin"):
            accounts.remove(conn, admin, other, "Sam", agent.clock())
        accounts.set_role(conn, other, "admin", "Pat", agent.clock())
        accounts.set_role(conn, admin, "staff", "Sam", agent.clock())
        assert accounts.get(conn, admin)["role"] == "staff"
    finally:
        conn.close()


def test_an_admin_cannot_remove_themselves(agent):
    admin = add_user(agent, "pat", "admin")
    add_user(agent, "lee", "admin")
    conn = conn_of(agent)
    try:
        with pytest.raises(accounts.AccountError, match="yourself"):
            accounts.remove(conn, admin, admin, "Pat", agent.clock())
    finally:
        conn.close()


# ---- sessions ---------------------------------------------------------------------------------


def test_only_the_tokens_hash_is_stored(agent):
    sign_in(agent)
    token = agent.client.cookies.get("owner_session")
    conn = conn_of(agent)
    try:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    finally:
        conn.close()
    assert token and stored and token not in stored


def test_a_session_ends_after_400_days(agent, clock):
    sign_in(agent)
    clock.advance(days=399)
    assert agent.client.get("/owner").status_code == 200
    clock.advance(days=2)
    assert agent.client.get("/owner").status_code == 401


def test_a_new_password_signs_the_user_out_everywhere(agent):
    sign_in(agent, "staff", "sam")
    conn = conn_of(agent)
    try:
        accounts.set_password(conn, user_named(agent, "sam")["id"], "another-password", "Test",
                              agent.clock())
    finally:
        conn.close()
    assert agent.client.get("/owner").status_code == 401


# ---- signing in and out -----------------------------------------------------------------------


OWNER_PAGES = [
    "/owner",
    "/owner/calls",
    "/owner/results",
    "/owner/rules",
    "/owner/rules/edit",
    "/owner/settings",
    "/owner/search?q=union",
    "/owner/design",
    "/owner/calls/nope",
    "/owner/calls/nope/log",
    "/owner/calls/nope/recording",
]


@pytest.mark.parametrize("path", OWNER_PAGES)
def test_every_owner_page_needs_a_sign_in(agent, path):
    page = agent.client.get(path)
    assert page.status_code == 401 and 'name="password"' in page.text


def test_signing_in_sets_a_strict_http_only_cookie_for_400_days(agent):
    add_user(agent, "pat", "admin")
    done = agent.client.post(
        "/owner/login", data={"username": "Pat", "password": PASSWORD}, follow_redirects=False
    )
    assert done.status_code == 303 and done.headers["location"] == "/owner"
    cookie = next(c for c in done.headers.get_list("set-cookie") if c.startswith("owner_session="))
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=strict" in lowered
    assert f"max-age={400 * 86400}" in lowered
    assert agent.client.get("/owner").status_code == 200


@pytest.mark.parametrize("username, password", [
    ("pat", "wrong-password"),
    ("nobody", PASSWORD),
    ("", ""),
    ("é", "é"),
])
def test_a_wrong_sign_in_is_refused_with_one_message(agent, username, password):
    add_user(agent, "pat", "admin")
    page = agent.client.post("/owner/login", data={"username": username, "password": password})
    assert page.status_code == 401
    assert "That username and password don't match." in page.text


def test_the_sign_in_returns_to_the_page_asked_for(agent):
    add_user(agent, "pat", "admin")
    form = agent.client.get("/owner/results?days=7").text
    assert 'name="next" value="/owner/results?days=7"' in form
    done = agent.client.post(
        "/owner/login",
        data={"username": "pat", "password": PASSWORD, "next": "/owner/results?days=7"},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/owner/results?days=7"


@pytest.mark.parametrize("given", ["https://evil.example/", "//evil.example", "/other", "/owner\\x"])
def test_the_sign_in_never_sends_anyone_off_the_owner_view(agent, given):
    add_user(agent, "pat", "admin")
    done = agent.client.post(
        "/owner/login",
        data={"username": "pat", "password": PASSWORD, "next": given},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/owner"


def test_signing_out_ends_the_session_on_the_server(agent):
    sign_in(agent)
    token = agent.client.cookies.get("owner_session")
    out = agent.client.post("/owner/logout", follow_redirects=False)
    assert out.status_code == 303
    agent.client.cookies.set("owner_session", token)
    assert agent.client.get("/owner").status_code == 401, "the old cookie no longer opens anything"


def test_a_removed_user_is_signed_out(agent):
    admin = add_user(agent, "pat", "admin")
    sign_in(agent, "staff", "sam")
    conn = conn_of(agent)
    try:
        accounts.remove(conn, user_named(agent, "sam")["id"], admin, "Pat", agent.clock())
    finally:
        conn.close()
    assert agent.client.get("/owner").status_code == 401


def test_the_header_names_who_is_signed_in(agent):
    page = sign_in(agent, "staff", "sam").client.get("/owner").text
    assert 'data-tip="Signed in as Sam, staff"' in page and ">Sign out</button>" in page


# ---- the move from tokens to accounts ---------------------------------------------------------


def test_the_settings_no_longer_carry_a_token_or_edit_codes(monkeypatch):
    monkeypatch.setenv("OWNER_TOKEN", "old-token")
    monkeypatch.setenv("RULES_EDITORS", "Dana=old-code")
    found = Settings()
    assert not hasattr(found, "owner_token") and not hasattr(found, "rules_editors")


def test_an_old_token_link_opens_nothing_and_is_dropped(tmp_path, clock, mock, monkeypatch):
    monkeypatch.setenv("OWNER_TOKEN", "old-token")
    agent = Agent(make_server(tmp_path, clock, mock[0]), clock)
    page = agent.client.get("/owner?token=old-token&day=2026-11-01")
    assert page.status_code == 401
    assert 'name="next" value="/owner?day=2026-11-01"' in page.text, "the token isn't carried on"


def test_old_cookies_open_nothing_and_signing_in_clears_them(agent):
    agent.client.cookies.set("owner_token", "old-token")
    agent.client.cookies.set("owner_edit", "old-code")
    assert agent.client.get("/owner").status_code == 401
    assert agent.client.post("/owner/rules/discard").status_code == 401
    add_user(agent, "pat", "admin")
    done = agent.client.post(
        "/owner/login", data={"username": "pat", "password": PASSWORD}, follow_redirects=False
    )
    cleared = " ".join(done.headers.get_list("set-cookie"))
    assert 'owner_token=""' in cleared and 'owner_edit=""' in cleared


def test_the_old_edit_login_route_is_gone(agent):
    assert agent.client.post("/owner/rules/login", data={"token": "x"}).status_code in (404, 405)


def test_an_existing_database_gains_the_account_tables(tmp_path):
    path = tmp_path / "old.db"
    db.init(path)
    conn = db.connect(path)
    for table in ("users", "sessions", "user_changes", "setting_changes", "call_settings",
                  "recording_declines"):
        conn.execute(f"DROP TABLE {table}")
    conn.close()
    db.init(path)
    conn = db.connect(path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"users", "sessions", "setting_changes", "call_settings"} <= names


def test_rule_versions_written_under_edit_codes_keep_their_author(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    page = sign_in(agent, "staff", "sam").client.get("/owner/rules").text
    assert "<td class=\"fit\">Dana</td>" in page


# ---- the seed admin ---------------------------------------------------------------------------


def test_the_seed_admin_is_made_once_from_the_environment(tmp_path, clock, mock):
    first = make_server(tmp_path, clock, mock[0], owner_admin_username="Pat",
                        owner_admin_name="Pat Moreno", owner_admin_password=PASSWORD)
    agent = Agent(first, clock)
    user = user_named(agent, "pat")
    assert (user["name"], user["role"]) == ("Pat Moreno", "admin")
    conn = conn_of(agent)
    try:
        accounts.set_password(conn, user["id"], "changed-since-1", "Pat", clock())
    finally:
        conn.close()
    # A restart with the same environment leaves the changed password alone.
    again = Agent(make_server(tmp_path, clock, mock[0], owner_admin_username="pat",
                              owner_admin_password=PASSWORD), clock)
    refused = again.client.post("/owner/login", data={"username": "pat", "password": PASSWORD})
    assert refused.status_code == 401
    conn = conn_of(again)
    try:
        assert len(accounts.live_users(conn)) == 1
    finally:
        conn.close()


def test_a_server_started_with_no_user_says_how_to_add_one(tmp_path, clock, mock, caplog,
                                                           monkeypatch):
    """A deploy upgraded with only OWNER_TOKEN set would otherwise leave nobody able to sign in."""
    monkeypatch.setenv("OWNER_TOKEN", "old-token")
    with caplog.at_level(logging.ERROR, logger="app.accounts"):
        agent = Agent(make_server(tmp_path, clock, mock[0]), clock)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    for needed in ("OWNER_ADMIN_USERNAME", "OWNER_ADMIN_NAME", "OWNER_ADMIN_PASSWORD",
                   "make owner-user"):
        assert needed in errors[0], needed
    assert agent.client.get("/owner").status_code == 401


def test_a_server_with_a_user_logs_no_error(tmp_path, clock, mock, caplog):
    with caplog.at_level(logging.ERROR, logger="app.accounts"):
        make_server(tmp_path, clock, mock[0], owner_admin_username="pat",
                    owner_admin_password=PASSWORD)
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_no_seed_admin_without_a_password(agent):
    conn = conn_of(agent)
    try:
        assert accounts.seed_admin(conn, "pat", "Pat", "", agent.clock(), environ={}) is None
        assert accounts.live_users(conn) == []
    finally:
        conn.close()


def test_the_old_variables_are_reported_as_no_longer_read(agent, caplog):
    conn = conn_of(agent)
    try:
        with caplog.at_level(logging.WARNING, logger="app.accounts"):
            accounts.seed_admin(conn, "", "", "", agent.clock(),
                                environ={"OWNER_TOKEN": "x", "RULES_EDITORS": "a=b"})
    finally:
        conn.close()
    assert "OWNER_TOKEN is no longer read" in caplog.text
    assert "RULES_EDITORS is no longer read" in caplog.text


# ---- the command line -------------------------------------------------------------------------


def test_the_command_line_adds_lists_and_resets_users(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "cli.db")
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert accounts.main(["--db", path, "add", "Dana", "--name", "Dana Ruiz", "--role", "admin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert accounts.main(["--db", path, "add", "sam", "--name", "Sam"]) == 1
    assert accounts.main(["--db", path, "list"]) == 0
    listed = capsys.readouterr().out
    assert "dana" in listed and "Admin" in listed and "sam" not in listed
    monkeypatch.setattr("sys.stdin", io.StringIO("brand-new-password\n"))
    assert accounts.main(["--db", path, "password", "dana"]) == 0
    assert accounts.main(["--db", path, "password", "nobody"]) == 1
    conn = db.connect(path)
    try:
        assert accounts.authenticate(conn, "dana", "brand-new-password") is not None
    finally:
        conn.close()


# ---- roles on every route ---------------------------------------------------------------------


def _ids(agent: Agent) -> dict[str, int]:
    return {"other": add_user(agent, "lee", "staff")}


WRITES = [
    ("get", "/owner/rules/edit", {}),
    ("post", "/owner/rules/draft", {"guide.booking.0": PETS}),
    ("post", "/owner/rules/discard", {}),
    ("post", "/owner/rules/cancel", {}),
    ("post", "/owner/rules/rollback", {"number": "0"}),
    ("post", "/owner/settings", {"s.two_party_consent": "1"}),
    ("post", "/owner/users", {"username": "eve", "name": "Eve", "role": "admin",
                              "password": PASSWORD}),
    ("post", "/owner/users/{other}/role", {"role": "admin"}),
    ("post", "/owner/users/{other}/remove", {}),
]


def _state(agent: Agent) -> tuple:
    """Everything an admin route could change: rule versions, settings and users."""
    conn = conn_of(agent)
    try:
        return (
            conn.execute("SELECT COUNT(*), MAX(status) FROM rule_versions").fetchone()[:],
            conn.execute("SELECT COUNT(*) FROM setting_changes").fetchone()[0],
            [tuple(r) for r in conn.execute(
                "SELECT username, role, removed_at FROM users ORDER BY id"
            )],
        )
    finally:
        conn.close()


@pytest.mark.parametrize("method, path, form", WRITES)
def test_staff_are_refused_every_change_and_nothing_changes(agent, method, path, form):
    ids = _ids(agent)
    sign_in(agent, "staff", "sam")
    before = _state(agent)
    url = path.format(**ids)
    response = getattr(agent.client, method)(url, **({"data": form} if method == "post" else {}))
    assert response.status_code == 403 and "Admins only" in response.text
    assert _state(agent) == before


@pytest.mark.parametrize("method, path, form", WRITES)
def test_nobody_signed_in_is_sent_to_sign_in_and_nothing_changes(agent, method, path, form):
    ids = _ids(agent)
    before = _state(agent)
    url = path.format(**ids)
    response = getattr(agent.client, method)(url, **({"data": form} if method == "post" else {}))
    assert response.status_code == 401 and 'name="password"' in response.text
    assert _state(agent) == before


@pytest.mark.parametrize("method, path, form", WRITES)
def test_an_admin_is_allowed_every_change(agent, method, path, form):
    ids = _ids(agent)
    sign_in(agent, "admin", "pat")
    url = path.format(**ids)
    response = getattr(agent.client, method)(
        url, follow_redirects=False, **({"data": form} if method == "post" else {})
    )
    # cancel with no check running answers 422 with the reason, still past the role check.
    assert response.status_code in (200, 303, 422), response.text
    assert "Admins only" not in response.text


def test_staff_can_send_a_report_to_revin(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "My AC is out.")
    sign_in(agent, "staff", "sam")
    sent = agent.client.post(f"/owner/calls/{call}/report", data={"text": "It missed the age."},
                             follow_redirects=False)
    assert sent.status_code == 303
    conn = conn_of(agent)
    try:
        assert conn.execute("SELECT COUNT(*) FROM owner_reports").fetchone()[0] == 1
    finally:
        conn.close()


def test_an_admin_publishes_under_their_own_name(agent):
    sign_in(agent, "admin", "pat")
    agent.client.post("/owner/rules/draft", data={"guide.booking.0": PETS, "publish": "1"})
    conn = conn_of(agent)
    try:
        row = rules.open_draft(conn, "summit-air")
    finally:
        conn.close()
    assert row["author"] == "Pat" and row["status"] == "checking"


# ---- the Users panel --------------------------------------------------------------------------


def test_an_admin_adds_a_user_from_the_page(agent):
    sign_in(agent, "admin", "pat")
    done = agent.client.post("/owner/users", data={
        "username": "dana", "name": "Dana Ruiz", "role": "staff", "password": PASSWORD,
    })
    assert done.status_code == 200 and "Dana Ruiz" in done.text and ">Saved</span>" in done.text
    assert user_named(agent, "dana")["role"] == "staff"
    assert "Added Dana Ruiz as staff" in done.text


def test_a_bad_new_user_shows_the_reason_on_its_field_and_keeps_what_was_typed(agent):
    sign_in(agent, "admin", "pat")
    page = agent.client.post("/owner/users", data={
        "username": "Dana Ruiz", "name": "Dana Ruiz", "role": "staff", "password": "short",
    })
    assert page.status_code == 422
    assert "lowercase letters" in page.text and "At least 10 characters" in page.text
    assert 'value="Dana Ruiz"' in page.text
    assert user_named(agent, "dana ruiz") is None


def test_removing_a_user_takes_a_confirm_on_the_row_with_no_dialog(agent):
    other = add_user(agent, "lee", "staff")
    sign_in(agent, "admin", "pat")
    page = agent.client.get("/owner/settings").text
    assert f"remove={other}" in page and ">Confirm</button>" not in page
    asked = agent.client.get(f"/owner/settings?remove={other}").text
    assert f'action="/owner/users/{other}/remove"' in asked and ">Confirm</button>" in asked
    assert "confirm(" not in asked and "alert(" not in asked
    agent.client.post(f"/owner/users/{other}/remove")
    assert user_named(agent, "lee") is None


def test_the_last_admin_shows_no_role_or_remove_control(agent):
    sign_in(agent, "admin", "pat")
    page = agent.client.get("/owner/settings").text
    assert "Make staff" not in page and "remove=" not in page


def test_an_admin_changes_a_role_from_the_page(agent):
    other = add_user(agent, "lee", "staff")
    sign_in(agent, "admin", "pat")
    done = agent.client.post(f"/owner/users/{other}/role", data={"role": "admin"})
    assert done.status_code == 200 and "Made Lee admin" in done.text
    assert user_named(agent, "lee")["role"] == "admin"


def test_staff_see_users_and_settings_without_controls(agent):
    add_user(agent, "pat", "admin")
    page = sign_in(agent, "staff", "sam").client.get("/owner/settings").text
    assert "Pat" in page and "Sam" in page
    assert "Make admin" not in page and ">Add user</button>" not in page
    assert 'type="radio"' not in page and ">Save</button>" not in page
    assert '<span class="tag outline">Off</span>' in page


def test_an_unknown_setting_sent_by_hand_is_refused(agent):
    sign_in(agent, "admin", "pat")
    page = agent.client.post("/owner/settings", data={"s.priority": "0"})
    assert page.status_code == 422 and "No such setting: priority" in page.text
    conn = conn_of(agent)
    try:
        assert conn.execute("SELECT COUNT(*) FROM setting_changes").fetchone()[0] == 0
    finally:
        conn.close()


def test_saving_the_settings_logs_who_changed_what(agent, clock):
    sign_in(agent, "admin", "pat")
    clock.advance(minutes=5)
    page = agent.client.post("/owner/settings", data={
        "s.two_party_consent": "1", "s.call_recordings": "1", "s.warm_transfer": "1",
    }).text
    assert ">Saved</span>" in page and "Two-party consent on" in page
    assert page.count("Call recordings on") == 0, "only what changed is logged"
    conn = conn_of(agent)
    try:
        tenant = agent.app.state.services.tenants.default
        assert owner_settings.current(conn, tenant)["two_party_consent"] is True
        row = owner_settings.history(conn, tenant)[0]
    finally:
        conn.close()
    assert (row["key"], row["author"]) == ("two_party_consent", "Pat")
    assert (clock() - timedelta(minutes=0)).isoformat()[:10] in row["created_at"]
