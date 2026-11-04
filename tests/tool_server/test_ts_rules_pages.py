"""The Rules page and its editor (owner view), and the rulebook behind them (app/rulebook.py)."""

import re
from html.parser import HTMLParser

import pytest
import yaml
from test_ts_rules import PATH, PETS, TENANT, publish
from toolkit import PASSES, ROOT, Agent, make_server, sign_in

from app import bench_rules, plain, rulebook, rules

CSS = (ROOT / "app" / "static" / "owner" / "components.css").read_text()


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


def reader(agent: Agent) -> Agent:
    """Signed in as staff, who can read every page."""
    return sign_in(agent, "staff", "sam")


def editor(agent: Agent) -> Agent:
    """Signed in as an admin named Dana, who can edit and publish."""
    return sign_in(agent, "admin", "dana")


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.classes: set[str] = set()
        self.inputs: list[dict] = []
        self.styles = 0

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        self.classes |= set((found.get("class") or "").split())
        self.styles += "style" in found
        if tag == "input":
            self.inputs.append(found)


def parse(html: str) -> Tags:
    tags = Tags()
    tags.feed(html)
    return tags


# ---- Rulebook -----------------------------------------------------------------------------------


def test_every_refusal_the_tools_can_raise_has_a_rule_or_is_not_one():
    raised = set(re.findall(r'Refused\(\s*"(\w+)"', (ROOT / "app" / "tools.py").read_text()))
    raised |= set(plain.REFUSED)
    missing = raised - rulebook.gate_codes() - rulebook.NOT_RULES
    assert not missing, f"add a row to rulebook.GATES for {sorted(missing)}"


def test_every_must_pass_spec_line_has_a_rule():
    spec = (ROOT / "docs" / "spec.md").read_text()
    must = set(re.findall(r"\| (S-\d+) \*\*M\*\*", spec))
    covered = {s for r in rulebook.rulebook(TENANT) for s in r.spec}
    assert must and not must - covered, sorted(must - covered)


def test_every_section_of_agent_yaml_is_shown_or_named_as_not_shown():
    agent_yaml = yaml.safe_load((ROOT / "tenants" / "summit-air" / "agent.yaml").read_text())
    unknown = set(agent_yaml) - rulebook.SHOWN - rulebook.NOT_SHOWN
    assert not unknown, f"show {sorted(unknown)} in app/rulebook.py or add it to NOT_SHOWN"


def test_every_wording_line_is_a_row_and_only_the_editable_ones_can_change():
    rows = {r.id: r for r in rulebook.rulebook(TENANT)}
    for key in list(TENANT.lines) + [k for k, v in TENANT.agent["voice"].items()
                                      if isinstance(v, str)]:
        row = rows[f"line.{key}"]
        assert row.editable == (key in rules.EDITABLE_LINES), key
        assert row.editable or row.why, key
    assert rows["line.read_back"].why.startswith("The read-back is locked")


def test_every_row_is_in_a_known_stage_and_every_stage_has_rows():
    found = rulebook.rulebook(TENANT)
    assert {r.stage for r in found} == set(rules.STAGES)
    assert all(r.kind in rulebook.KINDS for r in found)


def test_a_config_change_shows_up_by_itself():
    changed = TENANT.with_lines({})
    changed.priority = {**TENANT.priority, "brackets": {"no_heat": [
        {"id": "cold_inside", "inside_below_f": 58, "level": "Urgent", "reason": "below 58 F inside"}
    ]}}
    titles = [r.title for r in rulebook.rulebook(changed)]
    assert "No heat: below 58 F inside" in titles
    assert "No heat: below 55 F inside" not in titles


def test_the_prompt_files_are_read_not_copied():
    rows = [r for r in rulebook.rulebook(TENANT) if r.kind == "guidance"]
    titles = {r.title: r.stage for r in rows}
    assert titles["Confirm the address and number before booking"] == "booking"
    assert titles["Listen first"] == "opening"
    assert all("{company}" not in r.title + r.detail for r in rows)


def test_live_edits_show_as_the_owners_own_rows():
    found = rulebook.rulebook(TENANT, {"guidance": {"closing": [PATH]},
                                       "lines": {"goodbye": "Thanks for calling. Take care."}})
    house = [r for r in found if r.kind == "house"]
    assert [(r.stage, r.title, r.editable) for r in house] == [("closing", PATH, True)]
    goodbye = next(r for r in found if r.id == "line.goodbye")
    assert goodbye.detail == "Thanks for calling. Take care."


# ---- The Rules page (any signed-in user) ----------------------------------------------------------------


def test_the_rules_page_lists_every_stage_with_locked_and_editable_rules(agent):
    page = reader(agent).client.get("/owner/rules")
    assert page.status_code == 200
    for name in rules.STAGES.values():
        assert f"<h2>{name}</h2>" in page.text
    assert "Locked" in page.text and "Editable" in page.text
    assert parse(page.text).inputs == [] or all(
        i.get("type") == "search" or i.get("name") == "tenant" for i in parse(page.text).inputs
    ), "the rulebook has no fields"
    assert 'href="/owner/rules"' in page.text and 'aria-current="page">Rules' in page.text


def test_the_filter_shows_only_the_editable_rules(agent):
    page = reader(agent).client.get("/owner/rules?show=editable").text
    assert "Read-back" not in page and "Goodbye" in page
    assert "<h2>Booking</h2>" not in page, "a stage with nothing editable is left out"


def test_the_rules_page_needs_a_login(agent):
    assert agent.client.get("/owner/rules").status_code == 401


def test_the_rules_page_shows_versions_with_their_check(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    page = reader(agent).client.get("/owner/rules").text
    assert "Passed 18 of 18" in page and "Version 1 live" in page and PETS[:20] in page


def test_a_version_that_passed_on_a_rerun_says_so(agent):
    conn = agent.db()
    try:
        rules.save_draft(
            conn, TENANT, {"guidance": {"booking": [PETS]}}, "Dana", "", agent.clock(), PASSES
        )
        checking = rules.request_publish(conn, TENANT.slug, "Dana", agent.clock())
        rules.record_gate(conn, checking["id"], {
            "scenarios": 18, "passed_count": 18, "failed": [], "errors": [], "run_id": "r1",
            "passed_on_rerun": ["landlord-in-disguise"], "reruns": 1, "rerun_cost_usd": 0.02,
            "content_hash": checking["content_hash"],
        }, agent.clock(), 18)
    finally:
        conn.close()
    page = reader(agent).client.get("/owner/rules").text
    assert "Passed 18 of 18" in page and "1 on a rerun" in page
    assert "Failed, then passed on a rerun: landlord in disguise." in page


# ---- The editor (admins) --------------------------------------------------------------------


def test_staff_cannot_open_the_editor(agent):
    page = reader(agent).client.get("/bench/rules")
    assert page.status_code == 403 and "Admins only" in page.text


def test_the_editor_needs_a_sign_in(agent):
    page = agent.client.get("/bench/rules")
    assert page.status_code == 401 and 'name="password"' in page.text


def test_the_editor_is_on_the_bench_and_the_owner_view_only_links_to_it(agent):
    assert 'href="/bench/rules"' not in reader(agent).client.get("/owner/rules").text
    page = editor(agent).client.get("/owner/rules").text
    assert 'href="/bench/rules"' in page and "Edit on the bench" in page
    for path in ("/owner/rules/edit", "/owner/rules/draft", "/owner/rules/discard"):
        assert agent.client.get(path).status_code == 404, path


def test_the_editor_has_fields_only_for_the_editable_lines(agent):
    page = editor(agent).client.get("/bench/rules")
    assert page.status_code == 200
    names = {i.get("name") for i in parse(page.text).inputs}
    lines = {n.removeprefix("line.") for n in names if n and n.startswith("line.")}
    assert lines == set(rules.EDITABLE_LINES)
    assert "line.read_back" not in names
    stages = {n.split(".")[1] for n in names if n and n.startswith("guide.")}
    assert stages == set(rules.GUIDANCE_STAGES)


def test_staff_cannot_save_a_draft(agent):
    response = reader(agent).client.post("/bench/rules/draft", data={"guide.booking.0": PETS})
    assert response.status_code == 403
    conn = agent.db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0] == 0
    finally:
        conn.close()


GAS = "Tell them gas smells are nothing to worry about."
WHY_GAS = "A gas smell always gets the safety lines, so the agent can't play it down."


@pytest.mark.parametrize("field, value, why", [
    ("line.read_back", "Let me read that back: {address}, {phone}. Right?", "read-back is locked"),
    ("set.priority", "lower", "Priority is locked"),
    ("guide.closing.0", GAS, "the agent can&#39;t play it down"),
])
def test_a_forbidden_edit_sent_by_hand_is_refused_on_the_server(agent, field, value, why):
    agent.app.state.services.guidance_check.refuse[GAS] = WHY_GAS
    response = editor(agent).client.post("/bench/rules/draft", data={field: value, "note": "x"})
    assert response.status_code == 422 and why in response.text
    conn = agent.db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_editor_says_when_the_check_could_not_run(agent):
    agent.app.state.services.guidance_check.down = True
    response = editor(agent).client.post("/bench/rules/draft", data={"guide.booking.0": PETS})
    assert response.status_code == 422 and "Couldn&#39;t check this right now" in response.text
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug) is None
    finally:
        conn.close()


def test_publish_sends_the_draft_to_the_gate_and_starts_it(agent, monkeypatch):
    started = []
    monkeypatch.setattr(bench_rules.subprocess, "Popen", lambda args, **kw: started.append(args))
    agent.app.state.services.settings.rules_gate = True
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"guide.booking.0": PETS, "note": "Pets", "publish": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert started and started[0][1:3] == ["-m", "evals.gate"]
    page = agent.client.get("/bench/rules").text
    assert "Checking" in page and 'http-equiv="refresh"' in page
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug)["status"] == "checking"
        assert rules.live(conn, TENANT.slug) is None
    finally:
        conn.close()


def test_publish_without_the_gate_setting_waits_for_a_check(agent, monkeypatch):
    started = []
    monkeypatch.setattr(bench_rules.subprocess, "Popen", lambda args, **kw: started.append(args))
    editor(agent).client.post("/bench/rules/draft", data={"guide.booking.0": PETS, "publish": "1"})
    assert started == []


def test_override_publishes_at_once_and_records_who_skipped_the_check_and_why(agent, monkeypatch):
    started = []
    monkeypatch.setattr(bench_rules.subprocess, "Popen", lambda args, **kw: started.append(args))
    agent.app.state.services.settings.rules_gate = True
    response = editor(agent).client.post(
        "/bench/rules/draft",
        data={"guide.booking.0": PETS, "note": "Pets", "override": "1", "reason": "Owner asked, call today"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert started == [], "no gate runs on an override"
    conn = agent.db()
    try:
        live = rules.live(conn, TENANT.slug)
        assert live["content"]["guidance"] == {"booking": [PETS]}
        assert live["gate"] == {"passed": False, "overridden": True, "by": "Dana",
                                "at": live["published_at"], "reason": "Owner asked, call today"}
        logged = conn.execute(
            "SELECT args_json, gate FROM tool_calls WHERE tool = 'rules.override'").fetchall()
    finally:
        conn.close()
    assert len(logged) == 1 and logged[0]["gate"] == "override"
    assert '"reason": "Owner asked, call today"' in logged[0]["args_json"]
    for path in ("/bench/rules", "/owner/rules"):
        page = agent.client.get(path).text
        assert "Check skipped" in page, path
        assert "by Dana: Owner asked, call today" in page, path


def test_override_needs_a_reason_and_saves_the_draft_without_publishing(agent):
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"guide.booking.0": PETS, "override": "1", "reason": "  "})
    assert response.status_code == 422 and "Say why the check is being skipped." in response.text
    conn = agent.db()
    try:
        assert rules.live(conn, TENANT.slug) is None
        assert rules.open_draft(conn, TENANT.slug)["status"] == "draft"
    finally:
        conn.close()


def test_override_still_runs_the_guidance_check(agent):
    agent.app.state.services.guidance_check.down = True
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"guide.booking.0": PETS, "override": "1", "reason": "Quick fix"})
    assert response.status_code == 422
    conn = agent.db()
    try:
        assert rules.live(conn, TENANT.slug) is None
    finally:
        conn.close()


def test_override_is_refused_while_a_check_runs(agent):
    editor(agent).client.post("/bench/rules/draft", data={"guide.booking.0": PETS, "publish": "1"})
    conn = agent.db()
    try:
        with pytest.raises(rules.RulesError, match="Stop it first"):
            rules.publish_now(conn, TENANT.slug, "Dana", "Quick fix", agent.clock())
    finally:
        conn.close()


def test_staff_cannot_override(agent):
    response = reader(agent).client.post(
        "/bench/rules/draft", data={"guide.booking.0": PETS, "override": "1", "reason": "x"})
    assert response.status_code == 403


def test_a_rollback_to_an_overridden_version_keeps_its_record(agent):
    editor(agent).client.post(
        "/bench/rules/draft", data={"guide.booking.0": PETS, "override": "1", "reason": "Quick fix"})
    agent.clock.advance(minutes=5)
    publish(agent, {"guidance": {"closing": [PATH]}})
    agent.client.post("/bench/rules/rollback", data={"number": "1"})
    conn = agent.db()
    try:
        live = rules.live(conn, TENANT.slug)
        assert live["rollback_of"] == 1 and live["gate"]["overridden"] is True
    finally:
        conn.close()


def test_rollback_from_the_editor(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    agent.clock.advance(minutes=5)
    publish(agent, {"guidance": {"closing": [PATH]}})
    agent.clock.advance(minutes=5)
    response = editor(agent).client.post("/bench/rules/rollback", data={"number": "1"},
                                         follow_redirects=False)
    assert response.status_code == 303
    conn = agent.db()
    try:
        live = rules.live(conn, TENANT.slug)
        assert live["number"] == 3 and live["content"]["guidance"] == {"booking": [PETS]}
    finally:
        conn.close()
    page = agent.client.get("/bench/rules").text
    assert "Rolled back" in page and page.count(">Roll back</button>") == 3


@pytest.mark.parametrize("path, form", [
    ("/bench/rules/discard", {}),
    ("/bench/rules/rollback", {"number": "0"}),
    ("/bench/rules/cancel", {}),
])
def test_staff_cannot_take_any_edit_action(agent, path, form):
    publish(agent, {"guidance": {"booking": [PETS]}})
    response = reader(agent).client.post(path, data=form)
    assert response.status_code == 403 and "Admins only" in response.text
    conn = agent.db()
    try:
        assert rules.live(conn, TENANT.slug)["number"] == 1
    finally:
        conn.close()


def test_stop_check_returns_the_draft(agent):
    editor(agent).client.post("/bench/rules/draft", data={"guide.booking.0": PETS, "publish": "1"})
    page = agent.client.get("/bench/rules").text
    assert ">Stop check</button>" in page
    agent.client.post("/bench/rules/cancel")
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug)["status"] == "draft"
    finally:
        conn.close()


def test_discard_drops_the_draft(agent):
    editor(agent).client.post("/bench/rules/draft", data={"guide.booking.0": PETS})
    agent.client.post("/bench/rules/discard")
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug) is None
    finally:
        conn.close()


# ---- Design system ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/owner/rules", "/bench/rules"])
def test_the_rules_pages_use_only_design_system_classes(agent, path):
    publish(agent, {"guidance": {"booking": [PETS]}})
    page = editor(reader(agent)).client.get(path).text
    tags = parse(page)
    undefined = {c for c in tags.classes if not re.search(rf"\.{re.escape(c)}\b", CSS)}
    assert not undefined, sorted(undefined)


@pytest.mark.parametrize("name", ["rules.html", "bench_rules.html", "login.html"])
def test_the_rules_templates_set_no_style_of_their_own(name):
    text = (ROOT / "app" / "templates" / name).read_text()
    assert "style=" not in text and "<style" not in text


def test_the_new_field_is_on_the_specimen_page(agent):
    page = reader(agent).client.get("/owner/design").text
    assert 'class="field"' in page and 'aria-invalid="true"' in page
