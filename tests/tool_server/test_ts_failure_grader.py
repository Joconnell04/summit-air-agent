"""The failure grader (issue #178, app/failure_grader.py) with a fake model: what the model reads,
the server's checks on its answer, the cache, the grader flag (never emailed), the daily schedule,
and the owner view and bench controls."""

import json
from datetime import timedelta

import httpx
import pytest
from toolkit import Agent, make_server, sign_in

from app import checks_view, jobs, records
from app import failure_grader as grader
from app.db import tx

STYLE_ONLY = {"failure": False, "kind": "none", "severity": "none", "reason": "", "evidence": []}
CUT_OFF = {
    "failure": True,
    "kind": "hung_up",
    "severity": "serious",
    "reason": "The agent ended the call while the caller was still asking about a callback.",
    "evidence": ["Wait, can someone call me back about the bill?"],
}


class FakeModel:
    """Answers with the replies given, the last one repeated; keeps what it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies) or [STYLE_ONLY]
        self.records: list[dict] = []

    def grade(self, system: str, record: str) -> tuple[dict, float]:
        assert "true failure" in system
        self.records.append(json.loads(record))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply, 0.05


class FakeTranscripts:
    def __init__(self, calls: dict[str, dict]):
        self.calls = calls
        self.asked: list[str] = []

    def get(self, retell_id: str) -> dict:
        self.asked.append(retell_id)
        return self.calls[retell_id]


def inline(fn):
    fn()


@pytest.fixture
def agent(tmp_path, clock, mock):
    agent = Agent(make_server(tmp_path, clock, mock[0]), clock)
    services = agent.app.state.services
    services.grader_model = FakeModel()
    services.grader_transcripts = None
    services.grader_runner = inline
    return agent


def services(agent):
    return agent.app.state.services


def tenant(agent):
    return services(agent).tenants.default


def cut_off_call(agent: Agent, call_id: str | None = None) -> str:
    call_id = agent.start(call_id=call_id)["data"]["call_id"]
    agent.turn(call_id, "Hi, my heat is fine, I just have a billing question.")
    agent.say(call_id, "Thanks for calling. Goodbye.")
    agent.turn(call_id, "Wait, can someone call me back about the bill?")
    agent.end(call_id)
    return call_id


def grade(agent, call_id, model=None, force=False, transcripts=None):
    conn = agent.db()
    try:
        return grader.grade_call(conn, tenant(agent), model or services(agent).grader_model,
                                 transcripts, call_id, agent.clock(), force)
    finally:
        conn.close()


def grader_flags(agent, call_id):
    conn = agent.db()
    try:
        return conn.execute(
            "SELECT * FROM flags WHERE call_id = ? AND kind = 'grader'", (call_id,)
        ).fetchall()
    finally:
        conn.close()


# ---- the server's decision on the answer -----------------------------------------------------


RECORD = {
    "transcript": ["caller: Wait, can someone call me back about the bill?", "agent: Goodbye."],
    "records": {"callbacks": [{"due": "Fri Nov 6, 9:00 AM"}]},
}


def test_a_failure_with_evidence_from_the_call_is_kept():
    kept = grader.verdict(CUT_OFF, RECORD)
    assert kept["failure"] and kept["kind"] == "hung_up" and kept["dropped"] is None


def test_evidence_is_matched_ignoring_case_punctuation_labels_and_ellipses():
    corpus = grader._words(" ".join(RECORD["transcript"]))
    assert grader.evidence_found("Caller: wait -- can someone call me back...the bill", corpus)
    assert grader.evidence_found("Fri Nov 6, 9:00 AM", grader._words(json.dumps(RECORD["records"])))
    assert not grader.evidence_found("Can someone call me back tomorrow?", corpus)
    assert not grader.evidence_found("...", corpus)


@pytest.mark.parametrize(
    "change, why",
    [
        ({"evidence": ["I never said goodbye, you hung up on me"]}, "evidence not found"),
        ({"evidence": []}, "no evidence"),
        ({"kind": "rude"}, "no kind or severity"),
        ({"severity": "none"}, "no kind or severity"),
        ({"reason": "  "}, "no reason"),
    ],
)
def test_a_failure_the_server_cannot_trace_to_the_call_is_dropped(change, why):
    kept = grader.verdict({**CUT_OFF, **change}, RECORD)
    assert not kept["failure"]
    assert kept["dropped"].startswith(why)


def test_no_failure_is_kept_as_none():
    kept = grader.verdict(STYLE_ONLY, RECORD)
    assert not kept["failure"] and kept["dropped"] is None


# ---- grading a call ----------------------------------------------------------------------------


def test_a_failure_adds_one_grader_flag_and_is_cached(agent):
    call_id = cut_off_call(agent)
    model = FakeModel(CUT_OFF)
    first = grade(agent, call_id, model)
    assert first["failure"] == 1 and first["fresh"] and first["cost_usd"] == 0.05
    flags = grader_flags(agent, call_id)
    assert len(flags) == 1
    assert flags[0]["priority"] == "High"
    assert flags[0]["reason"].startswith("Cut off: The agent ended the call")
    again = grade(agent, call_id, model)
    assert not again["fresh"] and len(model.records) == 1
    forced = grade(agent, call_id, model, force=True)
    assert forced["fresh"] and len(model.records) == 2
    assert len(grader_flags(agent, call_id)) == 1
    assert forced["cost_usd"] == pytest.approx(0.10)


def test_critical_is_urgent(agent):
    call_id = cut_off_call(agent)
    grade(agent, call_id, FakeModel({**CUT_OFF, "severity": "critical"}))
    assert grader_flags(agent, call_id)[0]["priority"] == "Urgent"


def test_a_style_only_call_gets_no_flag(agent):
    call_id = cut_off_call(agent)
    result = grade(agent, call_id, FakeModel(STYLE_ONLY))
    assert result["failure"] == 0 and grader_flags(agent, call_id) == []


def test_a_model_error_is_stored_and_graded_again_next_time(agent):
    call_id = cut_off_call(agent)
    model = FakeModel(RuntimeError("overloaded"), STYLE_ONLY)
    first = grade(agent, call_id, model)
    assert first["error"].startswith("RuntimeError")
    second = grade(agent, call_id, model)
    assert second["fresh"] and second["error"] is None


def test_a_call_in_progress_is_not_graded(agent):
    call_id = agent.start()["data"]["call_id"]
    with pytest.raises(ValueError):
        grade(agent, call_id)


def test_the_record_holds_the_turns_and_the_rows(agent):
    call_id = cut_off_call(agent)
    conn = agent.db()
    try:
        with tx(conn):
            records.add_flag(conn, call_id, "person", "Billing question", agent.clock())
    finally:
        conn.close()
    model = FakeModel()
    grade(agent, call_id, model)
    record = model.records[0]
    assert "caller: Wait, can someone call me back about the bill?" in record["transcript"]
    assert record["records"]["flags"] == [
        {"kind": "person", "reason": "Billing question", "priority": "Normal"}
    ]
    assert record["call"]["started"].startswith("Mon Nov 2")
    assert record["records"]["caller_on_do_not_call_list"] is False


def test_a_retell_call_is_read_from_retell(agent):
    call_id = cut_off_call(agent, "retell_call_abc123")
    transcripts = FakeTranscripts({"call_abc123": {"transcript_with_tool_calls": [
        {"role": "agent", "content": "Thanks for calling Summit Air."},
        {"role": "tool_call_invocation", "name": "flag_person", "arguments": "{\"why\": \"bill\"}"},
        {"role": "tool_call_result", "content": "{\"ok\": true, \"say\": \"Someone will call.\"}"},
        {"role": "injected", "content": "Say this to the caller now: stay safe"},
        {"role": "user", "content": "Okay, thanks."},
    ]}})
    model = FakeModel()
    result = grade(agent, call_id, model, transcripts=transcripts)
    assert transcripts.asked == ["call_abc123"]
    assert result["source"] == "retell"
    lines = model.records[0]["transcript"]
    assert lines[0] == "agent: Thanks for calling Summit Air."
    assert lines[1].startswith("tool: flag_person")
    assert lines[2].startswith("result: ")
    assert lines[3] == "note: Say this to the caller now: stay safe"
    assert lines[4] == "caller: Okay, thanks."


def test_a_grader_flag_is_never_emailed(agent, clock):
    clock.now = clock.now.replace(hour=4)  # 11 PM in New York, after hours
    call_id = cut_off_call(agent)
    grade(agent, call_id, FakeModel({**CUT_OFF, "severity": "critical"}))
    conn = agent.db()
    try:
        counts = jobs.alert_on_call(conn, tenant(agent), services(agent).mailer, clock())
        assert counts == {"sent": 0, "held": 0, "failed": 0}
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_model_comes_from_the_offline_grader_profile(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert grader.model_from_config() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    model = grader.model_from_config()
    assert model.cfg["model"] == "claude-opus-5-5"


def test_the_anthropic_model_asks_for_the_schema_and_prices_the_call():
    class Usage:
        input_tokens, output_tokens = 10_000, 2_000

    class Text:
        type, text = "text", json.dumps(STYLE_ONLY)

    class Message:
        stop_reason, usage, content = "end_turn", Usage(), [Text()]

    class Client:
        def __init__(self):
            self.beta = self
            self.messages = self
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return Message()

    client = Client()
    cfg = {"model": "m", "thinking": True, "effort": "medium",
           "price": {"input": 4.0, "output": 20.0}}
    answer, cost = grader.AnthropicGraderModel(cfg, "KEY", client).grade("sys", "{}")
    assert answer == STYLE_ONLY
    assert cost == pytest.approx(0.08)
    assert client.kwargs["output_config"]["format"]["schema"] == grader.SCHEMA
    assert client.kwargs["thinking"] == {"type": "adaptive"}
    assert client.kwargs["output_config"]["effort"] == "medium"
    assert client.kwargs["fallbacks"] == "default"


def test_retell_is_read_from_the_versioned_get_call_endpoint():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers["authorization"]))
        return httpx.Response(200, json={"call_id": "call_1"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://retell.test")
    assert grader.RetellTranscripts("k", http).get("call_1") == {"call_id": "call_1"}
    assert seen == [("GET", "/v2/get-call/call_1", "Bearer k")]
    with pytest.raises(ValueError):
        grader.RetellTranscripts("k", http).get("../list-calls")


# ---- runs and the schedule ---------------------------------------------------------------------


def test_a_range_run_grades_each_call_once_and_counts_progress(agent):
    first, second = cut_off_call(agent), cut_off_call(agent)
    services(agent).grader_model = FakeModel(CUT_OFF, STYLE_ONLY)
    conn = agent.db()
    try:
        t, now = tenant(agent), agent.clock()
        ids = grader.calls_between(conn, t, now - timedelta(hours=1), now + timedelta(minutes=1))
        assert ids == [first, second]
        run_id = grader.create_run(conn, t, ids, now, "Test", scope="range")
        grader.launch(services(agent), run_id)
        run = grader.get_run(conn, run_id)
        assert (run["status"], run["done"], run["flagged"]) == ("done", 2, 1)
        assert run["cost_usd"] == pytest.approx(0.10)
        assert grader.cost_of(conn, ids) == {
            "calls": 2, "to_grade": 0, "already": 2, "usd": 0.0, "ask": False, "capped": False}
        again = grader.create_run(conn, t, ids, now, "Test", scope="range")
        grader.launch(services(agent), again)
        assert grader.get_run(conn, again)["cost_usd"] == 0
    finally:
        conn.close()


def test_a_big_range_asks_before_it_starts(agent):
    conn = agent.db()
    try:
        assert grader.cost_of(conn, ["a"] * 8)["ask"] is False
        assert grader.cost_of(conn, ["a"] * 9)["usd"] == pytest.approx(9 * grader.DEFAULT_CALL_USD)
        assert grader.cost_of(conn, ["a"] * 21)["ask"] is True
    finally:
        conn.close()


def test_the_daily_run_is_due_once_a_day_after_its_time(agent, clock):
    t = tenant(agent)
    conn = agent.db()
    try:
        assert grader.schedule(conn, t) == {"enabled": True, "at": "06:00"}
        clock.now = clock.now.replace(hour=10)  # 5 AM in New York
        assert grader.daily_due(conn, t, clock()) is None
        clock.now = clock.now.replace(hour=12)  # 7 AM
        since, until = grader.daily_due(conn, t, clock())
        assert until - since == timedelta(hours=24)
        assert grader.start_daily(services(agent), conn, t, clock()) is not None
        assert grader.daily_due(conn, t, clock()) is None
        clock.advance(days=1)
        since, until = grader.daily_due(conn, t, clock())
        assert until - since == timedelta(days=1)
        grader.set_schedule(conn, t, False, "06:00", "Test", clock())
        assert grader.daily_due(conn, t, clock()) is None
        with pytest.raises(ValueError):
            grader.set_schedule(conn, t, True, "6am", "Test", clock())
    finally:
        conn.close()


def test_the_jobs_loop_starts_the_daily_run(agent, clock):
    call_id = cut_off_call(agent)
    clock.advance(hours=1)
    services(agent).grader_model = FakeModel(CUT_OFF)
    report = jobs.run_once(services(agent))
    assert isinstance(report["summit-air"]["failure_check"], int)
    assert len(grader_flags(agent, call_id)) == 1
    assert jobs.run_once(services(agent))["summit-air"]["failure_check"] is None


def test_no_model_means_no_daily_run(agent, clock):
    services(agent).grader_model = None
    clock.advance(hours=1)
    assert jobs.run_once(services(agent))["summit-air"]["failure_check"] is None


# ---- pages --------------------------------------------------------------------------------------


def test_check_this_call_from_its_page(agent):
    call_id = cut_off_call(agent)
    services(agent).grader_model = FakeModel(CUT_OFF)
    sign_in(agent, "staff")
    page = agent.client.get(f"/owner/calls/{call_id}").text
    assert "Check this call" in page and "Not checked yet." in page
    response = agent.client.post(f"/owner/calls/{call_id}/check", follow_redirects=False)
    assert response.status_code == 303
    page = agent.client.get(f"/owner/calls/{call_id}").text
    assert "The agent ended the call while the caller was still asking" in page
    assert "Wait, can someone call me back about the bill?" in page
    assert "Check again" in page
    today = agent.client.get("/owner").text
    assert "Something went wrong on this call" in today or "Call check" in today


def test_checks_off_says_so(agent):
    call_id = cut_off_call(agent)
    services(agent).grader_model = None
    sign_in(agent, "staff")
    assert "Checks are off on this server." in agent.client.get(f"/owner/calls/{call_id}").text
    assert "Checks are off on this server." in agent.client.get("/owner/checks").text


def test_a_range_check_needs_an_admin(agent):
    sign_in(agent, "staff")
    page = agent.client.get("/owner/checks").text
    assert "An admin can check a range" in page
    response = agent.client.post("/owner/checks", data={"span": "day"})
    assert response.status_code == 403


def test_an_admin_checks_the_past_day_and_sees_the_flagged_call(agent):
    call_id = cut_off_call(agent)
    services(agent).grader_model = FakeModel(CUT_OFF)
    sign_in(agent)
    response = agent.client.post("/owner/checks", data={"span": "day"}, follow_redirects=False)
    assert response.status_code == 303
    run_id = int(response.headers["location"].rsplit("run=", 1)[1])
    page = agent.client.get(response.headers["location"]).text
    assert "Checked 1 call: 1 with a problem." in page
    assert f"/owner/calls/{call_id}" in page
    progress = agent.client.get(f"/owner/checks/runs/{run_id}").json()
    assert progress["finished"] and progress["percent"] == 100


def test_a_big_range_shows_its_cost_first(agent, monkeypatch):
    for _ in range(3):
        cut_off_call(agent)
    monkeypatch.setattr(grader, "CONFIRM_CALLS", 2)
    sign_in(agent)
    page = agent.client.post("/owner/checks", data={"span": "day"}).text
    assert "3 calls to check, about $0.36." in page
    conn = agent.db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM grader_runs").fetchone()[0] == 0
    finally:
        conn.close()
    response = agent.client.post(
        "/owner/checks", data={"span": "day", "confirm": "1"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_a_custom_range_is_read_in_local_time_and_checked(agent):
    sign_in(agent)
    bad = agent.client.post("/owner/checks", data={"span": "custom", "start": "", "end": ""})
    assert bad.status_code == 422 and "Pick a start and an end." in bad.text
    backwards = agent.client.post(
        "/owner/checks",
        data={"span": "custom", "start": "2026-11-02T09:00", "end": "2026-11-02T08:00"},
    )
    assert "The end has to be after the start." in backwards.text
    t = tenant(agent)
    since, until = checks_view.span_of(
        t, agent.clock(), "custom", "2026-11-01T08:00", "2026-11-02T08:00"
    )
    assert since.utcoffset() == timedelta(hours=-5)
    assert until - since == timedelta(days=1)


def test_the_schedule_is_set_on_the_settings_page(agent):
    sign_in(agent)
    page = agent.client.get("/owner/settings").text
    assert "Daily check" in page and 'value="06:00"' in page
    response = agent.client.post(
        "/owner/checks/schedule", data={"enabled": "1", "at": "19:30"}, follow_redirects=False
    )
    assert response.status_code == 303
    page = agent.client.get("/owner/settings?saved=checks").text
    assert 'value="19:30"' in page and "Daily call check at 19:30" in page
    conn = agent.db()
    try:
        assert grader.schedule(conn, tenant(agent)) == {"enabled": True, "at": "19:30"}
    finally:
        conn.close()


def test_staff_cannot_change_the_schedule(agent):
    sign_in(agent, "staff")
    response = agent.client.post("/owner/checks/schedule", data={"enabled": "0", "at": "06:00"})
    assert response.status_code == 403


def test_the_bench_shows_raw_output_and_grades_again(agent):
    call_id = cut_off_call(agent)
    services(agent).grader_model = FakeModel({**CUT_OFF, "evidence": ["not in the call"]})
    sign_in(agent)
    agent.client.post("/bench/checks", data={"span": "day"})
    page = agent.client.get("/bench/checks").text
    assert "Dropped" in page and "Raw output" in page and "not in the call" in page
    services(agent).grader_model = FakeModel(CUT_OFF)
    response = agent.client.post(f"/bench/checks/calls/{call_id}", follow_redirects=False)
    assert response.status_code == 303
    assert len(grader_flags(agent, call_id)) == 1


def test_the_bench_needs_an_admin(agent):
    sign_in(agent, "staff")
    assert agent.client.get("/bench/checks").status_code == 403


def test_a_purged_call_takes_its_grade_with_it(agent):
    call_id = cut_off_call(agent)
    grade(agent, call_id)
    conn = agent.db()
    try:
        with tx(conn):
            conn.execute("DELETE FROM turns WHERE call_id = ?", (call_id,))
            conn.execute("DELETE FROM calls WHERE id = ?", (call_id,))
        assert conn.execute("SELECT COUNT(*) FROM grades").fetchone()[0] == 0
    finally:
        conn.close()
