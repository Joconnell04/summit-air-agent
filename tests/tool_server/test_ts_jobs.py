"""The background jobs brief 06 adds: earlier openings offered to flagged urgent bookings, callbacks
the system places, booked-and-kept, on-call email after hours, and retention over the new tables
(journeys 11 and 12; spec S-25, S-32, S-42, S-45; C-42)."""

import smtplib

from toolkit import UNKNOWN, Agent, book, count, in_area_address, make_server, ready_to_book

from app import jobs

TOMORROW_MORNING = {"date": "2026-11-03", "window": "0800-1200"}


def services(agent: Agent):
    return agent.app.state.services


def tenant(agent: Agent):
    return services(agent).tenants.default


def run(agent: Agent, job, *args):
    conn = agent.db()
    try:
        return job(conn, tenant(agent), *args, agent.clock())
    finally:
        conn.close()


def offer(agent: Agent) -> int:
    s, slug = services(agent), tenant(agent).slug
    return run(agent, jobs.offer_earlier_slots, s.fsm[slug], s.caches[slug])


def urgent_booked_tomorrow(agent: Agent, caller: str = UNKNOWN) -> tuple[str, int]:
    """An urgent no-heat booking for tomorrow, flagged to look for an earlier slot (S-25)."""
    call = ready_to_book(agent, caller)
    agent.turn(call, "My 84-year-old parent lives with me and it's 52 in here.")
    booked = book(agent, call, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=52)
    assert booked["ok"] and booked["data"]["seek_earlier"], booked
    return call, booked["data"]["booking_id"]


def claim(agent: Agent) -> dict | None:
    response = agent.client.post("/outbound/claim", json={"worker": "test"}).json()
    assert response["ok"]
    return response["data"]["outbound"]


def done(agent: Agent, outbound_id: int, answered: bool, voicemail: bool = False) -> dict:
    body = {"answered": answered, "voicemail": voicemail}
    return agent.client.post(f"/outbound/{outbound_id}/done", json=body).json()


def place(agent: Agent, claimed: dict) -> str:
    """The voice layer starting the claimed call."""
    body = {
        "call_id": claimed["call_id"],
        "caller_id": claimed["phone"],
        "dialed_number": claimed["dialed_from"],
        "direction": "outbound",
    }
    assert agent.client.post("/calls/start", json=body).json()["ok"]
    agent.say(claimed["call_id"], claimed["opening_line"])
    return claimed["call_id"]


# ---- earlier openings (journey 12) -----------------------------------------------------------


def test_an_urgent_booking_flagged_for_earlier_is_offered_the_first_opening_once(agent):
    urgent_booked_tomorrow(agent)
    assert offer(agent) == 1
    assert offer(agent) == 0, "one offer call per booking"
    claimed = claim(agent)
    assert claimed["kind"] == "earlier_slot"
    assert claimed["phone"] == UNKNOWN
    assert claimed["offer"]["date"] == "2026-11-02"
    assert "today" in claimed["opening_line"]
    assert claim(agent) is None, "a claimed call is not handed out twice"


def test_an_accepted_opening_moves_the_booking_through_the_normal_tool(agent, clock):
    call, _ = urgent_booked_tomorrow(agent)
    offer(agent)
    clock.advance(minutes=15)
    claimed = claim(agent)
    placed = place(agent, claimed)
    agent.turn(placed, "Yes, today works, thank you.")
    moved = agent.tool(
        "reschedule_appointment",
        placed,
        booking_id=claimed["booking_id"],
        slot=claimed["offer"],
        reason="earlier opening",
    )
    assert moved["ok"], moved
    agent.end(placed, "agent_end")
    assert done(agent, claimed["outbound_id"], answered=True)["data"]["result"] == "accepted"
    assert count(agent, "SELECT seek_earlier FROM bookings WHERE call_id = ?", call) == 0
    assert (
        count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'seek_earlier' AND status = 'open'")
        == 0
    )
    start = count(agent, "SELECT window_start FROM bookings WHERE call_id = ?", call)
    assert start.startswith("2026-11-02")


def test_an_answered_offer_with_no_move_is_declined_and_keeps_the_booking(agent, clock):
    call, _ = urgent_booked_tomorrow(agent)
    offer(agent)
    claimed = claim(agent)
    place(agent, claimed)
    agent.turn(claimed["call_id"], "No thanks, tomorrow is fine.")
    agent.end(claimed["call_id"], "agent_end")
    assert done(agent, claimed["outbound_id"], answered=True)["data"]["result"] == "declined"
    assert count(agent, "SELECT seek_earlier FROM bookings WHERE call_id = ?", call) == 1
    assert done(agent, claimed["outbound_id"], answered=True)["refused"] == "invalid_state"


def test_an_offer_reported_at_pickup_is_judged_when_its_call_ends(agent, clock):
    """The voice layer reports the pickup before anyone speaks (spec S-25, journey 12)."""
    call, _ = urgent_booked_tomorrow(agent)
    offer(agent)
    clock.advance(minutes=15)
    claimed = claim(agent)
    placed = place(agent, claimed)
    assert done(agent, claimed["outbound_id"], answered=True)["data"]["result"] == "answered"
    agent.turn(placed, "Yes, today works, thank you.")
    moved = agent.tool(
        "reschedule_appointment",
        placed,
        booking_id=claimed["booking_id"],
        slot=claimed["offer"],
        reason="earlier opening",
    )
    assert moved["ok"], moved
    agent.end(placed, "agent_end")
    assert count(agent, "SELECT result FROM outbound") == "accepted"
    assert count(agent, "SELECT seek_earlier FROM bookings WHERE call_id = ?", call) == 0


def test_an_offer_whose_call_never_reports_its_end_is_judged_by_the_job(agent, clock):
    call, _ = urgent_booked_tomorrow(agent)
    offer(agent)
    claimed = claim(agent)
    place(agent, claimed)
    done(agent, claimed["outbound_id"], answered=True)
    jobs.run_once(services(agent))
    assert count(agent, "SELECT result FROM outbound") == "answered", "still waiting for the end"
    clock.advance(hours=1)
    jobs.run_once(services(agent))
    assert count(agent, "SELECT result FROM outbound") == "declined"
    assert count(agent, "SELECT seek_earlier FROM bookings WHERE call_id = ?", call) == 1


def test_no_offer_outside_outbound_hours_or_to_a_do_not_call_number(agent, clock):
    call, _ = urgent_booked_tomorrow(agent)
    clock.advance(hours=13)  # 10 p.m.
    assert offer(agent) == 0
    clock.advance(hours=-13)
    conn = agent.db()
    conn.execute(
        "INSERT INTO do_not_call (phone_key, phone, call_id, added_at) VALUES ('2125550999', ?, ?, ?)",
        (UNKNOWN, call, "2026-11-02T14:00:00Z"),
    )
    conn.close()
    assert offer(agent) == 0


def test_an_after_hours_window_is_never_offered_automatically(agent):
    urgent_booked_tomorrow(agent)
    offer(agent)
    claimed = claim(agent)
    assert claimed["offer"]["window"] != "after_hours"


# ---- callbacks the system places -------------------------------------------------------------


def system_callback(agent: Agent, minutes: int = 30) -> str:
    call = agent.start(UNKNOWN)["data"]["call_id"]
    flagged = agent.tool(
        "flag_person",
        call,
        reason="Caller asked to be called back",
        callback_in_minutes=minutes,
        system_calls_back=True,
    )
    assert flagged["ok"], flagged
    agent.end(call)
    return call


def test_a_requested_callback_waits_for_its_time_then_is_dialed_once(agent, clock):
    system_callback(agent)
    assert jobs.run_once(services(agent))["summit-air"]["callbacks"]["queued"] == 0
    assert claim(agent) is None
    clock.advance(minutes=31)
    report = jobs.run_once(services(agent))
    assert report["summit-air"]["callbacks"]["queued"] == 1
    assert report["overdue_callbacks"] == [], "the system is dialing it; not overdue yet"
    claimed = claim(agent)
    assert claimed["kind"] == "callback"
    assert count(agent, "SELECT attempts FROM callbacks") == 1
    placed = place(agent, claimed)
    agent.turn(placed, "Hi, thanks for calling back.")
    agent.confirm_read_back(placed, in_area_address())
    agent.tool("end_call", placed, reason="resolved")
    agent.end(placed)
    finished = done(agent, claimed["outbound_id"], answered=True)["data"]
    assert (finished["result"], finished["outcome"]) == ("answered", "resolved")
    # Answering is not enough: the callback is done once the call log shows it resolved
    # (docs/decisions/26-callback-done.md; the cases are in test_ts_callback_done.py).
    assert count(agent, "SELECT status FROM callbacks") == "pending"
    assert jobs.run_once(services(agent))["summit-air"]["callbacks_done"]["done"] == 1
    assert count(agent, "SELECT status FROM callbacks") == "done"
    open_flags = "SELECT COUNT(*) FROM flags WHERE status = 'open'"
    assert count(agent, open_flags) == 0, "nobody needs to act on an answered system callback"


def test_an_unanswered_callback_goes_to_a_person_and_is_flagged_once(agent, clock):
    system_callback(agent)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    claimed = claim(agent)
    assert done(agent, claimed["outbound_id"], False, True)["data"]["result"] == "voicemail"
    assert count(agent, "SELECT status FROM callbacks") == "overdue"
    jobs.run_once(services(agent))
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 1
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'overdue_callback'") == 0


def test_a_callback_due_after_hours_is_handed_to_a_person_not_dialed(agent, clock):
    clock.advance(hours=11, minutes=30)  # 8:30 p.m.
    system_callback(agent, minutes=45)
    clock.advance(minutes=46)
    report = jobs.run_once(services(agent))
    assert report["summit-air"]["callbacks"]["skipped"] == 1
    assert claim(agent) is None
    assert count(agent, "SELECT result FROM outbound") == "outside_hours"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 1


def test_a_callback_to_a_number_marked_do_not_call_is_never_dialed(agent, clock):
    call = system_callback(agent)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    conn = agent.db()
    conn.execute(
        "INSERT INTO do_not_call (phone_key, phone, call_id, added_at) VALUES ('2125550999', ?, ?, ?)",
        (UNKNOWN, call, "2026-11-02T14:00:00Z"),
    )
    conn.close()
    assert claim(agent) is None, "rechecked at claim time"
    assert count(agent, "SELECT result FROM outbound") == "do_not_call"


def test_a_dial_never_reported_back_is_settled_and_goes_to_a_person(agent, clock):
    system_callback(agent)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    claim(agent)
    clock.advance(minutes=jobs.STUCK_DIALING_MINUTES + 1)
    assert jobs.run_once(services(agent))["summit-air"]["callbacks"]["no_report"] == 1
    assert count(agent, "SELECT result FROM outbound") == "no_report"
    assert count(agent, "SELECT status FROM callbacks") == "overdue"


def test_without_the_flag_a_callback_is_left_to_a_person(agent, clock):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.tool("flag_person", call, reason="Wants a manager", callback_in_minutes=30)
    assert count(agent, "SELECT COUNT(*) FROM outbound") == 0


# ---- booked-and-kept -------------------------------------------------------------------------


def kept_check(agent: Agent) -> dict:
    return run(agent, jobs.check_kept, services(agent).fsm[tenant(agent).slug])


def test_a_booking_still_on_the_schedule_when_its_window_comes_is_kept(agent, clock):
    call = ready_to_book(agent)
    assert book(agent, call, TOMORROW_MORNING)["ok"]
    assert kept_check(agent) == {"kept": 0, "cancelled": 0}, "its window hasn't come"
    clock.advance(days=1)
    assert kept_check(agent) == {"kept": 1, "cancelled": 0}
    assert count(agent, "SELECT fsm_status FROM kept") == "Scheduled"
    assert kept_check(agent) == {"kept": 0, "cancelled": 0}, "checked once"


def test_a_cancelled_booking_is_not_kept(agent, clock):
    call = ready_to_book(agent)
    job_id = book(agent, call, TOMORROW_MORNING)["data"]["booking_id"]
    agent.turn(call, "Actually I need to cancel that.")
    assert agent.tool("cancel_appointment", call, booking_id=job_id, reason="fixed it")["ok"]
    clock.advance(days=1)
    assert kept_check(agent) == {"kept": 0, "cancelled": 1}


def test_a_booking_moved_later_waits_for_its_new_window(agent, clock):
    call = ready_to_book(agent)
    job_id = book(agent, call, TOMORROW_MORNING)["data"]["booking_id"]
    later = {"date": "2026-11-06", "window": "0800-1200"}
    agent.turn(call, "Can we do Friday instead?")
    assert agent.tool("reschedule_appointment", call, booking_id=job_id, slot=later, reason="x")[
        "ok"
    ]
    clock.advance(days=1)
    assert kept_check(agent) == {"kept": 0, "cancelled": 0}
    clock.advance(days=3)
    assert kept_check(agent) == {"kept": 1, "cancelled": 0}


# ---- on-call email after hours (journey 11) --------------------------------------------------


class FakeMailer:
    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send(self, to: str, subject: str, body: str) -> str:
        if self.fail:
            raise smtplib.SMTPException("relay refused")
        self.sent.append((to, subject, body))
        return "sent"


def alert(agent: Agent) -> dict:
    return run(agent, jobs.alert_on_call, services(agent).mailer)


def urgent_flag(
    agent: Agent, reason: str = "Elderly caller, no heat, 48 F inside", caller: str = UNKNOWN
) -> str:
    call = ready_to_book(agent, caller)
    assert agent.tool(
        "flag_person", call, reason=reason, priority="Urgent", callback_in_minutes=20
    )["ok"]
    return call


def test_an_urgent_flag_after_hours_emails_the_on_call_person_what_where_who_and_why(agent, clock):
    services(agent).mailer = mailer = FakeMailer()
    clock.advance(hours=12)  # 9 p.m.
    urgent_flag(agent)
    assert alert(agent) == {"sent": 1, "held": 0, "failed": 0}
    to, subject, body = mailer.sent[0]
    assert to == tenant(agent).on_call_alerts["email"]
    assert "Elderly caller" in subject
    assert "What: Elderly caller, no heat, 48 F inside" in body
    assert "Where: 101 Union St, 11231" in body
    assert f"Who: {UNKNOWN}" in body
    assert "Caller was told: someone will call by 9:20 PM" in body
    assert alert(agent) == {"sent": 0, "held": 0, "failed": 0}, "one email per flag"


def test_no_email_in_hours_for_a_routine_flag_or_for_seek_earlier(agent, clock):
    services(agent).mailer = mailer = FakeMailer()
    urgent_flag(agent)  # 9 a.m., the office is open
    clock.advance(hours=12)
    call = ready_to_book(agent)
    agent.tool("flag_person", call, reason="Billing question", callback_in_minutes=30)
    # After hours an older person at home needs the on-call alert before the booking (journey
    # 11): that alert is the one email, never the routine flag or the seek_earlier one.
    late = ready_to_book(agent, "(212) 555-0907")
    agent.turn(late, "My 84-year-old parent lives with me and it's 52 in here.")
    agent.tool("flag_person", late, reason="On-call: 84 at home, 52 F", priority="Urgent")
    booked = book(agent, late, TOMORROW_MORNING, job_type="no_heat", inside_temp_f=52)
    assert booked["ok"] and booked["data"]["seek_earlier"], booked
    alert(agent)
    assert len(mailer.sent) == 1
    assert "On-call: 84 at home" in mailer.sent[0][2]


def test_without_a_mail_server_the_alert_is_held_and_recorded(agent, clock):
    clock.advance(hours=12)
    urgent_flag(agent)
    assert alert(agent) == {"sent": 0, "held": 1, "failed": 0}
    assert count(agent, "SELECT status FROM alerts") == "held"


def test_a_failed_send_is_retried_then_left_failed(agent, clock):
    services(agent).mailer = FakeMailer(fail=True)
    clock.advance(hours=12)
    urgent_flag(agent)
    for _ in range(5):
        alert(agent)
    assert count(agent, "SELECT attempts FROM alerts") == 3
    assert "relay refused" in count(agent, "SELECT error FROM alerts")
    services(agent).mailer = FakeMailer()
    assert alert(agent) == {"sent": 0, "held": 0, "failed": 0}, "no further tries"


def test_a_flag_older_than_the_alert_window_is_not_emailed(agent, clock):
    services(agent).mailer = mailer = FakeMailer()
    clock.advance(hours=12)
    urgent_flag(agent)
    clock.advance(minutes=tenant(agent).on_call_alerts["max_age_minutes"] + 1)
    alert(agent)
    assert mailer.sent == []


# ---- retention over the new tables -----------------------------------------------------------


def test_retention_removes_outbound_kept_and_alert_rows_with_their_call(tmp_path, clock, mock):
    agent = Agent(make_server(tmp_path, clock, mock[0], environment="production"), clock)
    call = ready_to_book(agent, "(212) 555-0905")
    book(agent, call, TOMORROW_MORNING)
    clock.advance(hours=12)
    urgent_flag(agent, caller="(212) 555-0906")
    system_callback(agent)
    jobs.run_once(services(agent))
    clock.advance(days=1)
    jobs.run_once(services(agent))
    for table in ("outbound", "kept", "alerts"):
        assert count(agent, f"SELECT COUNT(*) FROM {table}") == 1, table
    clock.advance(days=366)
    jobs.run_once(services(agent))
    for table in ("calls", "outbound", "kept", "alerts", "callbacks", "flags"):
        assert count(agent, f"SELECT COUNT(*) FROM {table}") == 0, table


def test_the_new_jobs_run_on_the_server_timer(agent):
    report = jobs.run_once(services(agent))["summit-air"]
    assert {"earlier_slots_offered", "callbacks", "kept", "alerts"} <= set(report)


# ---- reviewer findings ------------------------------------------------------------------------


def test_a_queued_callback_nobody_claims_goes_to_a_person(agent, clock):
    system_callback(agent)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    clock.advance(minutes=jobs.UNCLAIMED_MINUTES + 1)
    assert jobs.run_once(services(agent))["summit-air"]["callbacks"]["not_claimed"] == 1
    assert count(agent, "SELECT status FROM callbacks") == "overdue"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 1


def test_the_system_never_takes_an_urgent_or_safety_callback_off_a_person(agent):
    call = agent.start(UNKNOWN)["data"]["call_id"]
    agent.tool(
        "flag_person",
        call,
        reason="No heat, baby at home",
        priority="Urgent",
        callback_in_minutes=20,
        system_calls_back=True,
    )
    other = agent.start("(212) 555-0911")["data"]["call_id"]
    agent.turn(other, "I smell gas in the kitchen.")
    agent.tool(
        "flag_person", other, reason="Call me back", callback_in_minutes=20, system_calls_back=True
    )
    assert count(agent, "SELECT COUNT(*) FROM outbound") == 0
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_requested'") == 0


def test_a_flag_reason_with_a_newline_doesnt_stop_the_jobs(agent, clock):
    clock.advance(hours=12)
    urgent_flag(agent, reason="Elderly caller\nno heat")
    report = jobs.run_once(services(agent))
    assert report["summit-air"]["alerts"]["held"] == 1
    assert "\n" not in count(agent, "SELECT subject FROM alerts")


def test_a_header_error_from_the_mail_server_is_recorded_not_raised(agent, clock):
    class Broken:
        def send(self, to, subject, body):
            raise ValueError("bad header")

    services(agent).mailer = Broken()
    clock.advance(hours=12)
    urgent_flag(agent)
    assert alert(agent) == {"sent": 0, "held": 0, "failed": 1}


def test_an_offer_is_not_dialed_once_the_booking_was_cancelled(agent, clock):
    call, job_id = urgent_booked_tomorrow(agent)
    offer(agent)
    agent.turn(call, "Actually, cancel it, it's working again.")
    assert agent.tool("cancel_appointment", call, booking_id=job_id, reason="fixed itself")["ok"]
    conn = agent.db()
    conn.execute("UPDATE bookings SET status = 'done' WHERE kind = 'book'")
    conn.close()
    assert claim(agent) is None
    assert count(agent, "SELECT result FROM outbound") == "stale"


def test_a_caller_who_says_dont_call_on_the_callback_stays_cancelled(agent, clock):
    system_callback(agent)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    claimed = claim(agent)
    placed = place(agent, claimed)
    agent.turn(placed, "Please don't call me again.")
    done(agent, claimed["outbound_id"], answered=True)
    assert count(agent, "SELECT status FROM callbacks") == "cancelled"
