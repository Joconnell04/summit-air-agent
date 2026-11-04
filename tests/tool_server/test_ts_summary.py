"""The technician's summary after a booked call (spec S-16, app/summary.py), with a fake model: what
the model is given, the checks on what it writes, the pinned job note, failure and retry, and the
calls that get none."""

import json
import logging
from datetime import timedelta

import pytest
from test_ts_safety import GAS_LINES
from toolkit import (
    UNKNOWN,
    Agent,
    Clock,
    book,
    first_open,
    in_area_address,
    make_server,
    ready_to_book,
)

from app import jobs, records, summary
from app.db import tx
from app.fsm import FsmRejected, FsmUnavailable
from app.settings import QUEUE_MAX_ATTEMPTS

TOKEN = "owner-token"
DRAFT = {
    "problem": "No heat since last night, radiators are cold",
    "watch_for": "",
    "access": "Caller will be home",
    "recent_work": "",
    "told": "Arrival window as given; no price quoted",
}


class FakeModel:
    """Answers with the replies given, one per call; an exception in the list is raised instead."""

    def __init__(self, *replies):
        self.replies = list(replies) or [DRAFT]
        self.records: list[dict] = []
        self.systems: list[str] = []

    def write(self, system: str, record: str) -> tuple[dict, float]:
        self.systems.append(system)
        self.records.append(json.loads(record))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply, 0.004


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0], owner_token=TOKEN), clock)


def services(agent: Agent):
    return agent.app.state.services


def adapter(agent: Agent):
    return services(agent).fsm["summit-air"]


def run(agent: Agent, model, **kwargs) -> dict:
    conn = agent.db()
    try:
        return summary.run(
            conn, services(agent).tenants.default, adapter(agent), model, agent.clock(), **kwargs
        )
    finally:
        conn.close()


def booked_call(agent: Agent, caller: str = UNKNOWN, **details) -> tuple[str, int]:
    call = ready_to_book(agent, caller)
    account = None if caller == UNKNOWN else "caller"
    booked = book(agent, call, first_open(agent, call), account=account, **details)
    assert booked["ok"], booked
    agent.end(call)
    return call, booked["data"]["booking_id"]


def status(agent: Agent, call: str) -> str:
    conn = agent.db()
    try:
        return conn.execute("SELECT summary_status FROM calls WHERE id = ?", (call,)).fetchone()[0]
    finally:
        conn.close()


def stored(agent: Agent, call: str) -> dict:
    conn = agent.db()
    try:
        return dict(conn.execute("SELECT * FROM summaries WHERE call_id = ?", (call,)).fetchone())
    finally:
        conn.close()


def pinned(agent: Agent, job_id: int) -> list[dict]:
    return [n for n in adapter(agent).job_notes(job_id) if n["isPinned"]]


# ---- what the model is given ------------------------------------------------------------------


def test_the_model_gets_the_booking_the_window_and_the_transcript_but_no_phone_number(agent):
    model = FakeModel()
    booked_call(agent, arrival_notes="Buzzer 3B, parking on the street")
    run(agent, model)
    rec = model.records[0]
    assert model.systems[0] == summary.PROMPT.read_text()
    assert rec["issue"] == "No heat since last night, radiators are cold"
    assert rec["arrival_notes"] == "Buzzer 3B, parking on the street"
    assert rec["approver"] == "Caller, the owner"
    assert rec["arrival_window"].startswith("Monday 2026-11-02")
    assert "Caller: Hi, the heat's out in my house." in rec["transcript"]
    assert rec["completed_jobs"] == [] and rec["customer_notes"] == []
    assert "555-0999" not in json.dumps({k: v for k, v in rec.items() if k != "transcript"})


def test_the_model_gets_the_customers_recent_work_and_office_notes(agent, scenarios):
    known = scenarios["jobDoneLastWeek"]
    model = FakeModel()
    booked_call(agent, known["phone"])
    run(agent, model)
    rec = model.records[0]
    # The job just booked is not completed, so it isn't recent work.
    assert [j["summary"] for j in rec["completed_jobs"]] == ["AC not cooling. Replaced capacitor."]
    assert rec["equipment"], "the house's installed equipment"
    # The note asking to skip a gate is passed as data; the prompt says it is not an instruction.
    assert scenarios["customerNotes"]["noteThatAsksToSkipAGate"]["text"] in rec["customer_notes"]


# ---- the checks on what the model writes --------------------------------------------------------


def test_the_summary_is_a_few_short_labelled_lines_pinned_to_the_job(agent):
    call, job_id = booked_call(agent)
    assert run(agent, FakeModel())["done"] == 1
    notes = pinned(agent, job_id)
    assert len(notes) == 1
    text = notes[0]["text"].splitlines()
    assert text[0] == summary.HEADER
    assert text[1] == "Problem: No heat since last night, radiators are cold"
    assert text[-1] == "Told: Arrival window as given; no price quoted"
    assert 3 <= len(text) - 1 <= 5
    assert status(agent, call) == "done"


def test_every_line_is_cut_to_the_length_limit(agent):
    long = (
        "The caller says the radiators upstairs are cold and the ones downstairs barely warm " * 4
    )
    call, _ = booked_call(agent)
    run(agent, FakeModel({**DRAFT, "problem": long}))
    lines = json.loads(stored(agent, call)["lines_json"])
    problem = dict(lines)["Problem"]
    assert len(problem) <= summary.MAX_LINE_CHARS and problem.endswith("...")
    assert all(len(t) <= summary.MAX_LINE_CHARS for _, t in lines)


def test_a_line_with_nothing_in_the_record_behind_it_is_dropped(agent):
    """A new customer has no history, so a recent-work line is the model filling the field."""
    draft = {**DRAFT, "recent_work": "Boiler serviced last spring", "watch_for": "Elderly caller"}
    call, job_id = booked_call(agent)
    run(agent, FakeModel(draft))
    text = pinned(agent, job_id)[0]["text"]
    assert "Recent work" not in text and "Boiler serviced" not in text
    assert "Watch for" not in text, "a Normal priority call with no reasons has nothing to watch"
    dropped = {d["field"]: d["reason"] for d in json.loads(stored(agent, call)["dropped_json"])}
    assert set(dropped) == {"recent_work", "watch_for"}


def test_a_line_with_a_number_the_record_does_not_hold_is_dropped(agent):
    draft = {**DRAFT, "access": "Side gate code 9921, dog in the yard"}
    call, job_id = booked_call(agent, arrival_notes="Dog in the yard")
    run(agent, FakeModel(draft))
    text = pinned(agent, job_id)[0]["text"]
    assert "9921" not in text and "Access" not in text
    [dropped] = json.loads(stored(agent, call)["dropped_json"])
    assert dropped["field"] == "access" and "9921" in dropped["reason"]


def test_a_number_from_the_record_stays(agent, scenarios):
    known = scenarios["repeatCallerOpenJob"]
    draft = {**DRAFT, "access": "Side gate code 4417, friendly dog"}
    call = ready_to_book(agent, known["phone"])
    booked = book(
        agent, call, first_open(agent, call, "no_heat"), job_type="no_heat", account="caller"
    )
    assert booked["ok"], booked
    agent.end(call)
    run(agent, FakeModel(draft))
    assert (
        "Access: Side gate code 4417, friendly dog"
        in pinned(agent, booked["data"]["booking_id"])[0]["text"]
    )


def test_a_line_with_a_phone_number_is_dropped(agent):
    """The number is in the record (the caller said it), so only the phone check stops it."""
    call = ready_to_book(agent)
    agent.turn(call, "If nobody answers the door, call 212 555 0999.")
    booked = book(agent, call, first_open(agent, call))
    agent.end(call)
    run(agent, FakeModel({**DRAFT, "access": "No answer at the door: call 212 555 0999"}))
    assert "0999" not in pinned(agent, booked["data"]["booking_id"])[0]["text"]
    [dropped] = json.loads(stored(agent, call)["dropped_json"])
    assert dropped["field"] == "access" and dropped["reason"] == "a phone number"


def test_problem_and_told_come_from_the_record_when_the_model_leaves_them_out(agent):
    call, _ = booked_call(agent)
    run(agent, FakeModel({k: "" for k in summary.FIELDS}))
    lines = dict(json.loads(stored(agent, call)["lines_json"]))
    assert lines["Problem"] == "No heat since last night, radiators are cold, since last night"
    assert lines["Told"].startswith("Arrival window Monday 2026-11-02")


def test_the_told_fallback_keeps_a_promised_callback(agent):
    call = ready_to_book(agent)
    assert book(agent, call, first_open(agent, call))["ok"]
    conn = agent.db()
    try:
        with tx(conn):
            records.add_callback(
                conn,
                services(agent).tenants.default,
                records.get_call(conn, call),
                agent.clock() + timedelta(hours=2),
                "Office to confirm the part",
                agent.clock(),
            )
    finally:
        conn.close()
    agent.end(call)
    # A Told line with a number the record doesn't hold is dropped, so the fallback stands in.
    run(agent, FakeModel({**DRAFT, "told": "Window 9 to 13"}))
    told = dict(json.loads(stored(agent, call)["lines_json"]))["Told"]
    assert told.startswith("Arrival window Monday 2026-11-02")
    assert "call back by 11:00 AM (Office to confirm the part)" in told


# ---- the write to ServiceTitan ------------------------------------------------------------------


def test_the_summary_is_added_and_nothing_on_the_job_is_changed(agent):
    call, job_id = booked_call(agent)
    agent.tool("add_note", call, text="Prefers a text before arrival", attach_to="job")
    before_job = adapter(agent).job(job_id)
    before_notes = adapter(agent).job_notes(job_id)
    run(agent, FakeModel())
    after_notes = adapter(agent).job_notes(job_id)
    assert after_notes[: len(before_notes)] == before_notes
    assert len(after_notes) == len(before_notes) + 1
    assert adapter(agent).job(job_id)["summary"] == before_job["summary"]
    run(agent, FakeModel())
    assert len(adapter(agent).job_notes(job_id)) == len(after_notes), "written once"


def test_a_note_whose_answer_was_lost_is_not_added_twice(agent, fsm):
    call, job_id = booked_call(agent)
    real = adapter(agent).add_note

    def add_note(*args, **kwargs):
        real(*args, **kwargs)
        raise summary.FsmUnavailable("add_note: ReadTimeout")

    adapter(agent).add_note = add_note
    model = FakeModel()
    assert run(agent, model)["retry"] == 1
    assert status(agent, call) == "written"
    del adapter(agent).add_note
    assert run(agent, model)["done"] == 1
    assert len(pinned(agent, job_id)) == 1
    assert len(model.records) == 1, "a written summary is sent again without asking the model"


def test_a_rejected_history_read_gives_up_without_asking_the_model(agent, monkeypatch):
    call, job_id = booked_call(agent)

    def notes(customer_id):
        raise FsmRejected(403, "unauthorized", "no access to notes")

    monkeypatch.setattr(adapter(agent), "notes", notes)
    model = FakeModel()
    assert run(agent, model)["failed"] == 1
    assert model.records == [] and status(agent, call) == "failed"
    assert "unauthorized" in stored(agent, call)["last_error"]
    assert pinned(agent, job_id) == []


def test_a_history_read_that_fails_uses_no_model_attempt(agent, monkeypatch):
    call, job_id = booked_call(agent)

    def notes(customer_id):
        raise FsmUnavailable("notes: ConnectError")

    monkeypatch.setattr(adapter(agent), "notes", notes)
    model = FakeModel()
    for _ in range(summary.MODEL_ATTEMPTS + 1):
        assert run(agent, model)["retry"] == 1
    assert model.records == [] and status(agent, call) == "pending"
    assert stored(agent, call)["model_attempts"] == 0
    monkeypatch.undo()
    assert run(agent, model)["done"] == 1
    assert len(pinned(agent, job_id)) == 1


def test_a_rejected_write_gives_up_and_keeps_the_written_lines(agent, monkeypatch):
    call, job_id = booked_call(agent)

    def add_note(*args, **kwargs):
        raise FsmRejected(404, "not_found", "Job not found")

    monkeypatch.setattr(adapter(agent), "add_note", add_note)
    assert run(agent, FakeModel())["failed"] == 1
    assert status(agent, call) == "failed"
    assert json.loads(stored(agent, call)["lines_json"]), "the lines stay for the call page"
    assert pinned(agent, job_id) == []


def test_servicetitan_down_is_retried_then_given_up_without_asking_the_model_again(
    agent, monkeypatch
):
    call, job_id = booked_call(agent)

    def add_note(*args, **kwargs):
        raise FsmUnavailable("add_note: ConnectError")

    monkeypatch.setattr(adapter(agent), "add_note", add_note)
    model = FakeModel()
    results = [run(agent, model) for _ in range(QUEUE_MAX_ATTEMPTS)]
    assert all(r["retry"] == 1 for r in results[:-1]) and results[-1]["failed"] == 1
    assert len(model.records) == 1
    row = stored(agent, call)
    assert row["post_attempts"] == QUEUE_MAX_ATTEMPTS and status(agent, call) == "failed"
    assert pinned(agent, job_id) == []


# ---- failure, retry and the calls that get none ---------------------------------------------------


def test_a_failed_model_call_is_retried_once_on_the_next_run(agent):
    call, job_id = booked_call(agent)
    model = FakeModel(TimeoutError("the model timed out"), DRAFT)
    assert run(agent, model)["retry"] == 1
    assert status(agent, call) == "pending" and pinned(agent, job_id) == []
    assert "timed out" in stored(agent, call)["last_error"]
    assert run(agent, model)["done"] == 1
    assert len(pinned(agent, job_id)) == 1


def test_after_two_failures_the_job_keeps_its_notes_and_the_failure_is_logged(agent, caplog):
    call, job_id = booked_call(agent)
    notes = adapter(agent).job_notes(job_id)
    model = FakeModel(RuntimeError("overloaded"))
    with caplog.at_level(logging.WARNING, logger="app.summary"):
        assert run(agent, model)["retry"] == 1
        assert run(agent, model)["failed"] == 1
        assert run(agent, model) == {"done": 0, "skipped": 0, "waiting": 0, "retry": 0, "failed": 0}
    assert len(model.records) == 2
    assert status(agent, call) == "failed"
    assert adapter(agent).job_notes(job_id) == notes
    assert any(r.levelname == "ERROR" and call in r.getMessage() for r in caplog.records)


def test_a_safety_call_with_no_booking_gets_no_summary(agent):
    call = agent.start()["data"]["call_id"]
    agent.turn(call, "I smell gas in the kitchen.")
    agent.say(call, GAS_LINES)
    agent.end(call, "caller_hangup")
    model = FakeModel()
    assert run(agent, model)["skipped"] == 1
    assert status(agent, call) == "skipped" and model.records == []


def test_a_call_that_booked_nothing_gets_no_summary(agent):
    call = ready_to_book(agent)
    agent.end(call)
    model = FakeModel()
    assert run(agent, model)["skipped"] == 1 and model.records == []


def test_a_queued_booking_waits_for_the_fsm_before_its_summary(agent, fsm):
    call = ready_to_book(agent)
    slot = first_open(agent, call)
    fsm.down()
    assert book(agent, call, slot)["data"]["queued"]
    agent.end(call)
    model = FakeModel()
    assert run(agent, model)["waiting"] == 1 and model.records == []
    assert status(agent, call) == "pending"


def test_a_call_older_than_a_day_is_skipped(agent, clock: Clock):
    call, job_id = booked_call(agent)
    clock.advance(hours=25)
    model = FakeModel()
    assert run(agent, model)["skipped"] == 1
    assert status(agent, call) == "skipped" and model.records == []
    assert pinned(agent, job_id) == []


def test_a_run_writes_at_most_its_cap_and_leaves_the_rest_for_the_next(agent):
    calls = [
        booked_call(agent, f"(212) 555-07{n:02d}") for n in range(summary.SUMMARIES_PER_RUN + 1)
    ]
    nothing_booked = ready_to_book(agent, address=in_area_address(9))
    agent.end(nothing_booked)
    model = FakeModel()
    first = run(agent, model)
    assert first["done"] == summary.SUMMARIES_PER_RUN and first["waiting"] == 2
    assert status(agent, calls[-1][0]) == "pending", "the newest booked call waits"
    # Past the cap the rest aren't read at all, so even a call with no booking isn't skipped yet.
    assert status(agent, nothing_booked) == "pending"
    second = run(agent, model)
    assert second["done"] == 1 and second["skipped"] == 1


def test_a_run_starts_no_new_summary_once_its_time_is_used_up(agent):
    calls = [booked_call(agent, f"(212) 555-07{n:02d}") for n in range(3)]
    elapsed = [0.0]

    class SlowModel(FakeModel):
        def write(self, system, record):
            elapsed[0] += summary.RUN_SECONDS * 0.6
            return super().write(system, record)

    counts = run(agent, SlowModel(), timer=lambda: elapsed[0])
    assert counts["done"] == 2 and counts["waiting"] == 1
    assert status(agent, calls[2][0]) == "pending"


def test_the_jobs_loop_writes_the_summary_last(agent):
    _, job_id = booked_call(agent)
    services(agent).summary_model = FakeModel()
    report = jobs.run_once(services(agent))
    assert report["summit-air"]["summaries"]["done"] == 1
    assert list(report["summit-air"])[-1] == "summaries"
    assert len(pinned(agent, job_id)) == 1


def test_summaries_are_off_without_the_switch():
    from app.settings import Settings

    assert Settings().summaries is False


# ---- the owner view ------------------------------------------------------------------------------


def test_the_call_page_shows_the_summary_beside_the_job(agent):
    call, _ = booked_call(agent, arrival_notes="Buzzer 3B")
    agent.client.get(f"/owner?token={TOKEN}")
    html = agent.client.get(f"/owner/calls/{call}").text
    assert "For the technician" in html and "Written shortly after the call." in html
    run(agent, FakeModel({**DRAFT, "access": "Buzzer 3B"}))
    html = agent.client.get(f"/owner/calls/{call}").text
    assert "Pinned to the job notes" in html
    assert "<dt>Access</dt><dd>Buzzer 3B</dd>" in html


def test_the_call_page_shows_nothing_for_a_call_with_no_booking(agent):
    call = ready_to_book(agent, address=in_area_address(2))
    agent.end(call)
    run(agent, FakeModel())
    agent.client.get(f"/owner?token={TOKEN}")
    assert "For the technician" not in agent.client.get(f"/owner/calls/{call}").text


# ---- the model client ------------------------------------------------------------------------------


class FakeClient:
    """Stands in for anthropic.Anthropic: records the request, answers with one message."""

    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.sent: dict = {}
        self.text, self.stop_reason = text, stop_reason
        self.messages = self

    def create(self, **params):
        self.sent = params
        block = type("Block", (), {"type": "text", "text": self.text})
        usage = type("Usage", (), {"input_tokens": 2000, "output_tokens": 500})
        return type(
            "Message", (), {"content": [block], "stop_reason": self.stop_reason, "usage": usage}
        )


def test_the_client_uses_the_post_call_profile_with_thinking_and_the_schema(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = summary.model_from_config()
    cfg = model.cfg
    client = FakeClient(json.dumps(DRAFT))
    model.client = client
    fields, cost = model.write("system", "{}")
    assert fields == DRAFT
    assert client.sent["model"] == cfg["model"]
    assert client.sent["thinking"] == {"type": "adaptive"}
    assert client.sent["output_config"]["format"]["schema"] == summary.SCHEMA
    assert cost == pytest.approx(
        (2000 * cfg["price"]["input"] + 500 * cfg["price"]["output"]) / 1e6
    )


def test_a_reply_cut_short_is_a_failure_not_a_summary():
    model = summary.AnthropicSummaryModel(
        {"model": "m", "thinking": True, "price": {"input": 1, "output": 1}},
        "ANTHROPIC_API_KEY",
        FakeClient('{"problem": "No h', stop_reason="max_tokens"),
    )
    with pytest.raises(RuntimeError):
        model.write("system", "{}")


def test_without_a_key_summaries_stay_off(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert summary.model_from_config() is None


def test_a_shared_deadline_already_past_starts_no_summary(agent):
    booked_call(agent)
    counts = run(agent, FakeModel(), timer=lambda: 100.0, deadline=50.0)
    assert counts["done"] == 0 and counts["waiting"] == 1


def test_the_summary_read_uses_the_index_in_order_without_a_sort(agent):
    conn = agent.db()
    try:
        plan = " ".join(
            row[-1]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM calls WHERE tenant = ? AND summary_status = ? "
                "ORDER BY ended_at, id LIMIT 200",
                ("summit-air", "pending"),
            )
        )
    finally:
        conn.close()
    assert "ix_calls_summary" in plan, plan
    assert "SCAN calls" not in plan and "TEMP B-TREE" not in plan, plan
