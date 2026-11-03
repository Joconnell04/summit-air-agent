"""Repeat callers, callbacks, do-not-call, leads, flags and the tool-failure queue (spec S-08, S-12a,
S-23, S-32, S-38, S-42, S-48, S-49)."""

import sqlite3

import httpx
import pytest
from toolkit import UNKNOWN, book, count, first_open, ready_to_book

from app import jobs, records
from app.fsm import FsmUnavailable
from app.settings import QUEUE_MAX_ATTEMPTS


class Down(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture
def fsm(agent):
    """The adapter, with a way to swap its HTTP client and put it back."""
    adapter = agent.app.state.services.fsm["summit-air"]
    original = adapter.http

    class Switch:
        def down(self):
            adapter.http = httpx.Client(transport=Down(), base_url="http://testserver")

        def losing_job_responses(self):
            """The job reaches the FSM, but the answer never comes back."""
            real = adapter.create_job

            def create_job(*args, **kwargs):
                real(*args, **kwargs)
                raise FsmUnavailable("create_job: ReadTimeout")

            adapter.create_job = create_job

        def up(self):
            adapter.http = original
            adapter.__dict__.pop("create_job", None)

    yield Switch()
    Switch().up()


def run_jobs(agent):
    return jobs.run_once(agent.app.state.services)


def fsm_jobs(mock) -> int:
    return sqlite3.connect(mock[0].state.db_path).execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


# ---- repeat callers --------------------------------------------------------------------------


def test_a_quick_call_back_is_linked_and_resumes_where_it_stopped(agent, clock):
    first = ready_to_book(agent)
    agent.turn(first, "It's the boiler in the basement, it keeps clicking.")
    agent.say(first, "Got it. And when did it stop?")
    agent.end(first, "dropped")
    clock.advance(minutes=3)
    second = agent.start(UNKNOWN)["data"]
    linked = second["linked_call"]
    assert linked["call_id"] == first
    assert linked["dropped"] is True
    assert linked["last_caller_line"] == "It's the boiler in the basement, it keeps clicking."
    assert linked["last_agent_line"] == "Got it. And when did it stop?"
    assert second["offer_callback"] is False


def test_nothing_done_on_the_dropped_call_is_done_again(agent, clock):
    first = ready_to_book(agent)
    assert book(agent, first, first_open(agent, first))["ok"]
    agent.end(first, "dropped")
    clock.advance(minutes=2)
    second = agent.start(UNKNOWN)["data"]
    assert second["linked_call"]["booking"]["status"] == "booked"
    agent.turn(second["call_id"], "Hi, we got cut off.")
    agent.confirm_read_back(second["call_id"], {"street": "101 Union St", "zip": "11231"})
    assert (
        book(agent, second["call_id"], first_open(agent, second["call_id"]))["refused"] == "limit"
    )


def test_repeated_drops_get_a_callback_offer(agent, clock):
    for _ in range(2):
        call = agent.start(UNKNOWN)["data"]["call_id"]
        agent.turn(call, "Hello? Can you hear me?")
        agent.end(call, "dropped")
        clock.advance(minutes=2)
    third = agent.start(UNKNOWN)
    assert third["data"]["offer_callback"] is True
    assert "keep getting cut off" in third["say"]


def test_a_call_from_the_same_number_long_after_is_not_linked(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.end(call, "dropped")
    clock.advance(minutes=45)
    assert agent.start(UNKNOWN)["data"]["linked_call"] is None


# ---- flags and callbacks -----------------------------------------------------------------------


def test_a_flag_returns_its_sentence_only_once_stored_and_names_a_time(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool(
        "flag_person", call, reason="Wants to talk about a warranty", callback_time="4:30 PM"
    )
    assert flagged["ok"]
    assert flagged["say"] == "I've let our team know, and someone will call you by 4:30 PM."
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'person'") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1
    urgent = agent.tool(
        "flag_person",
        call,
        reason="Oxygen machine at home",
        priority="Urgent",
        callback_in_minutes=20,
    )
    assert urgent["say"].startswith("I've marked this as urgent and let our on-call team know")
    assert "9:20 AM" in urgent["say"]


@pytest.mark.parametrize("bad", [None, "shortly", "soon", "8:00 AM", "25:00"])
def test_a_callback_without_a_real_future_time_is_refused_and_nothing_is_claimed(agent, bad):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    args = {"reason": "Call me back"}
    if bad:
        args["callback_time"] = bad
    refused = agent.tool("flag_person", call, **args)
    assert refused["refused"] == "invalid_callback_time"
    assert "let our team know" not in refused["say"]
    assert count(agent, "SELECT COUNT(*) FROM flags") == 0


def test_an_overdue_callback_is_flagged(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.tool("flag_person", call, reason="Billing question", callback_in_minutes=30)
    clock.advance(minutes=31)
    report = run_jobs(agent)
    assert len(report["overdue_callbacks"]) == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'overdue'") == 1
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'overdue_callback'") == 1


def test_outbound_calls_go_only_to_the_caller_in_hours_once_and_never_to_do_not_call(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.tool("flag_person", call, reason="Call back about the quote", callback_in_minutes=60)
    services = agent.app.state.services
    tenant = services.tenants.default
    conn = services.connect()

    def allowed():
        row = conn.execute("SELECT * FROM callbacks").fetchone()
        return records.outbound_allowed(conn, tenant, row, clock())

    assert allowed() == (True, "ok")
    clock.advance(hours=13)  # 10 p.m.
    assert allowed() == (False, "outside_hours")
    clock.advance(hours=-13)
    records.record_attempt(conn, conn.execute("SELECT id FROM callbacks").fetchone()[0], clock())
    assert allowed() == (False, "attempted")
    conn.execute("UPDATE callbacks SET attempts = 0, phone_key = '7185550000'")
    assert allowed() == (False, "not_the_caller")
    conn.execute("UPDATE callbacks SET phone_key = '2125550999'")
    agent.tool("mark_do_not_call", call)
    conn.execute("UPDATE callbacks SET status = 'pending'")
    assert allowed() == (False, "do_not_call")
    conn.close()


def test_there_is_no_tool_that_calls_911_or_an_outside_number(agent):
    tools = {r.path for r in agent.app.routes if r.path.startswith("/tools/")}
    assert not {
        p
        for p in tools
        if any(w in p for w in ("dial", "call_number", "transfer", "911", "place_call"))
    }


# ---- do not call -------------------------------------------------------------------------------


def test_do_not_call_is_marked_on_the_callers_words_and_cancels_callbacks(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.tool("flag_person", call, reason="Callback requested", callback_in_minutes=30)
    turn = agent.turn(call, "Actually, don't call me, I'll call you.")
    assert "do_not_call" in turn["data"]["hints"]
    # Marked at once, before the agent does anything.
    assert count(agent, "SELECT COUNT(*) FROM do_not_call") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 0
    marked = agent.tool("mark_do_not_call", call)
    assert marked["say"] == (
        "Understood, we won't call you. That means the callback is off, so give us a call any "
        "time you need us."
    )
    assert marked["data"]["cancelled_callbacks"] == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 0
    assert count(agent, "SELECT COUNT(*) FROM do_not_call") == 1
    # Nothing new is promised to that number afterwards.
    later = agent.tool("flag_person", call, reason="Another question", callback_in_minutes=30)
    assert later["say"] == "I've let our team know."
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 0


def test_do_not_call_without_a_callback_says_the_short_line(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    assert agent.tool("mark_do_not_call", call)["say"] == "Understood, we won't call you."
    assert agent.start(UNKNOWN)["data"]["caller"]["do_not_call"] is True


# ---- leads -------------------------------------------------------------------------------------


def test_a_commercial_call_saves_one_line_with_no_extra_lookups(agent, mock):
    started = agent.start(UNKNOWN)["data"]
    call = started["call_id"]
    agent.turn(call, "Hi, I run a bakery and our rooftop unit died.")
    lead = agent.tool(
        "record_lead",
        call,
        kind="commercial",
        details={
            "contact_name": "Lou",
            "company": "Lou's Bakery",
            "need": "Rooftop unit not cooling",
        },
    )
    assert lead["ok"]
    assert "only works on homes" in lead["say"]
    log = agent.client.get(
        "/debug/logs",
        params={"call_id": call, "tool": "record_lead"},
        headers={"X-Debug-Token": "test-token"},
    ).json()["data"][0]
    assert [f["op"] for f in log["fsm"]] == ["create_lead"]
    row = (
        sqlite3.connect(mock[0].state.db_path)
        .execute("SELECT kind, customer_type, summary FROM leads ORDER BY id DESC LIMIT 1")
        .fetchone()
    )
    assert row == (
        "Commercial",
        "Commercial",
        "Lou's Bakery; contact Lou (212) 555-0999; Rooftop unit not cooling",
    )
    assert count(agent, "SELECT COUNT(*) FROM callbacks") == 0, (
        "no callback is offered to a commercial caller"
    )


def test_a_membership_lead_needs_a_named_callback_time(agent, mock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    details = {"contact_name": "Pat", "need": "Wants to join the membership"}
    refused = agent.tool("record_lead", call, kind="membership", details=details)
    assert refused["refused"] == "invalid_callback_time"
    lead = agent.tool(
        "record_lead", call, kind="membership", details={**details, "callback_time": "2 PM"}
    )
    assert lead["ok"]
    assert lead["say"] == "Someone from our team will call you by 2 PM to tell you more."
    assert count(agent, "SELECT COUNT(*) FROM callbacks") == 1
    row = (
        sqlite3.connect(mock[0].state.db_path)
        .execute("SELECT kind, customer_type FROM leads ORDER BY id DESC LIMIT 1")
        .fetchone()
    )
    assert row == ("Other", "Residential")


# ---- the tool-failure queue --------------------------------------------------------------------


def test_a_booking_while_the_fsm_is_down_is_queued_said_honestly_and_retried_once_back(
    agent, mock, fsm
):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    before = fsm_jobs(mock)
    fsm.down()
    queued = book(agent, call, slot, key="b1")
    assert queued["refused"] == "fsm_unavailable"
    assert queued["say"] == (
        "I can't book that right now, but I've written it all down and someone will call you by "
        "10 AM to confirm."
    )
    assert count(agent, "SELECT COUNT(*) FROM bookings WHERE status = 'queued'") == 1
    assert count(agent, "SELECT COUNT(*) FROM callbacks") == 1
    assert book(agent, call, slot, key="b1") == queued, "a retried request gets the first answer"
    logged = count(agent, "SELECT COUNT(*) FROM tool_calls WHERE refused = 'fsm_unavailable'")
    assert logged == 2

    run_jobs(agent)  # still down
    assert count(agent, "SELECT COUNT(*) FROM bookings WHERE status = 'queued'") == 1
    fsm.up()
    run_jobs(agent)
    assert count(agent, "SELECT COUNT(*) FROM bookings WHERE status = 'booked'") == 1
    assert fsm_jobs(mock) == before + 1
    run_jobs(agent)
    assert fsm_jobs(mock) == before + 1


def test_a_retry_after_a_lost_response_does_not_book_twice(agent, mock, fsm):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    before = fsm_jobs(mock)
    fsm.losing_job_responses()
    assert book(agent, call, slot)["refused"] == "fsm_unavailable"
    assert fsm_jobs(mock) == before + 1, "the job did reach the FSM"
    fsm.up()
    run_jobs(agent)
    assert count(agent, "SELECT COUNT(*) FROM bookings WHERE status = 'booked'") == 1
    assert fsm_jobs(mock) == before + 1


def test_a_booking_that_keeps_failing_is_flagged_for_a_person(agent, fsm):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    fsm.down()
    book(agent, call, slot)
    for _ in range(QUEUE_MAX_ATTEMPTS):
        run_jobs(agent)
    assert count(agent, "SELECT COUNT(*) FROM bookings WHERE status = 'failed'") == 1
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'queue_failed'") == 1


def test_a_lookup_while_the_fsm_is_down_is_refused_honestly_and_logged(agent, fsm):
    fsm.down()
    started = agent.start("(718) 555-0142")
    assert started["ok"] and started["data"]["fsm_available"] is False
    call = started["data"]["call_id"]
    found = agent.tool("find_customer", call, phone="(718) 555-0142")
    assert found["refused"] == "fsm_unavailable"
    log = agent.client.get(
        "/debug/logs",
        params={"call_id": call, "errors": "true"},
        headers={"X-Debug-Token": "test-token"},
    ).json()["data"]
    assert log[-1]["error"].startswith("fsm_unavailable")


def test_a_lead_queued_while_the_fsm_is_down_is_sent_later(agent, mock, fsm):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    fsm.down()
    details = {"contact_name": "Pat", "need": "Heat pump quote", "callback_in_minutes": 90}
    lead = agent.tool("record_lead", call, kind="install", details=details)
    assert lead["ok"] and lead["data"]["queued"] is True
    fsm.up()
    run_jobs(agent)
    assert count(agent, "SELECT COUNT(*) FROM leads WHERE status = 'stored'") == 1


def test_the_slot_cache_is_used_between_turns_and_cleared_by_a_booking(agent):
    call = ready_to_book(agent)
    cache = agent.app.state.services.caches["summit-air"]
    first_open(agent, call)
    first_open(agent, call)
    log = agent.client.get(
        "/debug/logs",
        params={"call_id": call, "tool": "find_slots"},
        headers={"X-Debug-Token": "test-token"},
    ).json()["data"]
    assert "capacity" in [f["op"] for f in log[0]["fsm"]]
    assert log[1]["fsm"] == [], "the second ask is answered from the cache"
    book(agent, call, first_open(agent, call))
    assert len(cache) == 0, "a booking clears the cache"
    first_open(agent, call)
    assert cache.refresh() == 1, "the timer refreshes what is cached"


def test_the_retry_job_leaves_a_booking_that_is_still_being_sent_alone(agent, clock):
    call = ready_to_book(agent)
    services = agent.app.state.services
    conn = services.connect()
    conn.execute(
        "INSERT INTO bookings (call_id, kind, status, fsm_key, request_json, created_at, updated_at) "
        "VALUES (?, 'book', 'sending', 'k-live', '{}', ?, ?)",
        (call, "2026-11-02T14:00:00Z", "2026-11-02T14:00:00Z"),
    )
    report = run_jobs(agent)
    assert report["summit-air"]["queue"]["failed"] == 0
    assert (
        conn.execute("SELECT status FROM bookings WHERE fsm_key = 'k-live'").fetchone()[0]
        == "sending"
    )
    conn.close()
