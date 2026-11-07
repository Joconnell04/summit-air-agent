"""The owner's Today page follows each new call: a booking lands in Jobs booked, a callback the
caller booked in Callbacks, a person ask and a safety call in Needs attention, a commercial caller
in Leads. A row on Needs attention checks off, and every call shows its ID (owner view feedback,
2026-11-07)."""

import pytest
from test_ts_owner import booked_tune_up, caller, commercial, out_of_area
from test_ts_owner_view import gas_call_that_booked
from toolkit import Agent, make_server, sign_in

from app import jobs


@pytest.fixture
def agent(tmp_path, clock, mock):
    return sign_in(Agent(make_server(tmp_path, clock, mock[0]), clock))


def today(agent: Agent) -> str:
    return agent.client.get("/owner").text


def part(html: str, start: str, end: str) -> str:
    at = html.index(start)
    return html[at:html.index(end, at)]


def attention(html: str) -> str:
    """The open rows of Needs attention, without the resolved list under them."""
    block = part(html, 'id="attention"', "</section>")
    return block.split('class="panel-foot more"')[0]


def resolved(html: str) -> str:
    return part(html, 'id="attention"', "</section>").split('class="panel-foot more"')[-1]


def jobs_booked(html: str) -> str:
    return part(html, "<h2>Jobs booked</h2>", "</section>")


def callbacks_tab(html: str) -> str:
    return part(html, 'id="tray-callbacks"', 'id="tray-leads"')


def leads_tab(html: str) -> str:
    return part(html, 'id="tray-leads"', "</aside>")


def link(call: str) -> str:
    return f'href="/owner/calls/{call}"'


def booked_callback(agent: Agent, n: int) -> str:
    """A caller who books a call back for a time they name."""
    call = agent.start(caller(n))["data"]["call_id"]
    agent.turn(call, "Can someone call me tomorrow morning about the membership?")
    result = agent.tool("record_lead", call, kind="membership", details={
        "contact_name": "Pat Rivera", "need": "Wants to hear about the membership",
        "callback_day": "tomorrow", "callback_before": "12 PM"})
    assert result["ok"], result
    agent.end(call)
    return call


def person_ask(agent: Agent, n: int) -> str:
    """A caller who asks for a person now: a flag, with a callback promised as the fallback."""
    call = agent.start(caller(n))["data"]["call_id"]
    agent.turn(call, "I just want to talk to a person about my bill.")
    result = agent.tool("flag_person", call, reason="Asked for a person about a bill",
                        callback_in_minutes=30)
    assert result["ok"], result
    agent.end(call)
    return call


def open_flag(agent: Agent, call: str, kind: str) -> int:
    conn = agent.db()
    try:
        return conn.execute(
            "SELECT id FROM flags WHERE call_id = ? AND kind = ? AND status = 'open'", (call, kind)
        ).fetchone()[0]
    finally:
        conn.close()


def test_a_new_booking_shows_in_jobs_booked(agent):
    before = jobs_booked(today(agent))
    call = booked_tune_up(agent, 1)
    assert link(call) not in before
    assert link(call) in jobs_booked(today(agent))


def test_a_callback_the_caller_booked_is_in_callbacks_and_a_person_ask_is_not(agent):
    booked = booked_callback(agent, 1)
    asked = person_ask(agent, 2)
    html = today(agent)
    assert link(booked) in callbacks_tab(html)
    assert "Wants to hear about the membership" in callbacks_tab(html)
    assert link(asked) not in callbacks_tab(html), "a person ask is not a booked callback"
    assert link(asked) in attention(html)
    assert link(booked) not in attention(html)


def test_a_safety_call_is_an_urgent_row_on_needs_attention(agent):
    call = gas_call_that_booked(agent, 1)
    rows = [row for row in attention(today(agent)).split("<tr") if link(call) in row]
    assert rows and any('class="urgent-open"' in row for row in rows)


def test_a_commercial_caller_is_in_leads(agent):
    call = commercial(agent, 1)
    tab = leads_tab(today(agent))
    assert link(call) in tab and "Rooftop unit down" in tab


def test_today_has_no_turned_away_tab(agent):
    call = out_of_area(agent, 1, "11501")
    assert "Turned away" not in today(agent)
    assert link(call) in agent.client.get("/owner/calls").text, "still on the Calls page"


def test_checking_a_row_off_closes_it_and_the_callback_it_promised(agent):
    call = person_ask(agent, 1)
    flag = open_flag(agent, call, "person")
    sent = agent.client.post(f"/owner/flags/{flag}/done", follow_redirects=False)
    assert sent.status_code == 303 and sent.headers["location"].startswith("/owner")
    html = today(agent)
    assert link(call) not in attention(html)
    assert link(call) in resolved(html) and "Done by" in resolved(html)
    conn = agent.db()
    try:
        assert {r[0] for r in conn.execute(
            "SELECT status FROM callbacks WHERE call_id = ?", (call,))} == {"done"}
    finally:
        conn.close()


def test_checking_off_a_person_ask_also_closes_its_late_callback(agent, clock):
    call = person_ask(agent, 1)
    clock.advance(hours=2)
    jobs.run_once(agent.app.state.services)
    late = open_flag(agent, call, "overdue_callback")
    agent.client.post(f"/owner/flags/{open_flag(agent, call, 'person')}/done")
    conn = agent.db()
    try:
        assert conn.execute("SELECT status FROM flags WHERE id = ?", (late,)).fetchone()[0] == "done"
    finally:
        conn.close()


def test_a_late_callback_checked_off_marks_its_callback_done(agent, clock):
    call = booked_callback(agent, 1)
    clock.advance(days=1, hours=4)
    jobs.run_once(agent.app.state.services)
    agent.client.post(f"/owner/flags/{open_flag(agent, call, 'overdue_callback')}/done")
    conn = agent.db()
    try:
        assert conn.execute(
            "SELECT status FROM callbacks WHERE call_id = ?", (call,)).fetchone()[0] == "done"
    finally:
        conn.close()


def test_check_off_needs_a_sign_in_and_an_open_flag(agent):
    call = person_ask(agent, 1)
    flag = open_flag(agent, call, "person")
    agent.client.post("/owner/logout")
    agent.client.post(f"/owner/flags/{flag}/done")
    assert open_flag(agent, call, "person") == flag, "signed out: nothing changes"
    sign_in(agent)
    agent.client.post("/owner/flags/999999/done")
    assert open_flag(agent, call, "person") == flag


def test_every_call_shows_its_id_on_the_list_and_its_page(agent):
    call = booked_tune_up(agent, 1)
    assert f"<code class=\"faint\">{call}</code>" in agent.client.get("/owner/calls").text
    page = agent.client.get(f"/owner/calls/{call}").text
    assert f"<code data-copy-text>{call}</code>" in page and f'data-copy="{call}"' in page


def test_the_commercial_lead_seed_fills_leads_once_a_day(agent, clock):
    from scripts.seed_commercial_leads import CALLS, seed

    db_path = agent.app.state.services.settings.db_path
    made = seed(agent.client, db_path, clock())
    assert len(made) == len(CALLS) == 4
    assert seed(agent.client, db_path, clock()) == [], "a second run adds nothing"
    tab = leads_tab(today(agent))
    for call in CALLS:
        assert call["company"] in tab or call["need"] in tab, call["company"]
    conn = agent.db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM leads WHERE kind = 'commercial'").fetchone()[0] == 4
    finally:
        conn.close()


def test_checking_off_on_another_day_comes_back_to_that_day(agent):
    call = person_ask(agent, 1)
    flag = open_flag(agent, call, "person")
    sent = agent.client.post(f"/owner/flags/{flag}/done?day=2026-11-02", follow_redirects=False)
    assert sent.headers["location"] == "/owner?day=2026-11-02#attention"
