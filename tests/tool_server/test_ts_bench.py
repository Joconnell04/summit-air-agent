"""The debug bench at /bench: admin only, redacted, a call as a node graph with its rule proof, a
call in progress as server-sent events, a call as a draft eval scenario, the settings with their
documented measurements, and the eval runs."""

import pytest
import yaml
from test_ts_safety import GAS_LINES
from toolkit import (
    UNKNOWN,
    Agent,
    book,
    first_open,
    in_area_address,
    make_server,
    ready_to_book,
    sign_in,
)

from app import bench, bench_data, nodes, redact, trace
from evals.scenario import Scenario

# A number the mock has no customer for, so a booking needs no account choice.
CALLER = UNKNOWN


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


def admin(agent: Agent) -> Agent:
    return sign_in(agent, "admin", "dana")


def timed_call(agent: Agent) -> str:
    """A short call with the stage timings the LiveKit worker sends, one of them left out."""
    call = agent.start(CALLER)["data"]["call_id"]
    agent.say(call, "Thanks for calling Summit Air, how can I help?")
    agent.turn(call, "My heat is out at 45 Forest Ave, Staten Island 10310.",
               transcript_ms=240.0, end_of_turn_ms=355.0)
    agent.tool("check_service_area", call, zip="10310")
    agent.turn(call, "I can help with that. What's the best number to reach you?", speaker="agent",
               llm_first_token_ms=560.0, prompt_tokens=9400.0, cache_read_tokens=9300.0,
               tts_first_byte_ms=210.0, reply_ms=1810.0)
    agent.turn(call, "It's 212 555 0999.")
    return call


def graph(agent: Agent, call: str) -> dict:
    conn = agent.db()
    try:
        return nodes.build(conn, trace.timeline(conn, call), agent.app.state.services.settings.log_path)
    finally:
        conn.close()


# ---- access --------------------------------------------------------------------------------

PAGES = [
    "/bench", "/bench/settings", "/bench/evals", "/bench/rounds/model-matrix-01",
    "/bench/evals/20261104-013438-livekit-fast-standard",
]


@pytest.mark.parametrize("path", PAGES)
def test_the_bench_needs_an_admin(agent, path):
    response = agent.client.get(path)
    assert response.status_code == 401, "nobody signed in"
    assert 'action="/owner/login"' in response.text
    sign_in(agent, "staff", "sam")
    assert agent.client.get(path).status_code == 403, "staff can't open the bench"
    admin(agent)
    assert agent.client.get(path).status_code == 200


def test_every_route_on_a_call_needs_an_admin(agent):
    call = timed_call(agent)
    for path in ("", "/graph", "/stream", "/scenario.yaml"):
        assert agent.client.get(f"/bench/calls/{call}{path}").status_code == 401, path
    sign_in(agent, "staff", "sam")
    for path in ("", "/graph", "/stream", "/scenario.yaml"):
        assert agent.client.get(f"/bench/calls/{call}{path}").status_code == 403, path


def test_signing_in_from_the_bench_comes_back_to_it(agent):
    page = agent.client.get("/bench/settings").text
    assert 'value="/bench/settings"' in page
    sign_in(agent, "admin", "dana")
    response = agent.client.post("/owner/login", data={"username": "dana", "password": "test-password-1",
                                                       "next": "/bench/settings"}, follow_redirects=False)
    assert response.headers["location"] == "/bench/settings"


def test_only_an_admin_sees_the_bench_tab_on_the_owner_view(agent):
    sign_in(agent, "staff", "sam")
    assert 'href="/bench"' not in agent.client.get("/owner").text
    admin(agent)
    assert 'href="/bench"' in agent.client.get("/owner").text


# ---- redaction -----------------------------------------------------------------------------

def test_phone_numbers_keep_only_their_last_four_digits():
    for said in ("(718) 555-0142", "718-555-0142", "+17185550142", "718, 555, 0142"):
        assert redact.text(f"call me on {said}") == "call me on •••-•••-0142"
    assert redact.text("call-_+16466240418_mKdA") == "call-_•••-•••-0418_mKdA"


def test_an_address_keeps_only_its_street_name():
    out = redact.text("It's 45 Forest Ave, apartment 3B, Staten Island 10310.")
    assert "45" not in out and "3B" not in out and "10310" not in out
    assert "Forest Ave" in out
    found = redact.value({"street": "45 Forest Ave", "unit": "3B", "city": "Staten Island",
                          "zip": "10310"})
    assert found["street"] == "Forest Ave"
    assert {found["unit"], found["city"], found["zip"]} == {"•••"}


def test_keys_and_tokens_are_replaced():
    assert redact.text("key sk-ant-api03-abcdefghij1234 here") == "key [redacted] here"
    assert redact.text("ghp_abcdefghijklmnop1234") == "[redacted]"
    assert redact.text("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8") == "[redacted]"
    assert redact.value({"api_key": "anything", "fsm_key": "x", "gate_code": "1234"}) == {
        "api_key": "[redacted]", "fsm_key": "[redacted]", "gate_code": "[redacted]"}


def test_ordinary_text_is_left_alone():
    for said in ("I'm 84 and it's 55 degrees in here", "run 20261104-013438-livekit-fast-standard",
                 "2026-11-03T13:00:00Z", "skip-the-read-back-on-repeat-calls", "job 1234"):
        assert redact.text(said) == said


def test_the_call_page_shows_no_phone_number_or_house_number(agent):
    call = timed_call(agent)
    page = admin(agent).client.get(f"/bench/calls/{call}").text
    assert "555-0999" not in page and "5550999" not in page and "555 0999" not in page
    assert "45 Forest" not in page and "10310" not in page
    assert "•••-•••-0999" in page


# ---- the node graph ------------------------------------------------------------------------

def test_a_call_becomes_steps_in_lanes_with_their_timings(agent):
    call = timed_call(agent)
    g = graph(agent, call)
    kinds = [n["kind"] for n in g["nodes"]]
    assert kinds == [
        "start", "spoken",
        "caller_line", "end_of_turn", "turn_check",
        "model_request", "tool", "model_request", "model_wrote", "spoken",
        "caller_line", "end_of_turn", "turn_check",
    ]
    by_kind = {}
    for n in g["nodes"]:
        by_kind.setdefault(n["kind"], []).append(n)
    assert by_kind["caller_line"][0]["ms"] == 240.0
    assert by_kind["end_of_turn"][0]["ms"] == 355.0
    assert by_kind["caller_line"][1]["ms"] is None, "a timing the worker didn't send is missing"
    first, last = by_kind["model_request"]
    assert last["ms"] == 560.0, "first token belongs to the request that wrote the line"
    assert ("Prompt tokens", 9400.0) in first["facts"], "cache numbers belong to the first request"
    assert by_kind["spoken"][1]["ms"] == 210.0
    assert [n["lane"] for n in g["nodes"][:3]] == ["server", "voice", "caller"]
    assert g["nodes"][3]["from_col"] == nodes.LANE_INDEX["caller"]


def test_a_model_request_shows_what_the_model_was_given(agent):
    call = timed_call(agent)
    g = graph(agent, call)
    start = g["nodes"][0]
    prompt = next(b["text"] for b in start["blocks"] if b["title"] == "System prompt")
    assert "# This call" in prompt and "Summit Air" in prompt
    request = next(n for n in g["nodes"] if n["kind"] == "model_request")
    assert request["refs"] == [{"node": start["id"], "title": "System prompt",
                                "text": f"{len(prompt):,} characters, the same on every request"}]
    roles = [m["role"] for m in request["messages"]]
    assert roles == ["system", "assistant", "user"]
    assert "Forest Ave" in request["messages"][-1]["text"]
    second = [n for n in g["nodes"] if n["kind"] == "model_request"][1]
    assert [m["role"] for m in second["messages"]][-2:] == ["assistant", "tool"]
    assert '"ok": true' in second["messages"][-1]["text"]


def test_a_tool_step_carries_its_arguments_and_result(agent):
    call = timed_call(agent)
    tool = next(n for n in graph(agent, call)["nodes"] if n["kind"] == "tool")
    assert tool["title"] == "check_service_area"
    titles = [b["title"] for b in tool["blocks"]]
    assert titles == ["Arguments", "Result to the model"]
    assert "10310" not in tool["blocks"][0]["text"], "the ZIP is an address part"


def test_a_gas_call_shows_the_check_the_spoken_lines_and_the_rule_that_held(agent):
    call = ready_to_book(agent, CALLER)
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    held = book(agent, call, slot)
    assert held["refused"] == "gate_safety"
    agent.say(call, GAS_LINES)
    g = graph(agent, call)
    check = [n for n in g["nodes"] if n["kind"] == "turn_check"][-1]
    assert check["tone"] == "warn" and "gas" in check["summary"]
    rule = next(n for n in g["nodes"] if n["kind"] == "rule")
    assert rule["summary"] == "Held the booking until the safety lines were said."
    spoken = g["nodes"][-1]
    assert spoken["kind"] == "spoken" and "no model" in dict(spoken["facts"])["Source"]
    proof = {p["rule"]: p for p in g["proof"]}
    assert proof["Safety check on every caller line"]["outcome"] == "fired"
    assert proof["Safety lines first"]["outcome"] == "fired"
    assert proof["Read-back before booking"]["outcome"] == "passed"
    assert proof["Caller's own records"]["outcome"] == "not reached"


def test_markdown_the_voice_would_drop_is_shown_as_a_filter_step(agent):
    pytest.importorskip("livekit.agents")
    call = agent.start(CALLER)["data"]["call_id"]
    agent.turn(call, "Hello?")
    agent.say(call, "**Sure**, I can help with that.")
    g = graph(agent, call)
    step = next(n for n in g["nodes"] if n["kind"] == "filter")
    assert "*" in step["summary"]
    assert g["nodes"][-1]["summary"] == "Sure, I can help with that."


def test_the_call_page_draws_the_graph_and_says_what_is_missing(agent):
    call = timed_call(agent)
    page = admin(agent).client.get(f"/bench/calls/{call}").text
    assert 'data-node-graph' in page and 'id="d-n1"' in page
    assert '<span class="ms missing">missing</span>' in page
    assert "560 ms" in page
    assert "Rule proof" in page


def test_a_call_with_nothing_in_the_log_says_so(agent):
    call = timed_call(agent)
    agent.app.state.services.settings.log_path.unlink()
    g = graph(agent, call)
    assert not g["logged"]
    assert dict(g["nodes"][0]["facts"])["System prompt"].startswith("The /calls/start result")


def test_an_unknown_call_is_not_found(agent):
    assert admin(agent).client.get("/bench/calls/nope").status_code == 404


# ---- calls list, live view -----------------------------------------------------------------

def test_calls_are_found_by_phone_digits_or_call_id(agent):
    call = timed_call(agent)
    other = agent.start("(917) 555-0100")["data"]["call_id"]
    admin(agent)
    response = agent.client.get("/bench?q=0999", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/bench/calls/{call}"
    listed = agent.client.get("/bench").text
    assert f"/bench/calls/{call}" in listed and f"/bench/calls/{other}" in listed
    assert "In progress" in listed, "neither call has ended"


def test_a_call_in_progress_streams_its_changes_and_ends(agent, monkeypatch):
    call = timed_call(agent)
    monkeypatch.setattr(bench, "STREAM_SECONDS", 0)
    body = admin(agent).client.get(f"/bench/calls/{call}/stream").text
    assert body.startswith("event: changed\ndata: {")
    assert body.rstrip().endswith("event: ended\ndata: {}")
    page = agent.client.get(f"/bench/calls/{call}").text
    assert f'data-live="/bench/calls/{call}/stream"' in page


def test_an_ended_call_stream_closes_at_once(agent):
    call = timed_call(agent)
    agent.end(call)
    body = admin(agent).client.get(f"/bench/calls/{call}/stream").text
    assert body.count("event: changed") == 1 and "event: ended" in body
    assert "data-live=" not in agent.client.get(f"/bench/calls/{call}").text


# ---- replay as a scenario ------------------------------------------------------------------

def test_a_booked_call_becomes_a_valid_draft_scenario(agent):
    call = ready_to_book(agent, CALLER, in_area_address(2))
    assert book(agent, call, first_open(agent, call))["ok"]
    agent.end(call)
    response = admin(agent).client.get(f"/bench/calls/{call}/scenario.yaml")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment;")
    draft = yaml.safe_load(response.text)
    Scenario.model_validate(draft)
    said = [e["text"] for e in trace.timeline(agent.db(), call)["events"]
            if e["type"] == "turn" and e["speaker"] == "caller"]
    assert draft["opening"] == redact.text(said[0])
    assert draft["script"] == [f"Say: {redact.text(t)}" for t in said[1:]]
    checks = [next(iter(m)) for m in draft["must"]]
    assert checks == ["safety_lines", "booking", "read_back", "outcome"]
    assert draft["caller_id"] != CALLER and "0999" not in response.text
    assert response.text.startswith("# Draft from call")


def test_a_call_with_nothing_booked_still_drafts_a_valid_scenario(agent):
    call = timed_call(agent)
    found = trace.timeline(agent.db(), call)
    Scenario.model_validate(yaml.safe_load(bench_data.scenario_draft(found, redact)))


# ---- settings and evals --------------------------------------------------------------------

def test_every_documented_knob_names_a_setting_in_models_yaml():
    rows = bench_data.knobs()
    assert len(rows) == len(bench_data.KNOBS)
    for row in rows:
        assert row["basis"] in ("MEASURED", "SOURCE", "ASSUMED"), row["path"]
        assert row["source"], row["path"]


def test_the_settings_page_shows_live_values_and_never_a_secret(agent):
    page = admin(agent).client.get("/bench/settings").text
    config = yaml.safe_load(bench_data.MODELS.read_text())
    assert f"{config['voice']['endpointing']['normal']['min_delay']}" in page
    assert "voice.endpointing.digits.max_delay" in page
    assert "claude-haiku-5-5" in page and "Measured" in page and "Assumed" in page
    assert "test-token" not in page, "the debug token shows only as set"


def test_the_evals_page_shows_each_run_against_the_floor(agent):
    page = admin(agent).client.get("/bench/evals").text
    newest = bench_data.runs()[0]
    assert f"/bench/evals/{newest.run_id}" in page
    assert 'class="floor"' in page and "Floor 90%" in page
    assert "Reference" in page
    gate = agent.client.get("/bench/evals?suite=gate").text
    assert "20261103-225314-livekit-fast-gate" in gate
    assert "20261104-013438-livekit-fast-standard" not in gate


def test_a_run_lists_its_failures_with_checks_and_grader_notes(agent):
    run = next(r for r in bench_data.runs() if bench_data.failures(r))
    page = admin(agent).client.get(f"/bench/evals/{run.run_id}").text
    failed = bench_data.failures(run)[0]
    assert f"/bench/evals/{run.run_id}/{failed.key}" in page
    scenario = agent.client.get(f"/bench/evals/{run.run_id}/{failed.key}").text
    assert "Transcript" in scenario and "Code checks" in scenario
    assert agent.client.get(f"/bench/evals/{run.run_id}/nope").status_code == 404
    assert agent.client.get("/bench/evals/nope").status_code == 404


def test_a_round_is_shown_and_nothing_outside_the_folder(agent):
    admin(agent)
    page = agent.client.get("/bench/rounds/model-matrix-01").text
    assert "Haiku 5.5 against Sonnet 5.5" in page and "<table" in page
    assert agent.client.get("/bench/rounds/coauthoring").status_code == 200
    assert agent.client.get("/bench/rounds/..%2F..%2FCLAUDE").status_code == 404
    assert agent.client.get("/bench/rounds/README").status_code == 404


def test_markdown_is_escaped_before_it_is_drawn():
    out = bench_data.markdown("# T\n\nA <script>x</script> and **bold** `code`\n\n- one\n- two")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<strong>bold</strong>" in out and "<code>code</code>" in out
    assert "<ul><li>one</li><li>two</li></ul>" in out


def test_the_design_specimen_shows_the_node_graph(agent):
    page = admin(agent).client.get("/owner/design").text
    assert "Node graph" in page and 'aria-label="Specimen steps"' in page
    assert "Rate chart" in page
