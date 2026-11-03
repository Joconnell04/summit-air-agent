"""The redesigned owner view (brief 07, DECISIONS.md 014 to 016): the dispatch board, the status
line, the summary sentence, the plain notes on a call, the report to Revin, and Results."""

from datetime import date, timedelta

import httpx
import pytest
from test_ts_owner import TOKEN, booked_tune_up, caller, commercial, out_of_area, page
from test_ts_safety import GAS_LINES
from toolkit import (
    ROOT,
    Agent,
    book,
    count,
    first_open,
    in_area_address,
    make_server,
    ready_to_book,
)

from app import jobs, owner, plain, reports, trace

TODAY = date(2026, 11, 2)


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0], owner_token=TOKEN), clock)


def signed_in(agent: Agent) -> Agent:
    agent.client.get(f"/owner?token={TOKEN}")
    return agent


def gas_call_that_booked(agent: Agent, n: int) -> str:
    call = ready_to_book(agent, caller(n))
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    book(agent, call, slot)
    agent.say(call, GAS_LINES)
    agent.turn(call, "We're outside. Please just book a check.")
    assert book(agent, call, slot, job_type="safety_check")["ok"]
    agent.end(call)
    return call


def tenant(agent: Agent):
    return agent.app.state.services.tenants.default


def test_a_minute_of_the_day_is_placed_on_the_board_and_kept_on_it():
    assert owner.position(7 * 60, 7 * 60, 21 * 60) == 0.0
    assert owner.position(14 * 60, 7 * 60, 21 * 60) == 50.0
    assert owner.position(21 * 60, 7 * 60, 21 * 60) == 100.0
    assert owner.position(5 * 60, 7 * 60, 21 * 60) == 0.0, "before the start sits on the edge"
    assert owner.position(23 * 60, 7 * 60, 21 * 60) == 100.0


def test_the_board_puts_flags_jobs_and_other_calls_in_their_lanes(agent, clock):
    booked = booked_tune_up(agent, 1)  # 9:00 a.m.
    clock.advance(minutes=30)
    asked = agent.start(caller(2))["data"]["call_id"]
    agent.tool("flag_person", asked, reason="Billing question", callback_in_minutes=60)
    clock.advance(minutes=30)
    away = out_of_area(agent, 3, "10001")
    b = page(agent)["board"]
    assert [m["call_id"] for m in b["lanes"]["jobs"]] == [booked]
    assert [(m["call_id"], m["label"], m["tone"]) for m in b["lanes"]["needs"]] == [
        (asked, "Billing question", "normal")
    ]
    assert [m["call_id"] for m in b["lanes"]["others"]] == [away], "a flagged call is not 'other'"
    assert b["lanes"]["jobs"][0]["minute"] == 9 * 60
    assert (b["start"], b["end"]) == (7 * 60, 21 * 60)
    assert b["now"] == 10 * 60 and b["open"] == 1


def test_a_safety_call_that_booked_is_both_a_flag_and_a_job(agent):
    call = gas_call_that_booked(agent, 1)
    b = page(agent)["board"]
    assert [m["label"] for m in b["lanes"]["needs"]] == ["Gas smell"]
    assert b["lanes"]["needs"][0]["tone"] == "urgent"
    assert [m["call_id"] for m in b["lanes"]["jobs"]] == [call]


def test_a_handled_flag_stays_on_the_board_marked_handled(agent):
    call = agent.start(caller(1))["data"]["call_id"]
    agent.tool("flag_person", call, reason="Wants a manager", callback_in_minutes=60)
    conn = agent.db()
    conn.execute("UPDATE flags SET status = 'done'")
    conn.close()
    b = page(agent)["board"]
    assert b["lanes"]["needs"][0]["handled"] is True and b["open"] == 0


def test_the_board_widens_for_a_call_outside_its_hours(agent, clock):
    clock.advance(hours=13)  # 10 p.m. New York
    agent.start(caller(1))
    b = page(agent)["board"]
    assert (b["start"], b["end"]) == (7 * 60, 23 * 60)


def test_arrival_windows_count_the_jobs_landing_in_them(agent, clock):
    call = ready_to_book(agent, caller(1))
    afternoon = {"date": "2026-11-02", "window": "afternoon"}
    assert book(agent, call, afternoon, job_type="no_heat")["ok"]
    later = ready_to_book(agent, caller(2))
    assert book(agent, later, {"date": "2026-11-03", "window": "morning"})["ok"]
    b = page(agent)["board"]
    by_name = {w["name"]: w for w in b["windows"]}
    assert by_name["12 to 4"]["jobs"] == 1 and by_name["8 to 12"]["jobs"] == 0
    assert b["scheduled"] == 1 and b["later"] == 1
    assert by_name["8 to 12"]["past"] is False, "9 a.m. is inside the morning window"
    clock.advance(hours=4)
    assert {w["name"]: w["past"] for w in page(agent)["board"]["windows"]}["8 to 12"] is True


def change(agent: Agent, booked_call: str, kind: str, window_start: str | None = None) -> None:
    """Record a cancel or reschedule of the call's job the way the tools do: its own done row."""
    conn = agent.db()
    job = conn.execute(
        "SELECT fsm_job_id FROM bookings WHERE call_id = ? AND kind = 'book'", (booked_call,)
    ).fetchone()["fsm_job_id"]
    conn.execute(
        "INSERT INTO bookings (call_id, kind, status, fsm_key, request_json, fsm_job_id, "
        "window_start, created_at, updated_at) VALUES (?, ?, 'done', ?, '{}', ?, ?, '', '')",
        (booked_call, kind, f"{kind}-{booked_call}", job, window_start),
    )
    conn.close()


def test_a_cancelled_job_leaves_its_window_and_a_moved_one_follows_its_new_window(agent):
    kept = ready_to_book(agent, caller(1))
    assert book(agent, kept, {"date": "2026-11-02", "window": "afternoon"}, job_type="no_heat")[
        "ok"
    ]
    gone = ready_to_book(agent, caller(2))
    assert book(agent, gone, {"date": "2026-11-02", "window": "afternoon"})["ok"]
    moved = ready_to_book(agent, caller(3))
    assert book(agent, moved, {"date": "2026-11-03", "window": "morning"})["ok"]
    change(agent, gone, "cancel")
    change(agent, moved, "reschedule", "2026-11-02T21:00:00Z")  # 4 p.m. New York, today
    by_name = {w["name"]: w["jobs"] for w in page(agent)["board"]["windows"]}
    assert by_name["12 to 4"] == 1, "the cancelled job is gone"
    assert by_name["4 to 8"] == 1, "the moved job counts in its new window"
    assert page(agent)["board"]["scheduled"] == 2


def test_the_status_line_reads_the_voice_workers_heartbeats(agent, clock):
    s = agent.app.state.services
    booked_tune_up(agent, 1)

    def status():
        conn = s.connect()
        try:
            return owner.status(conn, s.tenants.default, clock(), 120)
        finally:
            conn.close()

    assert status() == {"state": "unknown", "last_call": "9 AM"}, "no worker has ever reported"
    agent.client.post("/healthz/heartbeat", json={"worker": "livekit"})
    assert status()["state"] == "ok"
    clock.advance(minutes=5)
    assert status() == {"state": "down", "last_call": "9 AM", "since": "9 AM"}


def test_the_summary_sentence_counts_what_matters_and_flags_what_is_late(agent):
    booked_tune_up(agent, 1)
    gas_call_that_booked(agent, 2)
    parts = page(agent)["summary"]
    assert parts[0] == ("2 calls came in. ", "")
    assert ("2 jobs booked.", "b") in parts
    assert (" 1 safety call handled.", "") in parts
    assert (" 1 needs a person.", "alarm") in parts


def test_an_empty_day_says_so(agent):
    assert page(agent)["summary"] == [("No calls yet.", "")]


def event(tool, ok=True, refused=None, gate=None, **args):
    return {"tool": tool, "ok": ok, "refused": refused, "gate": gate, "args": args}


def test_each_tool_call_reads_as_a_plain_note(agent):
    t = tenant(agent)
    note = lambda e: plain.tool_note(e, t)
    assert (
        note(event("check_service_area", zip="11231"))["text"]
        == "ZIP 11231 is in the service area."
    )
    assert note(event("check_service_area", ok=False, refused="out_of_area", zip="10001")) == {
        "text": "ZIP 10001 is outside the service area, so it can't be booked.",
        "tone": "warn",
    }
    held = note(event("book_appointment", ok=False, refused="gate_safety"))
    assert held == {"text": "Held the booking until the safety lines were said.", "tone": "hold"}
    booked = note(event("book_appointment", job_type="no_heat", slot={"window": "morning"}))
    assert booked["text"] == "Booked in ServiceTitan: No heat, 8 to 12."
    override = note(
        event("book_appointment", gate="safety_override", job_type="safety_check", slot={})
    )
    assert override["tone"] == "warn" and "safety override" in override["text"]
    assert note(event("record_lead", kind="install"))["text"] == "Saved an install lead for sales."
    assert note(event("calls.start")) is None and note(event("end_call")) is None
    assert note(event("end_call", ok=False, refused="anything_else_first")) is None
    assert note(event("book_appointment", ok=False, refused="new_code"))["tone"] == "hold"


def test_a_safety_call_reads_as_a_thread_with_the_lines_noted(agent):
    call = gas_call_that_booked(agent, 1)
    conn = agent.db()
    thread = plain.conversation(trace.timeline(conn, call), tenant(agent))
    conn.close()
    notes = [i["text"] for i in thread if i["kind"] == "note"]
    assert "Heard gas smell. Booking is locked until the safety lines are said." in notes
    assert "Said the signed-off safety lines, word for word." in notes
    assert "Held the booking until the safety lines were said." in notes
    turns = [i for i in thread if i["kind"] == "turn"]
    assert turns[0]["at"] == "0:00" and turns[0]["seq"] == 1
    assert all(i["tech"] is None or i["tech"]["tool"] for i in thread if i["kind"] == "note")


def test_the_call_page_places_the_call_in_its_day_and_sums_it_up(agent, clock):
    first = booked_tune_up(agent, 1)
    clock.advance(minutes=20)
    gas = gas_call_that_booked(agent, 2)
    s = agent.app.state.services
    conn = s.connect()
    p = owner.call_page(conn, tenant(agent), trace.timeline(conn, gas), clock())
    conn.close()
    assert (p["position"], p["of"], p["previous"]["id"], p["next"]) == (2, 2, first, None)
    assert p["summary"].startswith("Safety call: gas smell. The agent said the safety lines first.")
    assert "logged a safety override" in p["summary"]
    assert p["name"] == "Pat Caller" and p["address"]["zip"] == "11231"
    assert p["job_type"] == "Safety check" and p["safety_label"] == "Gas smell"


def test_a_report_is_stored_against_the_call_and_shown_on_it(agent):
    call = booked_tune_up(agent, 1)
    signed_in(agent)
    sent = agent.client.post(
        f"/owner/calls/{call}/report",
        data={"text": "It should have asked about pets.", "turn": "2"},
        follow_redirects=False,
    )
    assert sent.status_code == 303 and sent.headers["location"].endswith("?sent=1#report")
    conn = agent.db()
    stored = reports.for_call(conn, call)
    conn.close()
    assert [(r["text"], r["turn_seq"], r["status"]) for r in stored] == [
        ("It should have asked about pets.", 2, "held")
    ]
    html = agent.client.get(f"/owner/calls/{call}?sent=1").text
    assert "It should have asked about pets." in html and "Held here" in html


def test_a_report_needs_the_token_and_some_words(agent):
    call = booked_tune_up(agent, 1)
    assert agent.client.post(f"/owner/calls/{call}/report", data={"text": "x"}).status_code == 401
    signed_in(agent)
    agent.client.post(f"/owner/calls/{call}/report", data={"text": "   "})
    assert count(agent, "SELECT COUNT(*) FROM owner_reports") == 0
    assert agent.client.post("/owner/calls/nope/report", data={"text": "x"}).status_code == 404


def test_held_reports_are_filed_as_issues_with_the_replay_command(agent, clock):
    call = booked_tune_up(agent, 1)
    posted = []

    def post(title, body):
        posted.append((title, body))
        return 41

    conn = agent.db()
    reports.add(conn, call, 1, "It booked without asking who is home.", clock())
    t = tenant(agent)
    assert reports.file_held(conn, t, None, clock()) == {"filed": 0, "failed": 0, "held": 1}
    assert reports.file_held(conn, t, post, clock())["filed"] == 1
    assert reports.file_held(conn, t, post, clock())["filed"] == 0, "filed once only"
    row = reports.for_call(conn, call)[0]
    conn.close()
    assert (row["status"], row["issue_number"]) == ("filed", 41)
    title, body = posted[0]
    assert title.startswith(f"{t.name} owner report: It booked without asking")
    assert f"make replay CALL={call}" in body and "Hi, the heat's out in my house." in body


def test_a_filing_that_fails_is_tried_again_then_left(agent, clock):
    call = booked_tune_up(agent, 1)

    def broken(title, body):
        raise httpx.ConnectError("down")

    conn = agent.db()
    reports.add(conn, call, None, "Wrong window.", clock())
    t = tenant(agent)
    for _ in range(4):
        reports.file_held(conn, t, broken, clock())
    row = reports.for_call(conn, call)[0]
    conn.close()
    assert (row["status"], row["attempts"]) == ("failed", 3)
    assert "ConnectError" in row["error"]


def test_reports_are_held_without_a_github_token(agent):
    assert agent.app.state.services.report_poster is None
    assert "reports" in jobs.run_once(agent.app.state.services)["summit-air"]


def test_the_github_poster_opens_an_issue_with_the_label(tmp_path):
    from app.settings import Settings

    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["json"] = request.read().decode()
        return httpx.Response(201, json={"number": 7})

    settings = Settings(
        db_path=tmp_path / "x.db", reports_github_token="t0k", reports_github_repo="o/r"
    )
    post = reports.github_poster(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    assert post("Title", "Body") == 7
    assert seen["url"] == "https://api.github.com/repos/o/r/issues"
    assert seen["auth"] == "Bearer t0k" and "owner-report" in seen["json"]


def results(agent: Agent, days: int, day: date = TODAY) -> dict:
    s = agent.app.state.services
    conn = s.connect()
    try:
        return owner.results(conn, s.tenants.default, day, days, s.clock())
    finally:
        conn.close()


def test_results_measure_a_period_against_the_one_before(agent, clock):
    booked_tune_up(agent, 1)
    clock.advance(days=1)
    booked_tune_up(agent, 2)
    commercial(agent, 3)
    r = results(agent, 1, TODAY + timedelta(days=1))
    assert (r["now"]["calls"], r["now"]["booked"], r["before"]["calls"]) == (2, 1, 1)
    assert r["now"]["rate"] == 1.0 and r["now"]["leads"] == 1
    week = results(agent, 7, TODAY + timedelta(days=1))
    assert week["now"]["calls"] == 3 and week["before"]["calls"] is None
    rows = {row["label"]: row for row in owner.ledger(r["now"], r["before"])}
    assert rows["Calls answered"]["now"] == "2" and rows["Calls answered"]["delta"] == "+1"
    assert rows["Booking rate"]["now"] == "100%" and rows["Booking rate"]["delta"] == "+0 pts"
    week_rows = {row["label"]: row for row in owner.ledger(week["now"], week["before"])}
    assert (
        week_rows["Calls answered"]["before"] is None
        and week_rows["Calls answered"]["delta"] is None
    )


def test_results_list_every_inbound_call_by_hour_and_what_was_turned_away(agent, clock):
    booked_tune_up(agent, 1)
    clock.advance(hours=2)
    out_of_area(agent, 2, "10001")
    gas_call_that_booked(agent, 3)
    r = results(agent, 1)
    assert sorted((h["hour"], h["type"], h["booked"]) for h in r["hours"]) == [
        (9, "tune_up", True),
        (11, "out_of_area", False),
        (11, "safety", True),
    ]
    assert r["zips"] == [{"zip": "10001", "calls": 1}]
    assert r["held"] == {"calls": 1, "lines_first": 1, "booked": 1}
    rows = {row["label"]: row for row in owner.ledger(r["now"], r["before"])}
    assert rows["Safety lines said first"]["now"] == "100%", "a share of safety calls, not a count"
    assert len(r["overrides"]) == 1
    assert r["types"][0]["code"] == "safety", "safety sorts first in the filter"


def test_every_page_renders_behind_the_token(agent):
    call = booked_tune_up(agent, 1)
    for path in ("/owner/calls", f"/owner/calls/{call}", "/owner/results", "/owner/design"):
        assert agent.client.get(path).status_code == 401, path
    signed_in(agent)
    assert "Calls, Monday Nov 2" in agent.client.get("/owner/calls").text
    assert "Is it paying off" in agent.client.get("/owner/results?days=7").text
    assert "Owner view design system" in agent.client.get("/owner/design").text
    assert "Looked for openings" in agent.client.get(f"/owner/calls/{call}").text
    assert agent.client.get("/owner/results?days=12").status_code == 200, (
        "odd periods read as a day"
    )


def test_the_design_system_files_are_served(agent):
    for path in ("tokens.css", "components.css", "owner.js", "fonts/PublicSans-latin.woff2"):
        assert agent.client.get(f"/owner/static/{path}").status_code == 200, path
    assert "--ember: #FA3500" in agent.client.get("/owner/static/tokens.css").text


def test_a_poster_that_breaks_in_any_way_fails_that_report_only(agent, clock):
    call = booked_tune_up(agent, 1)

    def odd_reply(title, body):
        return [{"number": 1}]  # a list where an issue number was expected

    conn = agent.db()
    reports.add(conn, call, None, "Wrong window.", clock())
    assert reports.file_held(conn, tenant(agent), odd_reply, clock())["failed"] == 1
    row = reports.for_call(conn, call)[0]
    conn.close()
    assert row["status"] == "failed" and "TypeError" in row["error"]


def test_a_safety_call_that_ended_before_the_lines_does_not_claim_they_were_said(agent, clock):
    call = ready_to_book(agent, caller(1))
    agent.turn(call, "Wait, I smell gas.")
    agent.end(call, "caller_hangup")
    conn = agent.db()
    p = owner.call_page(conn, tenant(agent), trace.timeline(conn, call), clock())
    conn.close()
    assert "The safety lines were not said before the call ended." in p["summary"]
    assert "said the safety lines first" not in p["summary"]


def test_a_report_keeps_only_a_line_the_call_has_and_caps_its_length(agent):
    call = booked_tune_up(agent, 1)
    signed_in(agent)
    for turn in ("²", "9" * 30, "99", "1"):
        agent.client.post(f"/owner/calls/{call}/report", data={"text": "x" * 3000, "turn": turn})
    conn = agent.db()
    stored = reports.for_call(conn, call)
    conn.close()
    assert [r["turn_seq"] for r in stored] == [None, None, None, 1]
    assert {len(r["text"]) for r in stored} == {2000}


def test_a_fixed_clock_is_refused_outside_development_and_needs_an_offset(tmp_path):
    from app.main import Services
    from app.settings import Settings

    base = {"db_path": tmp_path / "x.db", "tenants_dir": ROOT / "tenants"}
    with pytest.raises(ValueError, match="development only"):
        Services(Settings(**base, environment="production", fixed_now="2026-11-03T17:20:00-05:00"))
    with pytest.raises(ValueError, match="UTC offset"):
        Services(Settings(**base, fixed_now="2026-11-03T17:20:00"))
    fixed = Services(Settings(**base, fixed_now="2026-11-03T17:20:00-05:00"))
    assert fixed.clock().isoformat() == "2026-11-03T17:20:00-05:00"


def test_the_lines_note_follows_the_servers_record_of_the_lines(agent):
    call = ready_to_book(agent, caller(1))
    agent.turn(call, "Wait, I smell gas.")
    agent.say(call, "Let me check something for you.")
    agent.say(call, GAS_LINES)
    agent.end(call)
    conn = agent.db()
    thread = plain.conversation(trace.timeline(conn, call), tenant(agent))
    conn.close()
    turns_then_notes = [
        (i["kind"], i.get("text", "")[:24]) for i in thread if i["kind"] in ("turn", "note")
    ]
    said = turns_then_notes.index(("note", "Said the signed-off safe"))
    assert turns_then_notes[said - 1][1].startswith(GAS_LINES[:24]), (
        "noted on the turn that said them"
    )


def test_a_safety_call_where_the_lines_never_came_says_so(agent):
    call = ready_to_book(agent, caller(1))
    agent.turn(call, "Wait, I smell gas.")
    agent.say(call, "Okay, let me look at openings.")
    agent.end(call)
    conn = agent.db()
    notes = [i["text"] for i in plain.conversation(trace.timeline(conn, call), tenant(agent))]
    conn.close()
    assert "Said the signed-off safety lines, word for word." not in notes
    assert notes[-1] == "The signed-off safety lines were not said on this call."


def test_a_callback_done_after_it_went_late_is_not_on_time(agent, clock):
    late, prompt = (agent.start(caller(n))["data"]["call_id"] for n in (1, 2))
    for call in (late, prompt):
        agent.tool("flag_person", call, reason="Call back please", callback_in_minutes=30)
    conn = agent.db()
    conn.execute("UPDATE callbacks SET status = 'done' WHERE call_id = ?", (prompt,))
    conn.close()
    clock.advance(minutes=31)
    jobs.run_once(agent.app.state.services)
    conn = agent.db()
    conn.execute("UPDATE callbacks SET status = 'done' WHERE call_id = ?", (late,))
    conn.close()
    callbacks = page(agent)["callbacks"]
    assert (callbacks["done"], callbacks["on_time"]) == (2, 1)
    assert results(agent, 1)["now"]["callbacks"] == (1, 2)


def test_results_leave_out_a_zip_when_the_call_back_on_the_thread_booked(agent, clock):
    first = agent.start(caller(1))["data"]["call_id"]
    agent.turn(first, "Do you go out to Hoboken? That's my mom's place.")
    agent.tool("check_service_area", first, zip="07030")
    agent.end(first, "dropped")
    clock.advance(minutes=1)
    second = agent.start(caller(1))["data"]["call_id"]
    agent.turn(second, "Sorry, we got cut off. I need a tune-up at my place.")
    agent.confirm_read_back(second, in_area_address())
    assert book(agent, second, first_open(agent, second))["ok"]
    agent.end(second)
    assert results(agent, 1)["zips"] == []
