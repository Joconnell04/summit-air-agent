"""Repeat callers, callbacks, do-not-call, leads, flags and the tool-failure queue (spec S-08, S-12a,
S-23, S-32, S-38, S-42, S-48, S-49)."""

import sqlite3

import pytest
from toolkit import DETAILS, UNKNOWN, book, count, first_open, ready_to_book

from app import jobs, lifecycle, records
from app.settings import QUEUE_MAX_ATTEMPTS


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


def test_a_dropped_call_earlier_the_same_day_is_linked_with_the_name_given(agent, clock):
    """Eval round 2, seed 6: same number and same day are the gate, and the greeting asks by name."""
    first = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(first, "Hi, this is Tomasz Nowak. Our heat went out this morning.")
    agent.say(first, "Oh no. What's the address there?")
    agent.turn(first, "145 Java Street, 11222.")
    agent.end(first, "dropped")
    clock.advance(hours=3)
    linked = agent.start(UNKNOWN)["data"]["linked_call"]
    assert linked["call_id"] == first
    assert linked["caller_name"] == "Tomasz"
    assert [t["speaker"] for t in linked["turns"]] == ["caller", "agent", "caller"]
    assert linked["turns"][-1]["text"] == "145 Java Street, 11222."


def test_a_call_from_the_same_number_the_next_day_is_not_linked(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.end(call, "dropped")
    clock.advance(hours=16)  # 1 a.m. the next day in New York
    assert agent.start(UNKNOWN)["data"]["linked_call"] is None


def test_a_call_that_finished_earlier_today_is_not_linked_later(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Thanks, that's all.")
    agent.tool("end_call", call, reason="caller_goodbye")
    agent.end(call, "agent_end")
    clock.advance(minutes=45)
    assert agent.start(UNKNOWN)["data"]["linked_call"] is None


def test_a_drop_followed_by_a_finished_call_is_not_resumed_later(agent, clock):
    """PR #75 review: only the latest call today, and only if it dropped."""
    dropped = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(dropped, "Hi, this is Tomasz, the heat is out.")
    agent.end(dropped, "dropped")
    clock.advance(hours=1)
    finished = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(finished, "Thanks, that's all.")
    agent.tool("end_call", finished, reason="caller_goodbye")
    agent.end(finished, "agent_end")
    clock.advance(hours=2)
    assert agent.start(UNKNOWN)["data"]["linked_call"] is None


def test_the_resume_name_is_the_callers_not_the_account_holders(agent, clock, scenarios):
    """PR #75 review: Maria booked her mother's house on its own account under Rosa; the call back
    asks for Maria."""
    maria = scenarios["repeatCallerOpenJob"]
    first = agent.start(maria["phone"])["data"]["call_id"]
    agent.turn(first, "This call is for my mother's house, the yearly tune-up.")
    agent.confirm_read_back(first, {"street": "1857 77th St", "zip": "11204"})
    booked = agent.tool(
        "book_appointment",
        first,
        job_type="tune_up",
        slot=first_open(agent, first),
        details={**DETAILS, "name": "Rosa Delgado"},
        new_address_account="own",
    )
    assert booked["ok"], booked
    agent.end(first, "dropped")
    clock.advance(hours=2)
    assert agent.start(maria["phone"])["data"]["linked_call"]["caller_name"] == "Maria"


def test_a_before_only_window_tomorrow_is_dialed_tomorrow(agent):
    """PR #75 review: "call me tomorrow before 3" is never dialed today."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool(
        "flag_person",
        call,
        reason="Call me back",
        callback_before="3 PM",
        callback_day="tomorrow",
        system_calls_back=True,
    )
    assert flagged["ok"], flagged
    assert flagged["say"].endswith("tomorrow before 3 PM.")
    conn = agent.db()
    try:
        dial = conn.execute("SELECT due_at FROM outbound").fetchone()["due_at"]
    finally:
        conn.close()
    assert dial.startswith("2026-11-03T13:00")  # 8 AM New York tomorrow, when calling hours open


def test_a_dropped_call_and_its_call_back_later_today_book_once(agent, clock):
    first = ready_to_book(agent)
    assert book(agent, first, first_open(agent, first))["ok"]
    agent.end(first, "dropped")
    clock.advance(hours=2)
    second = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(second, "Hi, we got cut off earlier.")
    agent.confirm_read_back(second, {"street": "101 Union St", "zip": "11231"})
    assert book(agent, second, first_open(agent, second))["refused"] == "limit"


@pytest.mark.parametrize(
    "line,name",
    [
        ("My name is Ana Ruiz and the heat is out.", "Ana"),
        ("It's Tomasz, we got cut off.", "Tomasz"),
        ("this is about my heat", None),
        ("This is Summit Air? Hi.", None),
    ],
)
def test_the_name_a_caller_gave_is_read_only_from_how_names_are_said(line, name):
    assert lifecycle._name_said([line]) == name


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
    # Eval round 3: "top priority", and no on-call team in office hours.
    assert urgent["say"] == (
        "I've let our team know this call is top priority, and someone will call you by 9:20 AM."
    )


@pytest.mark.parametrize(
    "window,spoken",
    [
        # Eval round 3: "after 5" is promised as a time inside it.
        ({"callback_after": "5 PM"}, "someone will call you around 5:30 PM."),
        ({"callback_before": "3 PM"}, "someone will call you before 3 PM."),
        (
            {"callback_after": "1 PM", "callback_before": "3:30 PM"},
            "someone will call you between 1 PM and 3:30 PM.",
        ),
    ],
)
def test_a_window_the_caller_asked_for_is_said_by_the_server_as_asked(agent, window, spoken):
    """Eval round 1, complaint-flag: "after 5" was said back as "by 5"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool("flag_person", call, reason="Unhappy about a bill", **window)
    assert flagged["ok"], flagged
    assert flagged["say"] == f"I've let our team know, and {spoken}"
    assert flagged["data"]["callback_spoken"] == spoken.removeprefix("someone will call you ")[:-1]


def test_an_after_window_is_due_at_the_time_promised_and_dialed_from_its_start(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool(
        "flag_person", call, reason="Call me back", callback_after="5 PM", system_calls_back=True
    )
    assert flagged["ok"], flagged
    assert flagged["data"]["callback_at"].startswith("2026-11-02T22:30")  # 5:30 PM New York
    conn = agent.db()
    try:
        dial = conn.execute("SELECT due_at FROM outbound").fetchone()["due_at"]
    finally:
        conn.close()
    assert dial.startswith("2026-11-02T22:00")  # 5 PM New York


@pytest.mark.parametrize(
    "window,spoken,due",
    [
        # Eval round 2, seed 7: "8 to 2" is 8 AM to 2 PM, never 2 in the morning.
        ({"callback_after": "10", "callback_before": "2"}, "between 10 AM and 2 PM", "19:00"),
        ({"callback_after": "1", "callback_before": "3"}, "between 1 PM and 3 PM", "20:00"),
        ({"callback_after": "6", "callback_before": "9"}, "between 6 PM and 9 PM", "02:00"),
        ({"callback_before": "4"}, "before 4 PM", "21:00"),
        # Eval round 3: "after" is promised as a time inside it.
        ({"callback_after": "noon"}, "around 12:30 PM", "17:30"),
    ],
)
def test_a_bare_hour_in_a_callback_window_reads_as_daytime(agent, window, spoken, due):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool("flag_person", call, reason="Call me back", **window)
    assert flagged["ok"], flagged
    assert flagged["data"]["callback_spoken"] == spoken
    assert flagged["data"]["callback_at"][11:16] == due  # UTC


def test_a_callback_tomorrow_not_before_noon_is_due_tomorrow_and_keeps_the_window(agent):
    """Eval round 2, seed 8: "call me tomorrow, but not before noon, I'm asleep"."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    details = {
        "contact_name": "Beatriz Lima",
        "need": "Wants to hear about the plan",
        "callback_after": "12 PM",
        "callback_day": "tomorrow",
    }
    lead = agent.tool("record_lead", call, kind="membership", details=details)
    assert lead["ok"], lead
    assert lead["say"] == (
        "Someone from our team will call you tomorrow around 12:30 PM to tell you more."
    )
    conn = agent.db()
    try:
        row = conn.execute("SELECT due_at, reason FROM callbacks").fetchone()
    finally:
        conn.close()
    assert row["due_at"].startswith("2026-11-03T17:30")  # 12:30 PM New York, tomorrow
    assert "caller asked: tomorrow around 12:30 PM" in row["reason"]


@pytest.mark.parametrize(
    "window,reason",
    [
        ({"callback_before": "8:30 AM"}, "that time has passed"),
        ({"callback_after": "9:30 PM"}, "outside callback hours"),
        ({"callback_before": "7 AM"}, "that time has passed"),
        ({"callback_after": "4 PM", "callback_before": "2 PM"}, "the window ends before it starts"),
        ({"callback_after": "later"}, "not a time"),
    ],
)
def test_a_window_in_the_past_or_outside_callback_hours_is_refused_with_a_reason(
    agent, window, reason
):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    refused = agent.tool("flag_person", call, reason="Call me back", **window)
    assert refused["refused"] == "invalid_callback_time"
    assert refused["data"]["errors"]["callback_time"] == reason
    assert "let our team know" not in refused["say"]
    assert count(agent, "SELECT COUNT(*) FROM flags") == 0


def test_a_window_outside_callback_hours_names_the_hours(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    refused = agent.tool("flag_person", call, reason="Call me back", callback_after="10 PM")
    assert refused["say"] == "Our team makes calls between 8 AM and 9 PM. What time in there works?"


def test_a_window_and_a_named_time_together_are_refused(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    refused = agent.tool(
        "flag_person", call, reason="Call me back", callback_after="5 PM", callback_time="5 PM"
    )
    assert refused["refused"] == "invalid_arguments"


def test_a_lead_says_the_window_the_caller_asked_for(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    details = {"contact_name": "Ana Ruiz", "need": "Wants to join the plan", "callback_after": "4 PM"}
    lead = agent.tool("record_lead", call, kind="membership", details=details)
    assert lead["say"] == "Someone from our team will call you around 4:30 PM to tell you more."


def test_an_after_window_near_closing_is_promised_no_later_than_closing(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool("flag_person", call, reason="Call me back", callback_after="8:45 PM")
    assert flagged["ok"], flagged
    assert flagged["data"]["callback_spoken"] == "around 9 PM"


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
    assert "works on homes and apartments" in lead["say"]
    log = agent.client.get(
        "/debug/logs",
        params={"call_id": call, "tool": "record_lead"},
        headers={"X-Debug-Token": "test-token"},
    ).json()["data"][0]
    assert [f["op"] for f in log["fsm"]] == ["create_lead"]
    row = (
        sqlite3.connect(mock[0].state.db_path)
        .execute(
            "SELECT call_reason_id, lead_customer_name, lead_phone, summary FROM leads "
            "ORDER BY id DESC LIMIT 1"
        )
        .fetchone()
    )
    assert row == (
        704,
        "Lou's Bakery",
        "(212) 555-0999",
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
        .execute("SELECT call_reason_id, lead_customer_name FROM leads ORDER BY id DESC LIMIT 1")
        .fetchone()
    )
    assert row == (705, "Pat"), "a membership lead carries the Other call reason"


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


def test_after_a_goodbye_a_flag_with_no_time_is_stored_and_nothing_is_asked(agent):
    """Eval round 4, co-op-owner-mentions-board: asking for a callback time reopened a call the
    caller had ended three times."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "any time this afternoon is fine thank you goodbye")
    flagged = agent.tool("flag_person", call, reason="Confirm who can approve")
    assert flagged["ok"], flagged
    assert flagged["say"] == ""
    assert flagged["data"]["callback_spoken"] == "as soon as they can"
    assert "End the call" in flagged["data"]["note"]
    assert count(agent, "SELECT COUNT(*) FROM callbacks WHERE status = 'pending'") == 1


def test_without_a_goodbye_a_flag_with_no_time_still_asks_for_one(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Can someone call me about the bill?")
    assert agent.tool("flag_person", call, reason="Bill")["refused"] == "invalid_callback_time"


def test_each_caller_turn_says_what_the_call_already_knows(agent):
    """Eval round 4: re-asking what was answered. The note comes from the tool log."""
    from prompts.assemble import server_notes

    call = ready_to_book(agent)
    agent.tool("check_service_area", call, zip="11231")
    agent.tool("find_slots", call, job_type="no_heat", facts={"who_home": "me and my wife"})
    turn = agent.turn(call, "Okay, what's next?")
    known = turn["data"]["known"]
    assert known["ZIP"] == "11231"
    assert known["who's home"] == "me and my wife"
    assert known["address"] == "101 Union St, 11231 (confirmed)"
    assert known["phone"] == "ending 0999 (confirmed)"
    note = server_notes([], known)[-1]
    assert note.startswith("[server] Already known, don't ask again: ZIP 11231;")
    assert len(note.split()) < 40


def test_the_known_list_takes_the_name_and_own_or_rent_from_the_callers_lines(agent):
    """Eval round 4 run 20261104-040959, spell-street-letter-by-letter."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "Hi, the heat's out in my home.")
    turn = agent.turn(call, "Oh and my name's Ruth Adeyemi if you need it.")
    assert turn["data"]["known"]["name"] == "Ruth Adeyemi"
    assert turn["data"]["known"]["rents or owns"] == "owns"
    renter = agent.start("(212) 555-0902")["data"]["call_id"]
    assert agent.turn(renter, "I rent the place, my landlord is Dennis.")["data"]["known"] == {
        "rents or owns": "rents"
    }


def test_a_zip_and_system_the_caller_said_are_known_on_the_next_turn(agent):
    """Eval run 20261104-053435: the agent asked again for the ZIP and the system type."""
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.turn(call, "My heat pump stopped. It's 8415 Ridge Blvd, Brooklyn, one one two oh nine.")
    known = agent.turn(call, "Okay.")["data"]["known"]
    assert known["ZIP said"] == "11209"
    assert known["system"] == "heat pump"
