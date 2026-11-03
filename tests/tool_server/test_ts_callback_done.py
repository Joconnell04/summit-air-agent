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
    finished = done(agent, claimed["outbound_id"], answered=True)["data"]
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
    done(agent, claimed["outbound_id"], answered=True)
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
    finished = done(agent, claimed["outbound_id"], answered=True)["data"]
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
    assert done(agent, claimed["outbound_id"], answered=True)["data"]["outcome"] == "resolved"
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
    done(agent, claimed["outbound_id"], answered=True)
    assert outcome(agent, claimed["outbound_id"]) == ("open", "the person was not confirmed")


def test_a_confirmed_person_whose_reason_was_not_handled_is_open(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Hi, thanks for calling back.")
    agent.confirm_read_back(placed, in_area_address())
    agent.turn(placed, "Actually I can't talk right now, try me tomorrow.")
    done(agent, claimed["outbound_id"], answered=True)
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
    done(agent, claimed["outbound_id"], answered=True)
    assert outcome(agent, claimed["outbound_id"]) == ("open", "the person was not confirmed")


def test_resolved_is_doubted_when_the_agent_also_handed_it_to_a_person(agent, clock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    agent.tool(
        "flag_person", placed, reason="Wants the manager too", callback_in_minutes=60, key="again"
    )
    done(agent, claimed["outbound_id"], answered=True)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"


def test_resolved_is_doubted_when_the_caller_never_spoke(agent, clock):
    claimed, placed = dial(agent, clock)
    agent.confirm_read_back(placed, in_area_address())  # needs a caller line, so use a bare one
    conn = agent.db()
    conn.execute("DELETE FROM turns WHERE call_id = ? AND speaker = 'caller'", (placed,))
    conn.close()
    agent.tool("end_call", placed, reason="resolved")
    done(agent, claimed["outbound_id"], answered=True)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"


def test_a_booking_made_on_the_callback_is_the_reason_handled(agent, clock, mock):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Thanks for calling back, I'd like to book my tune-up.")
    agent.confirm_read_back(placed, in_area_address())
    assert book(agent, placed, first_open(agent, placed))["ok"]
    done(agent, claimed["outbound_id"], answered=True)
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
    done(agent, claimed["outbound_id"], answered=True)
    assert outcome(agent, claimed["outbound_id"]) == (
        "open",
        "the call was not to the number that asked",
    )


def test_a_caller_who_asked_not_to_be_called_again_is_open_and_not_handed_to_anyone(
    agent, clock, mock
):
    claimed, placed = dial(agent, clock)
    agent.turn(placed, "Please don't call me again.")
    done(agent, claimed["outbound_id"], answered=True)
    assert outcome(agent, claimed["outbound_id"])[0] == "open"
    assert callback_status(agent) == "cancelled"
    assert count(agent, "SELECT COUNT(*) FROM flags WHERE kind = 'callback_needs_person'") == 0
    run_jobs(agent)
    assert callback_status(agent) == "cancelled", "a cancelled callback is never marked done"


def test_a_stuck_call_is_settled_once_and_a_second_report_changes_nothing(agent, clock):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    assert done(agent, claimed["outbound_id"], answered=True)["ok"]
    assert done(agent, claimed["outbound_id"], answered=False)["refused"] == "invalid_state"
    assert count(agent, "SELECT COUNT(*) FROM call_outcomes") == 1


# ---- the tag has to reach ServiceTitan -------------------------------------------------------


def test_a_tag_that_could_not_be_applied_is_retried_and_the_callback_then_done(
    agent, clock, mock, fsm
):
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    fsm.down()
    assert done(agent, claimed["outbound_id"], answered=True)["data"]["outcome"] == "resolved"
    assert count(agent, "SELECT tagged FROM call_outcomes") == 0
    assert tags_on(mock, placed) == []
    fsm.up()
    report = run_jobs(agent)
    assert report["call_tags"] == {"tried": 1, "tagged": 1}
    assert tags_on(mock, placed) == [RESOLVED]
    assert report["callbacks_done"]["done"] == 1, "tagged first, then read, in the same run"
    assert callback_status(agent) == "done"


def test_when_servicetitan_never_shows_the_call_the_tag_is_tried_a_few_times_and_it_is_not_done(
    agent, clock, mock
):
    """A real tenant whose phone system does not log our call: no tag can land."""
    services(agent).fsm["summit-air"].log_calls_via_debug = False
    claimed, placed = dial(agent, clock)
    confirm_person_and_resolve(agent, placed)
    done(agent, claimed["outbound_id"], answered=True)
    assert logged(mock, placed) is None
    for _ in range(8):
        run_jobs(agent)
    assert count(agent, "SELECT attempts FROM call_outcomes") == 5, "gives up after five tries"
    assert callback_status(agent) != "done"


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
    assert [c["sid"] for c in found] == ["office-1"]
    assert [t["typeName"] for t in found[0]["tags"]] == [RESOLVED]
