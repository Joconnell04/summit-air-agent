"""A promised callback counts as done only when ServiceTitan's call log shows a call back that
resolved it (DECISIONS 010, docs/decisions/26-callback-done.md).

ServiceTitan has no outcome field on a call, so the outcome is a tag on the call. A person at the
office tags the calls they make; the server tags the callbacks it places itself, deciding from what
it saw on the call. A background job reads the call log and marks the callback done."""

import sqlite3
from datetime import timedelta

import pytest
from test_ts_jobs import claim, done, place, services
from toolkit import UNKNOWN, book, count, first_open, in_area_address

from app import jobs, outbound, owner, records
from app.fsm import FsmAdapter
from app.settings import TAG_RETRY_MINUTES
from servicetitan_mock import worldkit

RESOLVED, OPEN, NO_CONTACT = "Callback resolved", "Callback open", "No contact"
OFFICE_LINE = "(718) 555-0100"


def promise_callback(agent, caller=UNKNOWN, minutes=30, system=True) -> str:
    """A call that ends with a callback promised, by the system or by a person."""
    call = agent.start(caller)["data"]["call_id"]
    flagged = agent.tool(
        "flag_person",
        call,
        reason="Caller asked to be called back",
        callback_in_minutes=minutes,
        system_calls_back=system,
    )
    assert flagged["ok"], flagged
    agent.end(call)
    return call


def dial(agent, clock, caller=UNKNOWN) -> tuple[dict, str]:
    """A callback promised, due, queued, claimed, and the placed call started. Returns the claim
    and the placed call's id."""
    promise_callback(agent, caller)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    claimed = claim(agent)
    assert claimed and claimed["kind"] == "callback"
    return claimed, place(agent, claimed)


def finish(agent, claimed, placed) -> dict:
    """The conversation is over: the call ends on the tool server, then the voice layer reports it."""
    agent.end(placed)
    return done(agent, claimed["outbound_id"], answered=True)


def tags_on(mock, sid: str) -> list[str]:
    """Tag names on a call in the mock ServiceTitan's log, found by its sid."""
    state = mock[0].state
    conn = sqlite3.connect(state.db_path)
    rows = conn.execute(
        "SELECT t.tag_type_id FROM call_tags t JOIN calls c ON c.id = t.call_id WHERE c.sid = ?",
        (sid,),
    ).fetchall()
    names = {t["id"]: t["name"] for t in state.cfg.tag_types}
    return [names[r[0]] for r in rows]


def logged(mock, sid: str) -> tuple | None:
    conn = sqlite3.connect(mock[0].state.db_path)
    return conn.execute(
        "SELECT direction, call_type, to_number FROM calls WHERE sid = ?", (sid,)
    ).fetchone()


def outcome(agent, outbound_id: int) -> tuple[str, str]:
    row = (
        agent.db()
        .execute("SELECT outcome, why FROM call_outcomes WHERE outbound_id = ?", (outbound_id,))
        .fetchone()
    )
    return row["outcome"], row["why"]


def callback_status(agent) -> str:
    return count(agent, "SELECT status FROM callbacks")


def confirm_person_and_resolve(agent, placed: str) -> None:
    agent.turn(placed, "Hi, thanks for calling back.")
    agent.confirm_read_back(placed, in_area_address())
    assert agent.tool("end_call", placed, reason="resolved")["ok"]


def run_jobs(agent) -> dict:
    return jobs.run_once(services(agent))["summit-air"]


# ---- the server tags its own callbacks -------------------------------------------------------


def test_a_resolved_callback_is_tagged_in_servicetitan_and_then_marked_done(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    finished = finish(agent, claimed, placed)["data"]
    assert finished["outcome"] == "resolved"
    assert outcome(agent, claimed["outbound_id"])[0] == "resolved"
    # The call is in ServiceTitan's log as an outbound call to the caller, tagged resolved.
    assert logged(mock, placed) == ("Outbound", None, UNKNOWN)
    assert tags_on(mock, placed) == [RESOLVED]
    assert count(agent, "SELECT tagged FROM call_outcomes") == 1
    # Nothing marks it done until the job reads the log.
    assert callback_status(agent) == "pending"
    report = run_jobs(agent)
    assert report["callbacks_done"] == {"checked": 1, "done": 1}
    assert callback_status(agent) == "done"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE status = 'open'") == 0
    assert run_jobs(agent)["callbacks_done"]["done"] == 0, "and it stays done"


def test_the_owner_view_counts_the_callback_done_only_after_the_log_shows_it(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    finish(agent, claimed, placed)
    s = services(agent)
    today = s.tenants.default.local(clock()).date()

    def promised_done() -> tuple[int, int]:
        conn = agent.db()
        stats = owner.callbacks(conn, s.tenants.default, today)
        conn.close()
        return stats["promised"], stats["done"]

    assert promised_done() == (1, 0)
    run_jobs(agent)
    assert promised_done() == (1, 1)


@pytest.mark.parametrize(
    "voicemail, why",
    [(True, "voicemail"), (False, "no_answer")],
    ids=["voicemail", "no answer"],
)
def test_a_voicemail_or_no_answer_is_no_contact_and_never_marks_the_callback_done(
    agent, clock, mock, voicemail, why
):
    claimed, placed = dial(agent, clock)
    finished = done(agent, claimed["outbound_id"], answered=False, voicemail=voicemail)["data"]
    assert (finished["result"], finished["outcome"]) == (why, "no_contact")
    assert tags_on(mock, placed) == [NO_CONTACT]
    assert callback_status(agent) == "overdue", "a person picks it up"
    for _ in range(2):
        assert run_jobs(agent)["callbacks_done"]["done"] == 0
    assert callback_status(agent) == "overdue"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 1


def test_a_failed_call_that_never_connected_is_no_contact_and_never_marks_it_done(
    agent, clock, mock
):
    promise_callback(agent)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    claimed = claim(agent)
    # The voice layer could not place the call at all: no call was started on the tool server.
    assert done(agent, claimed["outbound_id"], answered=False)["data"]["outcome"] == "no_contact"
    assert logged(mock, claimed["call_id"]) is None, "no call happened, so none is logged"
    run_jobs(agent)
    assert callback_status(agent) == "overdue"


def test_the_wrong_person_answering_is_open_and_never_marks_it_done(agent, clock, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    claimed, placed = dial(agent, clock, maria["phone"])
    agent.say(placed, "Hi, this is Summit Air calling back. Am I speaking with Maria?")
    agent.turn(placed, "No, you have the wrong person, she's not here.")
    agent.tool(
        "end_call", placed, reason="resolved"
    )  # the agent says resolved; the server doubts it
    finished = finish(agent, claimed, placed)["data"]
    assert finished["outcome"] == "open"
    assert outcome(agent, claimed["outbound_id"])[1] == "the person was not confirmed"
    assert tags_on(mock, placed) == [OPEN]
    assert callback_status(agent) == "overdue"
    assert run_jobs(agent)["callbacks_done"]["done"] == 0
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 1


def test_a_name_check_the_person_confirms_counts_as_the_right_person(agent, clock, mock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    claimed, placed = dial(agent, clock, maria["phone"])
    agent.say(placed, "Hi, this is Summit Air calling back. Am I speaking with Maria?")
    agent.turn(placed, "Yes, this is Maria.")
    agent.tool("end_call", placed, reason="resolved")
    assert finish(agent, claimed, placed)["data"]["outcome"] == "resolved"
    assert tags_on(mock, placed) == [RESOLVED]
    run_jobs(agent)
    assert callback_status(agent) == "done"


def test_a_yes_to_a_question_that_does_not_name_the_customer_is_not_a_name_check(
    agent, clock, scenarios
):
    maria = scenarios["repeatCallerOpenJob"]
    claimed, placed = dial(agent, clock, maria["phone"])
    agent.say(placed, "Hi, this is Summit Air calling back. Is now a good time?")
    agent.turn(placed, "Yes.")
    agent.tool("end_call", placed, reason="resolved")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"]) == ("open", "the person was not confirmed")


def test_a_confirmed_person_whose_reason_was_not_handled_is_open(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Hi, thanks for calling back.")
    agent.confirm_read_back(placed, in_area_address())
    agent.turn(placed, "Actually I can't talk right now, try me tomorrow.")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"]) == (
        "open",
        "the reason for the callback was not handled",
    )
    assert tags_on(mock, placed) == [OPEN]
    assert callback_status(agent) == "overdue"


def test_the_agent_saying_resolved_is_not_enough_without_a_confirmed_person(agent, clock):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Hi, thanks for calling back.")
    agent.tool("end_call", placed, reason="resolved")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"]) == ("open", "the person was not confirmed")


def test_resolved_is_doubted_when_the_agent_also_handed_it_to_a_person(agent, clock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    agent.tool(
        "flag_person", placed, reason="Wants the manager too", callback_in_minutes=60, key="again"
    )
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"


def test_resolved_is_doubted_when_the_caller_never_spoke(agent, clock):
    claimed, placed = dial(agent, clock)
    agent.confirm_read_back(placed, in_area_address())  # needs a caller line, so use a bare one
    conn = agent.db()
    conn.execute("DELETE FROM turns WHERE call_id = ? AND speaker = 'caller'", (placed,))
    conn.close()
    agent.tool("end_call", placed, reason="resolved")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"


def test_a_booking_made_on_the_callback_is_the_reason_handled(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Thanks for calling back, I'd like to book my tune-up.")
    agent.confirm_read_back(placed, in_area_address())
    assert book(agent, placed, first_open(agent, placed))["ok"]
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"])[0] == "resolved"
    assert tags_on(mock, placed) == [RESOLVED]


@pytest.mark.parametrize(
    "kind, status, handled",
    [
        ("book", "booked", True),
        ("reschedule", "done", True),
        ("cancel", "done", True),
        ("book", "queued", False),
        ("reschedule", "failed", False),
        ("cancel", "failed", False),
    ],
)
def test_which_actions_on_the_call_count_as_the_reason_handled(agent, clock, kind, status, handled):
    _, placed = dial(agent, clock)
    conn = agent.db()
    conn.execute(
        "INSERT INTO bookings (call_id, kind, status, fsm_key, request_json, created_at, updated_at) "
        "VALUES (?, ?, ?, 'k', '{}', 'x', 'x')",
        (placed, kind, status),
    )
    assert outbound.reason_handled(conn, records.get_call(conn, placed)) is handled
    conn.close()


def test_a_call_to_a_number_other_than_the_one_that_asked_is_open(agent, clock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    conn = agent.db()
    conn.execute("UPDATE callbacks SET phone_key = '2125550000'")
    conn.close()
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"]) == (
        "open",
        "the call was not to the number that asked",
    )


def test_a_caller_who_asked_not_to_be_called_again_is_open_and_not_handed_to_anyone(
    agent, clock, mock
):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Please don't call me again.")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"
    assert callback_status(agent) == "cancelled"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 0
    run_jobs(agent)
    assert callback_status(agent) == "cancelled", "a cancelled callback is never marked done"


def test_a_stuck_call_is_settled_once_and_a_second_report_changes_nothing(agent, clock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    assert finish(agent, claimed, placed)["ok"]
    assert done(agent, claimed["outbound_id"], answered=False)["refused"] == "invalid_state"
    assert count(agent, "SELECT COUNT(*) FROM call_outcomes") == 1


# ---- the tag has to reach ServiceTitan -------------------------------------------------------


def test_a_tag_that_could_not_be_applied_is_retried_and_the_callback_then_done(
    agent, clock, mock, fsm
):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    fsm.down()
    assert finish(agent, claimed, placed)["data"]["outcome"] == "resolved"
    assert count(agent, "SELECT tagged FROM call_outcomes") == 0
    assert tags_on(mock, placed) == []
    fsm.up()
    report = run_jobs(agent)
    assert report["call_tags"] == {"tried": 1, "tagged": 1, "flagged": 0}
    assert tags_on(mock, placed) == [RESOLVED]
    assert report["callbacks_done"]["done"] == 1, "tagged first, then read, in the same run"
    assert callback_status(agent) == "done"


def test_when_servicetitan_never_shows_the_call_the_tag_is_tried_for_a_while_then_a_person_is_told(
    agent, clock, mock
):
    """A real tenant whose phone system does not log our call: no tag can land."""
    services(agent).fsm["summit-air"].log_calls_via_debug = False
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    finish(agent, claimed, placed)
    assert logged(mock, placed) is None
    attempts = "SELECT attempts FROM call_outcomes"
    first = count(agent, attempts)
    clock.advance(minutes=60)
    assert run_jobs(agent)["call_tags"]["tried"] == 1
    assert count(agent, attempts) == first + 1, "tried again within the window"
    flags = "SELECT COUNT(*) FROM flags WHERE kind = 'callback_tag_failed'"
    assert count(agent, flags) == 0
    clock.advance(minutes=TAG_RETRY_MINUTES)
    report = run_jobs(agent)["call_tags"]
    assert (report["tried"], report["flagged"]) == (0, 1), "gives up and tells a person"
    assert count(agent, flags) == 1
    assert run_jobs(agent)["call_tags"]["flagged"] == 0, "once"
    assert callback_status(agent) != "done"


def test_a_call_the_phone_system_logged_under_another_id_is_found_by_number_and_time(
    agent, clock, mock
):
    """The sid we hold may not be the one ServiceTitan holds; the outbound call to that number logged
    at that time is the same call."""
    services(agent).fsm["summit-air"].log_calls_via_debug = False
    claimed, placed = dial(agent, clock)
    office_call(mock, clock, tags=(), sid="telephony-id-we-never-saw")
    confirm_person_and_resolve(agent, placed)
    finish(agent, claimed, placed)
    assert tags_on(mock, "telephony-id-we-never-saw") == [RESOLVED]
    run_jobs(agent)
    assert callback_status(agent) == "done"


def test_a_call_logged_long_before_the_dial_is_not_taken_for_it(agent, clock, mock):
    services(agent).fsm["summit-air"].log_calls_via_debug = False
    promise_callback(agent)
    office_call(mock, clock, tags=(), sid="an-hour-ago", ago=61)
    clock.advance(minutes=31)
    jobs.run_once(services(agent))
    claimed = claim(agent)
    placed = place(agent, claimed)
    confirm_person_and_resolve(agent, placed)
    finish(agent, claimed, placed)
    assert tags_on(mock, "an-hour-ago") == []
    assert count(agent, "SELECT tagged FROM call_outcomes") == 0


# ---- when the call is judged -----------------------------------------------------------------


def test_the_voice_layer_reports_at_pickup_and_the_call_is_judged_when_it_ends(agent, clock, mock):
    """The LiveKit worker reports "answered" as soon as the line is picked up, before anyone has
    spoken. Judging it then would always find nothing, so the outcome waits for the call's end."""
    claimed, placed = dial(agent, clock)
    reported = done(agent, claimed["outbound_id"], answered=True)["data"]
    assert (reported["result"], reported["outcome"]) == ("answered", None)
    assert count(agent, "SELECT COUNT(*) FROM call_outcomes") == 0
    assert callback_status(agent) == "pending", "not handed to a person while the call goes on"
    confirm_person_and_resolve(agent, placed)
    ended = agent.end(placed)
    assert ended["ok"]
    assert outcome(agent, claimed["outbound_id"])[0] == "resolved"
    assert tags_on(mock, placed) == [RESOLVED]
    run_jobs(agent)
    assert callback_status(agent) == "done"


def test_a_call_that_never_ends_is_judged_by_the_job_once_it_has_waited_long_enough(agent, clock):
    claimed, placed = dial(agent, clock)
    done(agent, claimed["outbound_id"], answered=True)
    confirm_person_and_resolve(agent, placed)
    clock.advance(minutes=jobs.STUCK_DIALING_MINUTES - 5)
    assert run_jobs(agent)["callbacks_settled"] == 0
    clock.advance(minutes=10)
    assert run_jobs(agent)["callbacks_settled"] == 1
    assert outcome(agent, claimed["outbound_id"])[0] == "resolved"
    assert run_jobs(agent)["callbacks_settled"] == 0


def test_a_voicemail_reported_as_answered_is_still_no_contact(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    agent.end(placed)
    done(agent, claimed["outbound_id"], answered=True, voicemail=True)
    assert outcome(agent, claimed["outbound_id"])[0] == "no_contact"
    assert tags_on(mock, placed) == [NO_CONTACT]


def test_a_long_reply_to_the_name_question_is_not_a_confirmation(agent, clock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    claimed, placed = dial(agent, clock, maria["phone"])
    agent.say(placed, "Am I speaking with Maria?")
    agent.turn(placed, "Yeah, hold on, let me go and get her, she is out in the garden right now.")
    agent.tool("end_call", placed, reason="resolved")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"]) == ("open", "the person was not confirmed")


def test_a_voicemail_greeting_that_says_this_is_is_not_a_confirmation(agent, clock, scenarios):
    maria = scenarios["repeatCallerOpenJob"]
    claimed, placed = dial(agent, clock, maria["phone"])
    agent.say(placed, "Am I speaking with Maria?")
    agent.turn(placed, "This is Maria's voicemail.")
    agent.tool("end_call", placed, reason="resolved")
    finish(agent, claimed, placed)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"


def test_the_tag_names_the_customer_and_location_when_the_call_is_matched_to_one(
    agent, clock, scenarios
):
    """ServiceTitan's PUT on a call asks for the customer and a location."""
    maria = scenarios["repeatCallerOpenJob"]
    adapter = services(agent).fsm["summit-air"]
    sent = []
    original = adapter.http.request

    def spy(method, url, **kwargs):
        if method == "PUT" and "/calls/" in url:
            sent.append(kwargs["json"])
        return original(method, url, **kwargs)

    adapter.http.request = spy
    claimed, placed = dial(agent, clock, maria["phone"])
    agent.say(placed, "Hi, this is Summit Air calling back. Am I speaking with Maria?")
    agent.turn(placed, "Yes, speaking.")
    agent.tool("end_call", placed, reason="resolved")
    finish(agent, claimed, placed)
    assert len(sent) == 1
    body = sent[0]
    assert body["tag"] == RESOLVED and body["excuseMemo"] == ""
    assert body["customer"]["id"] == maria["customerId"]
    assert body["customer"]["address"]["zip"] and body["customer"]["contacts"]
    assert body["location"]["id"] == maria["locationId"]
    assert {"street", "city", "state", "zip", "country"} <= set(body["location"]["address"])


# ---- the job reads the log: calls the office made ---------------------------------------------


def office_call(
    mock, clock, phone=UNKNOWN, tags=(RESOLVED,), direction="Outbound", ago=0, sid=None
):
    """A call in ServiceTitan's log, as its phone system records one when a person at the office
    calls (or someone calls in) and puts tags on it."""
    state = mock[0].state
    conn = sqlite3.connect(state.db_path, isolation_level=None)
    when = clock() - timedelta(minutes=ago)
    if direction == "Outbound":
        worldkit.add_call(
            conn,
            state.cfg,
            when,
            OFFICE_LINE,
            None,
            None,
            None,
            "",
            200,
            phone,
            "Outbound",
            sid,
            tags,
        )
    else:
        worldkit.add_call(
            conn,
            state.cfg,
            when,
            phone,
            None,
            None,
            "Unbooked",
            "",
            200,
            OFFICE_LINE,
            "Inbound",
            sid,
            tags,
        )
    conn.close()


def test_a_call_back_from_the_office_tagged_resolved_marks_the_callback_done(agent, clock, mock):
    call = promise_callback(agent, system=False)  # a person owes this one
    clock.advance(minutes=45)
    assert run_jobs(agent)["callbacks_done"]["done"] == 0
    assert callback_status(agent) == "overdue"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'overdue_callback'") == 1
    office_call(mock, clock)
    assert run_jobs(agent)["callbacks_done"] == {"checked": 1, "done": 1}
    assert callback_status(agent) == "done"
    flags = "SELECT COUNT(*) FROM flags WHERE call_id = ? AND status = 'open'"
    assert count(agent, flags, call) == 0, "the person flag and the overdue flag are closed"


@pytest.mark.parametrize("tag", [OPEN, NO_CONTACT])
def test_an_office_call_tagged_open_or_no_contact_does_not_mark_it_done(agent, clock, mock, tag):
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    office_call(mock, clock, tags=(tag,))
    assert run_jobs(agent)["callbacks_done"]["done"] == 0
    assert callback_status(agent) in ("pending", "overdue")


def test_an_untagged_office_call_does_not_mark_it_done(agent, clock, mock):
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    office_call(mock, clock, tags=())
    assert run_jobs(agent)["callbacks_done"]["done"] == 0


def test_a_resolved_call_from_before_the_promise_does_not_count(agent, clock, mock):
    office_call(mock, clock, ago=120)  # tagged resolved, two hours before the promise
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    assert run_jobs(agent)["callbacks_done"]["done"] == 0


def test_an_inbound_call_or_a_call_to_another_number_does_not_count(agent, clock, mock):
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    office_call(mock, clock, direction="Inbound")
    office_call(mock, clock, phone="(212) 555-0001")
    assert run_jobs(agent)["callbacks_done"]["done"] == 0


def test_one_resolved_call_settles_every_open_callback_promised_to_that_number(agent, clock, mock):
    promise_callback(agent, system=False)
    clock.advance(minutes=10)
    promise_callback(agent, system=False)
    clock.advance(minutes=10)
    office_call(mock, clock)
    assert run_jobs(agent)["callbacks_done"]["done"] == 2


def test_the_job_leaves_callbacks_alone_while_servicetitan_is_down(agent, clock, mock, fsm):
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    office_call(mock, clock)
    fsm.down()
    assert run_jobs(agent)["callbacks_done"]["done"] == 0
    fsm.up()
    assert run_jobs(agent)["callbacks_done"]["done"] == 1


def test_confirm_callbacks_reads_the_log_through_the_adapter_only(agent, clock, mock):
    """The adapter has the calls_to read the job uses, and the job needs nothing of the mock."""
    assert callable(FsmAdapter.calls_to)
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    office_call(mock, clock, sid="office-1")
    adapter = services(agent).fsm["summit-air"]
    found = adapter.calls_to(UNKNOWN, clock() - timedelta(hours=1))
    assert [c["leadCall"]["sid"] for c in found] == ["office-1"]
    assert [t["typeName"] for t in found[0]["leadCall"]["tags"]] == [RESOLVED]


def test_a_resolved_call_logged_before_the_first_run_after_the_due_time_leaves_no_overdue_flag(
    agent, clock, mock
):
    promise_callback(agent, system=False)
    clock.advance(minutes=45)  # past due, and no run has noticed yet
    office_call(mock, clock)
    report = jobs.run_once(services(agent))
    assert report["summit-air"]["callbacks_done"]["done"] == 1
    assert report["overdue_callbacks"] == [], "confirmed before overdue is flagged"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'overdue_callback'") == 0


def test_a_backlog_of_unreachable_callbacks_does_not_starve_a_newer_one(agent, clock, mock):
    call = promise_callback(agent, system=False)
    conn = agent.db()
    for n in range(jobs.CALLBACK_CHECKS_PER_RUN + 10):
        conn.execute(
            "INSERT INTO callbacks (call_id, phone, phone_key, due_at, owner, reason, status, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'office', 'old', 'overdue', ?, ?)",
            (
                call,
                f"(917) 555-{n:04d}",
                f"917555{n:04d}",
                "2026-11-01T10:00:00Z",
                "2026-11-01T09:00:00Z",
                "2026-11-01T10:00:00Z",
            ),
        )
    conn.close()
    newest = promise_callback(agent, caller="(212) 555-0777", system=False)
    clock.advance(minutes=5)
    office_call(mock, clock, phone="(212) 555-0777")
    run_jobs(agent)
    run_jobs(agent)
    status = count(agent, "SELECT status FROM callbacks WHERE call_id = ?", newest)
    assert status == "done", "everything is looked at within a couple of runs"


def test_a_callback_older_than_the_lookback_is_no_longer_looked_for(agent, clock, mock):
    promise_callback(agent, system=False)
    clock.advance(days=jobs.CALLBACK_LOOKBACK_DAYS + 1)
    office_call(mock, clock)
    assert run_jobs(agent)["callbacks_done"]["checked"] == 0


def test_a_reply_the_job_does_not_understand_does_not_stop_the_jobs_after_it(agent, clock):
    promise_callback(agent, system=False)
    clock.advance(minutes=5)
    adapter = services(agent).fsm["summit-air"]

    def broken(phone, since=None):
        return [{"unexpected": "shape"}]  # not what ServiceTitan returns

    adapter.calls_to = broken
    report = run_jobs(agent)
    assert "error" in report["callbacks_done"]
    assert {"kept", "alerts", "purged"} <= set(report), "the jobs after it still ran"


def test_a_tenant_must_name_all_three_callback_tags(tmp_path):
    import shutil

    from toolkit import TENANT_DIR

    from app.tenant import Tenant

    folder = tmp_path / "other"
    shutil.copytree(TENANT_DIR, folder)
    text = (folder / "agent.yaml").read_text()
    (folder / "agent.yaml").write_text(text.replace("    no_contact: No contact\n", ""))
    with pytest.raises(ValueError, match="no_contact"):
        Tenant.load(folder)
