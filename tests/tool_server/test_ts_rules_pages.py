"""The Rules page on the debug bench (exploration 018, DECISIONS.md 091): the owner's rules, the
fixed responses (app/fixed.py), the system prompt by section, and the rulebook behind them
(app/rulebook.py). The owner view has no rules page; its old address redirects here."""

import re
from html import escape
from html.parser import HTMLParser

import pytest
import yaml
from test_ts_rules import PATH, PETS, TENANT, publish
from toolkit import PASSES, ROOT, Agent, make_server, sign_in

from app import bench_rules, fixed, plain, rulebook, rules
from prompts import assemble

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


# ---- Fixed responses ----------------------------------------------------------------------------


def test_every_line_and_every_server_check_has_a_topic():
    assert {title for _stage, title, _codes, _spec in rulebook.GATES} == set(fixed.GATES)
    assert {topic for topic, _when, _line in fixed.GATES.values()} <= set(fixed.TOPICS)
    keys = [r.id.removeprefix("line.") for r in rulebook.rulebook(TENANT) if r.kind == "wording"]
    assert all(fixed.topic_of(k) in fixed.TOPICS for k in keys)


def test_every_line_is_said_on_exactly_one_row():
    rows = [r for _key, _name, found in fixed.responses(TENANT) for r in found]
    said = [r.line for r in rows if r.line] + [v for r in rows for v in r.variants]
    lines = [r.detail for r in rulebook.rulebook(TENANT) if r.kind == "wording"]
    assert sorted(said) == sorted(lines), "every line is said on exactly one row"
    assert sum(r.line.startswith("So that's [address]") for r in rows) == 1, "the read-back shows once"
    assert all(rows for _key, _name, rows in fixed.responses(TENANT)), "a topic with no rows"


def test_each_line_the_owner_may_reword_is_one_field():
    rows = [r for _key, _name, found in fixed.responses(TENANT) for r in found]
    editable = [r.editable for r in rows if r.editable]
    assert sorted(editable) == sorted(rules.EDITABLE_LINES)


def test_variants_of_one_response_share_a_row():
    rows = {r.when: r for _key, _name, found in fixed.responses(TENANT) for r in found}
    assert len(rows["A lookup takes a moment"].variants) == 2
    # A check that holds the call says its line on its own row, with the line's variants.
    check = rows["A change is asked for from an unknown number"]
    assert check.rule and check.line.startswith("Of course.") and len(check.variants) >= 5
    # The owner may reword a line a check says, in place on the check's row.
    assert rows["Before ending a booked call"].editable == "anything_else_first"


def test_every_row_has_its_own_plain_trigger():
    rows = [r for _key, _name, found in fixed.responses(TENANT) for r in found]
    whens = [r.when for r in rows]
    assert len(whens) == len(set(whens)), "two rows with one trigger"
    titles = {title for _stage, title, _codes, _spec in rulebook.GATES}
    assert not titles & set(whens), "a check shows its trigger, not its rule"
    # A check that says nothing shows what it does beside its trigger.
    held = [r for r in rows if r.rule and not r.line]
    assert held and all(r.rule_text in titles for r in held)


# ---- The page -----------------------------------------------------------------------------------


def test_checks_that_say_nothing_are_listed_once_under_always(agent):
    text = editor(agent).client.get("/bench/rules").text
    safety = text[text.index('id="topic-safety"'):text.index('id="topic-greeting"')]
    assert '<span class="what">Always</span>' in safety
    assert "A safety caller asks for a person" in safety
    assert "Never puts a safety call through to a person" in safety
    assert "Held by the server" not in text


def test_the_owner_view_has_no_rules_page_and_its_address_moved_to_the_bench(agent):
    moved = agent.client.get("/owner/rules?tenant=x", follow_redirects=False)
    assert moved.status_code == 308 and moved.headers["location"] == "/bench/rules?tenant=x"
    # Signed in, staff land on the bench's Admins only page and an admin on the Rules page.
    staff = reader(agent).client.get("/owner/rules", follow_redirects=False)
    assert staff.status_code == 308 and staff.headers["location"] == "/bench/rules"
    landed = agent.client.get("/owner/rules")
    assert landed.status_code == 403 and "Admins only" in landed.text
    admin = editor(agent).client.get("/owner/rules", follow_redirects=False)
    assert admin.status_code == 308 and admin.headers["location"] == "/bench/rules"
    landed = agent.client.get("/owner/rules")
    assert landed.status_code == 200 and "<h2>Your rules</h2>" in landed.text
    page = reader(agent).client.get("/owner").text
    assert "/owner/rules" not in page and ">Rules</a>" not in page
    assert not (ROOT / "app" / "templates" / "rules.html").exists()
    for path in ("/owner/rules/edit", "/owner/rules/draft", "/owner/rules/discard"):
        assert agent.client.get(path).status_code == 404, path


def test_the_page_has_the_three_parts_in_order(agent):
    page = editor(agent).client.get("/bench/rules")
    assert page.status_code == 200
    text = page.text
    assert text.index("<h2>Your rules</h2>") < text.index("<h2>Fixed responses</h2>")
    assert text.index("<h2>Fixed responses</h2>") < text.index("<h2>System prompt</h2>")
    for _key, name, _rows in fixed.responses(TENANT):
        assert f">{escape(name, quote=False).replace(chr(39), '&#39;')}<span class=\"count\">" in text
    # One topic open at a time; the rest wait behind their tab.
    assert text.count('role="tabpanel"') == len(fixed.TOPICS)
    assert len(re.findall(r'role="tabpanel"[^>]* hidden>', text)) == len(fixed.TOPICS) - 1


def test_the_prompt_sections_come_from_assemble_in_join_order(agent):
    publish(agent, {"guidance": {"opening": ["Ask how their day is going."]}})
    text = editor(agent).client.get("/bench/rules").text
    start = bench_rules.sample_call(TENANT, {"guidance": {"opening": ["Ask how their day is going."]}})
    names = [name for name, _text in assemble.sections(start)]
    assert names == ["identity.md", "spine.md", "journeys.md", "house-guidance.md", "tool-use.md",
                     "safety.md", "Company knowledge", "This call"]
    found = [text.index(f'<span class="name">{name}</span>') for name in names]
    assert found == sorted(found)
    assert "Ask how their day is going." in text
    assert "A sample caller, not a real one" in text


def test_the_sample_call_has_the_shape_of_a_real_call_start(agent):
    real = agent.start()["data"]
    sample = bench_rules.sample_call(TENANT, {})
    assert set(sample) == set(real) - {"call_id"}
    for key in ("tenant", "caller", "house_rules"):
        assert set(sample[key]) == set(real[key]), key
    assert sample["knowledge"] == real["knowledge"]
    # Both build the same sections, so the page shows what a call gets.
    assert [n for n, _t in assemble.sections(sample)] == [n for n, _t in assemble.sections(real)]


def test_the_prompt_is_its_sections_joined():
    start = bench_rules.sample_call(TENANT, {"guidance": {"whole_call": [PATH]}})
    parts = assemble.sections(start)
    assert assemble.instructions(start) == "\n\n".join(text for _name, text in parts)
    assert "{company}" not in assemble.instructions(start)


def test_booking_and_closing_rules_are_not_in_the_prompt_and_the_page_says_where_they_go(agent):
    publish(agent, {"guidance": {"closing": [PATH]}})
    text = editor(agent).client.get("/bench/rules").text
    assert "booking and closing rules are told to the agent when the call gets there" in text
    prompt = text[text.index("<h2>System prompt</h2>"):]
    assert PATH not in prompt and "house-guidance.md" not in prompt


def test_the_rules_page_shows_versions_with_their_check(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    page = editor(agent).client.get("/bench/rules").text
    assert "Passed 18 of 18" in page and '<span class="tag done">Live</span>' in page
    assert PETS[:20] in page


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
    page = editor(agent).client.get("/bench/rules").text
    assert "Passed 18 of 18" in page and "1 on a rerun" in page
    assert "Failed, then passed on a rerun: landlord in disguise." in page


# ---- Editing (admins) ---------------------------------------------------------------------------


def test_staff_cannot_open_the_editor(agent):
    page = reader(agent).client.get("/bench/rules")
    assert page.status_code == 403 and "Admins only" in page.text


def test_the_editor_needs_a_sign_in(agent):
    page = agent.client.get("/bench/rules")
    assert page.status_code == 401 and 'name="password"' in page.text


def test_the_editor_has_fields_only_for_the_editable_lines_and_the_rules(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    page = editor(agent).client.get("/bench/rules")
    names = {i.get("name") for i in parse(page.text).inputs}
    lines = {n.removeprefix("line.") for n in names if n and n.startswith("line.")}
    assert lines == set(rules.EDITABLE_LINES)
    assert "line.read_back" not in names
    # The live rule and one blank row to add another.
    assert {n for n in names if n and n.startswith("rule.")} == {"rule.0", "rule.1"}
    assert 'name="when.0"' in page.text and '<option value="booking" selected>' in page.text


def test_a_rule_is_added_with_its_stage_and_the_note_says_what_changed(agent):
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "rule.1": "",
                                    "when.1": "opening"})
    assert response.status_code == 200
    conn = agent.db()
    try:
        draft = rules.open_draft(conn, TENANT.slug)
    finally:
        conn.close()
    assert draft["content"]["guidance"] == {"booking": [PETS]}
    assert draft["note"] == "Added 1 rule"


def test_remove_drops_one_rule_and_keeps_the_rest(agent):
    publish(agent, {"guidance": {"booking": [PETS], "closing": [PATH]}})
    editor(agent).client.post("/bench/rules/draft", data={
        "rule.0": PETS, "when.0": "booking", "rule.1": PATH, "when.1": "closing", "remove": "0"})
    conn = agent.db()
    try:
        draft = rules.open_draft(conn, TENANT.slug)
    finally:
        conn.close()
    assert draft["content"]["guidance"] == {"closing": [PATH]}
    assert draft["note"] == "Removed 1 rule"


def test_the_note_names_a_reworded_line():
    note = rules.describe(TENANT, {"lines": {"goodbye": "Thanks, take care."},
                                   "guidance": {"closing": [PATH]}}, {"guidance": {"closing": [PATH]}})
    assert note == "Reworded Goodbye"
    assert rules.describe(TENANT, {}, None) == "No change"


def test_a_refused_line_opens_its_topic(agent):
    response = editor(agent).client.post("/bench/rules/draft", data={"line.goodbye": "Bye {nope}"})
    assert response.status_code == 422
    text = response.text
    tab = text[text.index('id="tab-closing"') - 80:text.index('id="tab-closing"') + 120]
    assert 'aria-selected="true"' in tab
    assert 'id="topic-closing" aria-labelledby="tab-closing">' in text


def test_staff_cannot_save_a_draft(agent):
    response = reader(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking"})
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
    ("rule.0", GAS, "the agent can&#39;t play it down"),
])
def test_a_forbidden_edit_sent_by_hand_is_refused_on_the_server(agent, field, value, why):
    agent.app.state.services.guidance_check.refuse[GAS] = WHY_GAS
    response = editor(agent).client.post(
        "/bench/rules/draft", data={field: value, "when.0": "closing", "note": "x"})
    assert response.status_code == 422 and why in response.text
    conn = agent.db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_editor_says_when_the_check_could_not_run(agent):
    agent.app.state.services.guidance_check.down = True
    response = editor(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking"})
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
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "note": "Pets", "publish": "1"},
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
    editor(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "publish": "1"})
    assert started == []


def test_override_publishes_at_once_and_records_who_skipped_the_check_and_why(agent, monkeypatch):
    started = []
    monkeypatch.setattr(bench_rules.subprocess, "Popen", lambda args, **kw: started.append(args))
    agent.app.state.services.settings.rules_gate = True
    response = editor(agent).client.post(
        "/bench/rules/draft",
        data={"rule.0": PETS, "when.0": "booking", "note": "Pets", "override": "1", "reason": "Owner asked, call today"},
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
    page = agent.client.get("/bench/rules").text
    assert "Check skipped" in page and "by Dana: Owner asked, call today" in page


def test_override_needs_a_reason_and_saves_the_draft_without_publishing(agent):
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "  "})
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
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "Quick fix"})
    assert response.status_code == 422
    conn = agent.db()
    try:
        assert rules.live(conn, TENANT.slug) is None
    finally:
        conn.close()


def live_number(agent) -> int | None:
    conn = agent.db()
    try:
        found = rules.live(conn, TENANT.slug)
        return found["number"] if found else None
    finally:
        conn.close()


def test_override_is_refused_while_a_check_runs(agent):
    editor(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "publish": "1"})
    response = agent.client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "Quick fix"})
    assert response.status_code == 422
    assert "A check is running on the last draft" in response.text
    assert live_number(agent) is None


@pytest.mark.parametrize("field, value, why", [
    ("line.read_back", "Let me read that back: {address}, {phone}. Right?", "read-back is locked"),
    ("set.priority", "lower", "Priority is locked"),
])
def test_override_cannot_carry_a_locked_rule(agent, field, value, why):
    response = editor(agent).client.post(
        "/bench/rules/draft",
        data={"rule.0": PETS, "when.0": "booking", field: value, "override": "1", "reason": "Quick fix"},
    )
    assert response.status_code == 422 and why in response.text
    assert live_number(agent) is None


def test_override_of_a_draft_that_matches_live_is_refused(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "Again"})
    assert response.status_code == 422 and "the same as what&#39;s live" in response.text
    assert live_number(agent) == 1


def test_an_override_that_cannot_be_logged_publishes_nothing(agent, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bench_rules.logs, "write", broken)
    response = editor(agent).client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "Quick fix"})
    assert response.status_code == 503 and "nothing was published" in response.text
    assert live_number(agent) is None
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug)["status"] == "draft"
    finally:
        conn.close()


def test_staff_cannot_override(agent):
    response = reader(agent).client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "x"})
    assert response.status_code == 403


def test_a_rollback_to_an_overridden_version_keeps_its_record(agent):
    editor(agent).client.post(
        "/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "override": "1", "reason": "Quick fix"})
    agent.clock.advance(minutes=5)
    publish(agent, {"guidance": {"closing": [PATH]}})
    agent.client.post("/bench/rules/rollback", data={"number": "1"})
    conn = agent.db()
    try:
        live = rules.live(conn, TENANT.slug)
        assert live["rollback_of"] == 1 and live["gate"]["overridden"] is True
    finally:
        conn.close()
    row = next(r for r in agent.client.get("/bench/rules").text.split("<tr>")
               if f'<span class="what">{live["number"]}</span>' in r)
    assert "Rolled back" in row and "Check skipped" in row


def test_enter_in_a_field_saves_the_draft_and_never_discards(agent):
    editor(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking"})
    page = agent.client.get("/bench/rules").text
    head = page[page.index('<div class="actions">'):page.index("</div>", page.index('<div class="pop-body">'))]
    first = head.index('<button class="btn" type="submit">Save draft</button>')
    assert first < head.index(">Discard</button>") and first < head.index(">Publish now</button>")


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
    editor(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking", "publish": "1"})
    page = agent.client.get("/bench/rules").text
    assert ">Stop check</button>" in page
    agent.client.post("/bench/rules/cancel")
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug)["status"] == "draft"
    finally:
        conn.close()


def test_discard_drops_the_draft(agent):
    editor(agent).client.post("/bench/rules/draft", data={"rule.0": PETS, "when.0": "booking"})
    agent.client.post("/bench/rules/discard")
    conn = agent.db()
    try:
        assert rules.open_draft(conn, TENANT.slug) is None
    finally:
        conn.close()


# ---- Design system ------------------------------------------------------------------------------


def test_the_rules_page_uses_only_design_system_classes(agent):
    publish(agent, {"guidance": {"booking": [PETS]}})
    page = editor(agent).client.get("/bench/rules").text
    tags = parse(page)
    undefined = {c for c in tags.classes if not re.search(rf"\.{re.escape(c)}\b", CSS)}
    assert not undefined, sorted(undefined)


@pytest.mark.parametrize("name", ["bench_rules.html", "login.html"])
def test_the_rules_templates_set_no_style_of_their_own(name):
    text = (ROOT / "app" / "templates" / name).read_text()
    assert "style=" not in text and "<style" not in text


def test_the_new_field_is_on_the_specimen_page(agent):
    page = reader(agent).client.get("/owner/design").text
    assert 'class="field"' in page and 'aria-invalid="true"' in page
