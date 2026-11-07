"""The bench views for the interview walkthrough (issue #188): the call list's filters, the call
page's Why, Timing, Transcript and Summary panes, Retell's side of the transcript read on demand,
and the Questions and Cost pages. All read-only."""

import pytest
from test_ts_safety import GAS_LINES
from toolkit import UNKNOWN, Agent, book, first_open, make_server, ready_to_book, sign_in

from app import bench, bench_call, bench_pages, redact, trace


@pytest.fixture
def agent(tmp_path, clock, mock):
    agent = Agent(make_server(tmp_path, clock, mock[0]), clock)
    sign_in(agent, "admin", "dana")
    return agent


def timeline(agent: Agent, call: str) -> dict:
    conn = agent.db()
    try:
        return trace.timeline(conn, call)
    finally:
        conn.close()


def gas_call(agent: Agent) -> str:
    call = ready_to_book(agent, UNKNOWN)
    slot = first_open(agent, call)
    agent.clock.advance(seconds=2)
    agent.turn(call, "Wait, I smell gas.")
    assert book(agent, call, slot)["refused"] == "gate_safety"
    agent.clock.advance(seconds=3)
    agent.say(call, GAS_LINES)
    agent.end(call, "caller_hangup")
    return call


def timed_call(agent: Agent, call_id: str | None = None) -> str:
    call = agent.start(UNKNOWN, call_id=call_id)["data"]["call_id"]
    agent.turn(call, "My heat is out at 45 Forest Ave.")
    agent.turn(call, "I can help. What's the ZIP?", speaker="agent", server_ms=40.0,
               llm_first_token_ms=600.0, tool_ms=200.0, reply_ms=1200.0)
    agent.turn(call, "10310.")
    agent.turn(call, "Thanks.", speaker="agent", server_ms=30.0, llm_first_token_ms=500.0,
               reply_ms=900.0)
    agent.end(call)
    return call


# ---- the call list --------------------------------------------------------------------------

def test_the_call_list_filters_by_outcome_ended_by_and_signal(agent):
    gas = gas_call(agent)
    plain = timed_call(agent)
    page = agent.client.get("/bench?outcome=safety").text
    assert redact.call_key(gas) in page and redact.call_key(plain) not in page
    page = agent.client.get("/bench?ended=caller").text
    assert redact.call_key(gas) in page and redact.call_key(plain) not in page
    page = agent.client.get("/bench?ended=agent").text
    assert redact.call_key(plain) in page and redact.call_key(gas) not in page
    page = agent.client.get("/bench?signal=refused").text
    assert redact.call_key(gas) in page and redact.call_key(plain) not in page


def test_an_unknown_filter_value_is_ignored_not_an_error(agent):
    call = timed_call(agent)
    page = agent.client.get("/bench?outcome=drop%20table&signal=nope").text
    assert redact.call_key(call) in page


def test_a_chip_links_keep_the_other_filters_and_a_second_click_clears_its_own(agent):
    timed_call(agent)
    page = agent.client.get("/bench?outcome=safety&ended=caller").text
    assert 'href="/bench?ended=caller"' in page, "the picked Safety chip clears only itself"
    assert "outcome=safety" in page and "ended=agent" in page


# ---- the call page ----------------------------------------------------------------------

def test_why_shows_the_safety_trigger_the_refusal_and_how_fast_the_lines_came(agent):
    call = gas_call(agent)
    conn = agent.db()
    try:
        rows = bench_call.decisions(conn, timeline(agent, call))
    finally:
        conn.close()
    kinds = [r["kind"] for r in rows]
    assert "Safety" in kinds and "Refused" in kinds and kinds[-1] == "End"
    refused = next(r for r in rows if r["kind"] == "Refused")
    assert refused["decision"] == "book_appointment" and refused["rule"] == "Safety lines first"
    signal = next(r for r in rows if r["kind"] == "Safety" and r["source"] == "Caller's words")
    assert signal["say"] == "Wait, I smell gas."
    summary = bench_call.safety_summary(timeline(agent, call))
    assert summary["trigger"] == "Wait, I smell gas." and summary["seconds"] == pytest.approx(3.0, abs=1)


def test_priority_reasons_name_their_source(agent):
    assert bench_call._priority_source("side_model", "x, from the side model") == "Side model"
    assert bench_call._priority_source("vulnerable_person", 'older person, caller said "my mom is 90"') \
        == "Caller's words"
    assert bench_call._priority_source("bracket:cold_outside", "cold tonight") == "Temperature"
    assert bench_call._priority_source("parent_no_age", "a parent at home") == "Phrase rule"


def test_who_ended_the_call(agent):
    gas = timeline(agent, gas_call(agent))
    assert bench_call.ended_by(gas)["who"] == "Caller"
    plain = timeline(agent, timed_call(agent))
    assert bench_call.ended_by(plain) == {"who": "Agent", "why": "", "retell": None}
    shown = bench_call.ended_by(plain, {"disconnection_reason": "agent_hangup"})
    assert shown["retell"] == "agent hangup"


def test_timing_lays_out_each_stage_and_the_rest_of_the_reply(agent):
    found = bench_call.timing(timeline(agent, timed_call(agent)))
    first, _ = found["rows"]
    assert first["stages"]["llm_first_token_ms"] == 600.0 and first["reply"] == 1200.0
    assert first["other"] == pytest.approx(360.0)
    assert first["caller"] == "My heat is out at 45 Forest Ave."
    assert [k for k, _ in found["stages"]] == ["server_ms", "llm_first_token_ms", "tool_ms"]
    assert found["summary"]["reply"]["p50"] == 1200.0 and found["summary"]["reply"]["n"] == 2


def test_the_call_page_has_every_pane_and_the_cost(agent):
    call = timed_call(agent)
    page = agent.client.get(f"/bench/calls/{redact.call_key(call)}").text
    for pane in ("summary", "why", "transcript", "timing", "steps", "replay"):
        assert f'id="{pane}"' in page and f'href="#{pane}"' in page
    assert "Ended by" in page and "Reply p50" in page and "$0." in page


# ---- what Retell heard ------------------------------------------------------------------

def words(text: str, start: float) -> list[dict]:
    out = []
    for w in text.split():
        out.append({"word": w, "start": start, "end": start + 0.3})
        start += 0.35
    return out


RETELL = {
    "call_status": "ended",
    "disconnection_reason": "user_hangup",
    "call_cost": {"combined_cost": 21.5},
    "transcript_object": [
        {"role": "user", "content": "My eat is out at 45 for rest Ave.", "words": words("My eat is out at 45 for rest Ave.", 1.0)},
        {"role": "agent", "content": "I can help. What's the", "words": words("I can help. What's the", 5.0)},
        {"role": "user", "content": "10310.", "words": words("10310.", 6.4)},
        {"role": "agent", "content": "ZIP?", "words": words("ZIP?", 6.0)},
        {"role": "agent", "content": "Thanks.", "words": words("Thanks.", 9.0)},
    ],
}


class Reader:
    def __init__(self):
        self.asked = []

    def get(self, retell_id):
        self.asked.append(retell_id)
        return RETELL


def test_heard_pairs_runs_and_marks_misheard_and_talked_over_lines(agent):
    rows = bench_call.heard(timeline(agent, timed_call(agent)), RETELL)
    assert [r["role"] for r in rows] == ["caller", "agent", "caller", "agent"]
    assert rows[0]["differs"] and "for rest" in rows[0]["retell"]
    assert rows[1]["tail"] and rows[1]["retell"] == "I can help. What's the ZIP?"
    assert rows[2]["over"], "the caller began before the agent's line had ended"
    assert not rows[3]["differs"]


def test_without_retell_the_transcript_is_the_servers_alone(agent):
    rows = bench_call.heard(timeline(agent, timed_call(agent)), None)
    assert all(r["retell"] == "" for r in rows) and rows[0]["server"].startswith("My heat")


def test_retell_is_read_on_demand_once_and_only_for_retell_calls(agent):
    reader = Reader()
    agent.app.state.bench_retell = bench_call.RetellCalls(reader)
    call = timed_call(agent, call_id="retell_call_abc123")
    key = redact.call_key(call)
    page = agent.client.get(f"/bench/calls/{key}").text
    assert reader.asked == [], "the page itself never waits on Retell"
    assert f'data-heard="/bench/calls/{key}/heard"' in page
    pane = agent.client.get(f"/bench/calls/{key}/heard").text
    assert "Talked over" in pane and "user hangup" in pane and "$0.215" in pane
    agent.client.get(f"/bench/calls/{key}/heard")
    assert reader.asked == ["call_abc123"], "an ended call's object is kept"
    other = timed_call(agent)
    assert bench_call.RetellCalls(reader).get(other) == (None, "not a Retell call")


def test_a_failed_retell_read_shows_the_server_side_and_changes_nothing(agent):
    class Broken:
        def get(self, retell_id):
            raise RuntimeError("down")

    agent.app.state.bench_retell = bench_call.RetellCalls(Broken())
    call = timed_call(agent, call_id="retell_call_def456")
    response = agent.client.get(f"/bench/calls/{redact.call_key(call)}/heard")
    assert response.status_code == 200 and "Server only" in response.text


# ---- summary records ----------------------------------------------------------------------

def test_the_read_back_check_matches_the_booking(agent):
    call = ready_to_book(agent, UNKNOWN)
    book(agent, call, first_open(agent, call))
    conn = agent.db()
    try:
        found = bench_call.readback(conn, timeline(agent, call))
    finally:
        conn.close()
    assert found["confirmed"] and found["mismatches"] == []


def test_cost_uses_the_measured_rates_and_retells_bill_when_read(agent):
    call = timed_call(agent)
    agent.clock.advance(minutes=1)
    conn = agent.db()
    try:
        found = bench_call.call_cost(conn, timeline(agent, call), {"call_cost": {"combined_cost": 12.0}})
    finally:
        conn.close()
    assert found["model"] == bench_call.cost.MODEL_PER_CALL
    assert found["total"] == pytest.approx(found["voice"] + found["model"] + found["after"])
    assert found["retell_billed"] == 0.12


# ---- Questions, Cost, System -----------------------------------------------------------------

def test_every_question_has_evidence_and_its_links_open(agent):
    gas_call(agent)
    timed_call(agent)
    conn = agent.db()
    try:
        groups = bench_pages.questions(conn, agent.app.state.services.tenants.default)
    finally:
        conn.close()
    items = [i for g in groups for i in g["items"]]
    names = {i["name"] for i in items}
    for needed in ("Hazard smell", "Wants a human", "Hangs up on callers", "Cost per call", "2am Sunday",
                   "200 calls at once", "Quotes a price", "Spanish", "Stack and latency", "Barge-in"):
        assert needed in names
    assert all(i["evidence"] for i in items)
    gas = next(i for i in items if i["name"] == "Hazard smell")
    assert gas["evidence"].startswith("1 calls, lines in")
    for i in items:
        if i["link"]:
            path = i["link"].split("#")[0]
            assert agent.client.get(path).status_code == 200, i["link"]


def test_the_cost_page_lists_each_ended_call(agent):
    call = timed_call(agent)
    page = agent.client.get("/bench/cost").text
    assert redact.call_key(call) in page and "Voice, per minute" in page and "Measured" in page


def test_system_shows_the_prompt_files_after_hours_and_capacity(agent):
    page = agent.client.get("/bench/settings").text
    assert 'id="prompt"' in page and "spine.md" in page
    assert "On-call techs" in page
    assert 'id="capacity"' in page and "Cartesia" in page
    assert bench_pages.capacity(), "docs/scaling.md's limits table is read"


def test_the_bench_uses_the_sidebar_on_every_page(agent):
    for path in ("/bench", "/bench/questions", "/bench/cost", "/bench/settings", "/bench/evals",
                 "/bench/rules", "/bench/schedule", "/bench/checks"):
        page = agent.client.get(path).text
        assert 'class="side"' in page and 'aria-current="page"' in page, path
        assert "/bench/static/bench.css" in page, path
        assert 'class="app-header"' not in page and "tokens.css" not in page, path
        assert 'href="/owner"' not in page, path
    owner = agent.client.get("/owner").text
    assert 'class="app-header"' in owner and "/bench/static" not in owner


def test_the_bench_serves_its_own_stylesheet_and_script(agent):
    css = agent.client.get("/bench/static/bench.css")
    assert css.status_code == 200 and "--page: #0D0E10" in css.text
    assert "data-panes" in agent.client.get("/bench/static/bench.js").text
    assert "data-panes" not in agent.client.get("/owner/static/owner.js").text


def test_bench_filters_are_known_sql_only():
    for group, values in bench.FILTERS.items():
        assert set(values) == set(bench.FILTER_LABELS[group][1]), group


def test_the_call_page_shows_the_graders_verdict(agent):
    call = timed_call(agent)
    key = redact.call_key(call)
    assert "not run" in agent.client.get(f"/bench/calls/{key}").text
    conn = agent.db()
    try:
        conn.execute(
            "INSERT INTO grades (call_id, tenant, failure, kind, reason, source, model_profile, graded_at) "
            "VALUES (?, 'summit-air', 1, 'hung_up', 'Cut off mid-booking', 'server', 'grader', '2026-11-06T00:00:00Z')",
            (call,))
        conn.commit()
    finally:
        conn.close()
    page = agent.client.get(f"/bench/calls/{key}").text
    assert "hung up" in page and "Cut off mid-booking" in page
