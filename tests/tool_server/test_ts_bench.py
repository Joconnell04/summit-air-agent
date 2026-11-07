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

from app import bench, bench_call, bench_data, nodes, redact, trace
from evals.scenario import Scenario

# A number the mock has no customer for, so a booking needs no account choice.
CALLER = UNKNOWN
# A LiveKit call id carries the caller's number, the way the real ones do.
CALL_ID = "call-_+12125550999_mKdAaWwe"


@pytest.fixture
def agent(tmp_path, clock, mock):
    return Agent(make_server(tmp_path, clock, mock[0]), clock)


def admin(agent: Agent) -> Agent:
    return sign_in(agent, "admin", "dana")


def timed_call(agent: Agent) -> str:
    """A short call with the stage timings the LiveKit worker sends, one of them left out."""
    call = agent.start(CALLER, call_id=CALL_ID)["data"]["call_id"]
    agent.say(call, "Thanks for calling Summit Air, how can I help?")
    agent.turn(call, "My heat is out at 45 Forest Ave, Staten Island 10310.",
               transcript_ms=240.0, end_of_turn_ms=355.0)
    agent.tool("check_service_area", call, zip="10310")
    agent.turn(call, "I can help with that. What's the best number to reach you?", speaker="agent",
               llm_first_token_ms=560.0, prompt_tokens=9400.0, cache_read_tokens=9300.0,
               tts_first_byte_ms=210.0, reply_ms=1810.0)
    agent.turn(call, "It's 212 555 0999.")
    return call


def key(call: str) -> str:
    return redact.call_key(call)


def graph(agent: Agent, call: str) -> dict:
    conn = agent.db()
    try:
        return nodes.build(conn, trace.timeline(conn, call), agent.app.state.services.settings.log_path)
    finally:
        conn.close()


# ---- access --------------------------------------------------------------------------------

def bench_paths(agent: Agent, call: str) -> list[str]:
    """Every GET route under /bench, with real values in place of its parameters, so a route added
    later is checked too."""
    run = bench_data.runs()[0]
    agent.tool("find_slots", call, job_type="tune_up")  # a tool call for the tool call page
    conn = agent.db()
    try:
        tool_id = conn.execute("SELECT max(id) FROM tool_calls WHERE call_id = ? AND tool = "
                               "'find_slots'", (call,)).fetchone()[0]
    finally:
        conn.close()
    values = {"key": key(call), "run_id": run.run_id, "scenario": run.scenarios[0].key,
              "slug": "model-matrix-01", "tool_id": str(tool_id)}
    paths = []
    for route in agent.app.routes:
        if route.path.startswith("/bench") and "GET" in getattr(route, "methods", ()):
            path = route.path
            for name, value in values.items():
                path = path.replace("{" + name + "}", value)
            assert "{" not in path, f"no test value for {route.path}"
            paths.append(path)
    return paths


def test_every_bench_route_needs_an_admin(agent):
    call = timed_call(agent)
    agent.end(call)
    paths = bench_paths(agent, call)
    assert len(paths) >= 10
    for path in paths:
        response = agent.client.get(path)
        assert response.status_code == 401 and 'action="/owner/login"' in response.text, path
    sign_in(agent, "staff", "sam")
    for path in paths:
        assert agent.client.get(path).status_code == 403, path
    admin(agent)
    for path in paths:
        assert agent.client.get(path).status_code == 200, path


def test_signing_in_from_the_bench_comes_back_to_it(agent):
    page = agent.client.get("/bench/settings").text
    assert 'value="/bench/settings"' in page
    sign_in(agent, "admin", "dana")
    response = agent.client.post("/owner/login", data={"username": "dana", "password": "test-password-1",
                                                       "next": "/bench/settings"}, follow_redirects=False)
    assert response.headers["location"] == "/bench/settings"


def test_the_bench_is_a_button_on_settings_for_admins_and_not_a_tab(agent):
    """Jackson, 7 November: the bench is his tool, not the owner's, so it leaves the tab bar."""
    sign_in(agent, "staff", "sam")
    assert 'href="/bench"' not in agent.client.get("/owner/settings").text
    admin(agent)
    assert 'href="/bench"' not in agent.client.get("/owner").text
    assert 'href="/bench"' in agent.client.get("/owner/settings").text


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


@pytest.mark.parametrize("said, gone", [
    ("I'm at 123 Main, near the park", "123"),
    ("It's 123 Broadway", "123"),
    ("my number is seven one eight, five five five, zero one four two", "seven one eight"),
    ("the code is 4 4 1 1", "4 4 1 1"),
    ("the gate code is 4411, come round the side", "4411"),
    ("alarm code: 90210", "90210"),
    ("use sk_live_abcdefghijkl1234", "sk_live_"),
    ("key proj-abcdefghijklmnop", "proj-"),
    ("Authorization: Bearer abc.DEF+ghi/jkl=", "abc.DEF"),
    ("token Zm9vYmFyQmF6cXV4MTIzNDU2Nzg5MGFiY2RlZg+/==", "Zm9vYmFy"),
])
def test_what_the_patterns_missed_before_is_masked(said, gone):
    assert gone not in redact.text(said), redact.text(said)


def test_fields_named_like_a_key_are_hidden():
    found = redact.value({"key": "x1", "api-key": "x2", "API_KEY": "x3", "x-api-key": "x4",
                          "caller_key": "2125550999", "monkey": "kept"})
    assert found == {"key": "[redacted]", "api-key": "[redacted]", "API_KEY": "[redacted]",
                     "x-api-key": "[redacted]", "caller_key": "\u2022\u2022\u2022-\u2022\u2022\u2022-0999",
                     "monkey": "kept"}


def test_ordinary_text_is_left_alone():
    for said in ("I'm 84 and it's 55 degrees in here", "run 20261104-013438-livekit-fast-standard",
                 "2026-11-03T13:00:00Z", "skip-the-read-back-on-repeat-calls", "job 1234",
                 "Round 4 Jackson's notes", "Nov 3 Brooklyn", "at 3 PM on Monday",
                 "one or two people", "29 of 32 standard scenarios", "Haiku 5.5 against Sonnet 5.5"):
        assert redact.text(said) == said


def test_the_call_page_shows_the_full_phone_number_and_address_to_an_admin(agent):
    call = timed_call(agent)
    page = admin(agent).client.get(f"/bench/calls/{key(call)}").text
    title = page.split("<h1", 1)[1].split("</h1>", 1)[0]
    assert "5550999" in title, "the call id, phone number included, is shown as recorded"
    assert "10310" in page, "addresses are shown as recorded"


# ---- the node graph ------------------------------------------------------------------------

def test_a_call_becomes_steps_in_lanes_with_their_timings(agent):
    call = timed_call(agent)
    g = graph(agent, call)
    kinds = [n["kind"] for n in g["steps"]]
    assert kinds == [
        "start", "spoken",
        "caller_line", "end_of_turn", "turn_check",
        "model_request", "tool", "model_request", "model_wrote", "spoken",
        "caller_line", "end_of_turn", "turn_check",
    ]
    by_kind = {}
    for n in g["steps"]:
        by_kind.setdefault(n["kind"], []).append(n)
    assert by_kind["caller_line"][0]["ms"] == 240.0
    assert by_kind["end_of_turn"][0]["ms"] == 355.0
    assert by_kind["caller_line"][1]["ms"] is None, "a timing the worker didn't send is missing"
    first, last = by_kind["model_request"]
    assert last["ms"] == 560.0, "first token belongs to the request that wrote the line"
    assert ("Prompt tokens", 9400.0) in first["facts"], "cache numbers belong to the first request"
    assert by_kind["spoken"][1]["ms"] == 210.0
    assert [n["lane"] for n in g["steps"][:3]] == ["server", "voice", "caller"]
    assert g["steps"][3]["from_col"] == nodes.LANE_INDEX["caller"]
    # Jackson read "Known number: yes" as a customer on file; it only means the caller ID came through.
    labels = [k for k, _ in g["steps"][0]["facts"]]
    assert "Caller ID shown" in labels and "Known number" not in labels


def test_a_model_request_shows_what_the_model_was_given(agent):
    call = timed_call(agent)
    g = graph(agent, call)
    start = g["steps"][0]
    prompt = next(b["text"] for b in start["blocks"] if b["title"] == "System prompt")
    assert "# This call" in prompt and "Summit Air" in prompt
    request = next(n for n in g["steps"] if n["kind"] == "model_request")
    assert request["refs"] == [{"node": start["id"], "title": "System prompt",
                                "text": f"{len(prompt):,} characters, the same on every request"}]
    roles = [m["role"] for m in request["messages"]]
    assert roles == ["system", "assistant", "user"]
    assert "Forest Ave" in request["messages"][-1]["text"]
    second = [n for n in g["steps"] if n["kind"] == "model_request"][1]
    assert [m["role"] for m in second["messages"]][-2:] == ["assistant", "tool"]
    assert '"ok": true' in second["messages"][-1]["text"]


def test_a_tool_step_carries_its_arguments_and_result(agent):
    call = timed_call(agent)
    tool = next(n for n in graph(agent, call)["steps"] if n["kind"] == "tool")
    assert tool["title"] == "check_service_area"
    titles = [b["title"] for b in tool["blocks"]]
    assert titles == ["Arguments", "Result to the model"]
    assert "10310" in tool["blocks"][0]["text"], "admins see the ZIP as the agent sent it"


def test_a_gas_call_shows_the_check_the_spoken_lines_and_the_rule_that_held(agent):
    call = ready_to_book(agent, CALLER)
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    held = book(agent, call, slot)
    assert held["refused"] == "gate_safety"
    agent.say(call, GAS_LINES)
    g = graph(agent, call)
    check = [n for n in g["steps"] if n["kind"] == "turn_check"][-1]
    assert check["tone"] == "warn" and "gas" in check["summary"]
    rule = next(n for n in g["steps"] if n["kind"] == "rule")
    assert rule["summary"] == "Held the booking until the safety lines were said."
    spoken = g["steps"][-1]
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
    step = next(n for n in g["steps"] if n["kind"] == "filter")
    assert "*" in step["summary"]
    assert g["steps"][-1]["summary"] == "Sure, I can help with that."


def test_the_steps_tab_joins_related_steps_into_rows(agent):
    """Jackson, 7 November: fewer, plainer rows. A caller line with its end of turn and turn
    check is one row; a reply with the model's request, what it wrote and what was spoken is one
    "Agent said" row; a request that only led to a tool joins the tool's row."""
    call = timed_call(agent)
    g = graph(agent, call)
    assert [r["kind"] for r in g["nodes"]] == [
        "start", "reply", "caller_line", "tool", "reply", "caller_line"]
    assert len(g["steps"]) == 13
    caller = g["nodes"][2]
    assert [j["kind"] for j in caller["joined"]] == ["end_of_turn", "turn_check"]
    tool = g["nodes"][3]
    assert [j["kind"] for j in tool["joined"]] == ["model_request"]
    reply = g["nodes"][4]
    assert reply["title"] == "Agent said" and reply["lane"] == "model"
    assert [j["kind"] for j in reply["joined"]] == ["model_request", "model_wrote", "spoken"]
    assert reply["ms"] == 560.0, "a reply's timing is the model's first token"


def test_fragments_of_one_line_and_a_caller_line_said_again_are_one_row(agent):
    """Call retell_call_44f21506eca3346bd3547be9de8: Retell reported "Hi," and "I'm" as two
    agent lines and the caller's growing line three times."""
    call = agent.start(CALLER)["data"]["call_id"]
    agent.turn(call, "Hello?")
    agent.say(call, "Hi,")
    agent.say(call, "I'm")
    agent.turn(call, "Hello? Um, my heat is out.")
    agent.turn(call, "Hello? Um, my heat is out. It's cold.")
    rows = graph(agent, call)["nodes"]
    replies = [r for r in rows if r["kind"] == "reply"]
    assert len(replies) == 1 and replies[0]["summary"] == "Hi, I'm"
    callers = [r for r in rows if r["kind"] == "caller_line"]
    assert [c["summary"] for c in callers] == ["Hello?", "Hello? Um, my heat is out. It's cold."]


def test_rules_that_held_are_marked_as_proof_on_their_rows(agent):
    call = ready_to_book(agent, CALLER)
    slot = first_open(agent, call)
    agent.turn(call, "Wait, I smell gas.")
    book(agent, call, slot)
    agent.say(call, GAS_LINES)
    rows = graph(agent, call)["nodes"]
    marked = {r["kind"]: r["proof"] for r in rows if r.get("proof")}
    assert "gas" in marked["caller_line"]
    assert marked["tool"] == "Held the booking until the safety lines were said."
    assert marked["reply"] == "Safety lines, word for word"


def test_a_server_note_says_why_it_was_added_and_shows_the_note_once(agent):
    """Jackson: the note step showed the same text twice with no reason."""
    call = agent.start(CALLER)["data"]["call_id"]
    agent.say(call, "Thanks for calling, how can I help?")
    agent.turn(call, "Hang on one sec, let me grab my glasses.")
    note = next(r for r in graph(agent, call)["nodes"] if r["kind"] == "server_note")
    assert note["summary"] == "Server added a note to the caller's line before the model saw it"
    assert dict(note["facts"])["Because"] == "the caller asked the agent to wait"
    assert [b["title"] for b in note["blocks"]] == ["Caller's line", "Note added"]
    assert note["blocks"][0]["text"] == "Hang on one sec, let me grab my glasses."


def test_what_was_cut_from_a_reply_is_struck_with_the_rule_that_cut_it(agent):
    call = agent.start(CALLER)["data"]["call_id"]
    agent.turn(call, "Hello?")
    body = {"speaker": "agent", "text": "What's the best number", "timings": {},
            "said": "What's the best number to reach you?",
            "cuts": [{"text": "I'm booking that now.", "reason": "narrates"}]}
    assert agent.client.post(f"/calls/{call}/turn", json=body).status_code == 200
    reply = next(r for r in graph(agent, call)["nodes"] if r["kind"] == "reply")
    assert reply["view"]["cut"] == [
        {"text": "to reach you?", "reason": "interrupted", "struck": True,
         "tag": "Caller talked over it"},
        {"text": "I'm booking that now.", "reason": "narrates", "struck": True,
         "tag": "Reply filter: narrated its own steps"}]
    page = admin(agent).client.get(f"/bench/calls/{key(call)}").text
    assert '<s class="cut">to reach you?</s> <span class="cut-tag">Caller talked over it</span>' in page


def test_an_older_call_rebuilds_its_greeting_to_show_where_it_was_cut(agent):
    """Call retell_call_44f21506eca3346bd3547be9de8, from before the socket posted `said`: the
    greeting was heard only as "Hi there, thanks for"."""
    call = agent.start(CALLER)["data"]["call_id"]
    agent.say(call, "Hi there, thanks for")
    agent.turn(call, "there.")
    reply = next(r for r in graph(agent, call)["nodes"] if r["kind"] == "reply")
    cut = reply["view"]["cut"][0]
    assert cut["reason"] == "interrupted" and cut["text"].startswith("calling Summit Air")
    assert "rebuilt" in reply["view"]["note"]


def test_the_rule_proof_counts_transfers_and_the_server_led_read_back(agent):
    """Rule proof on live calls: call_480748620a2ad8cec45d7c26c71's refused transfer and the
    server-led read-back were missing from the list."""
    call = agent.start(CALLER)["data"]["call_id"]
    agent.turn(call, "Can you route my call to the billing team?")
    refused = agent.tool("request_transfer", call, reason="person", summary="Caller asked.")
    assert not refused["ok"]
    proof = {p["rule"]: p for p in graph(agent, call)["proof"]}
    assert proof["Person only when asked"]["outcome"] == "fired"


DEMAND = "this is ridiculous the furnace you fixed died again put me through to a real person right now"


def why_rows(agent: Agent, call: str) -> list[dict]:
    import os

    conn = agent.db()
    try:
        found = trace.timeline(conn, call)
        g = nodes.build(conn, found, agent.app.state.services.settings.log_path)
        return bench_call.decisions(conn, found, g, agent.app.state.services.tenants.default,
                                    os.environ)
    finally:
        conn.close()


def test_the_why_tab_shows_the_transfer_to_a_person(agent, monkeypatch):
    """Jackson, call retell_call_e2d08b8735893e12274237d845e: the Why tab showed only the label
    and the end, not the transfer."""
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    monkeypatch.setenv("TRANSFER_ON_CALL_NUMBER", "+12125550111")
    monkeypatch.delenv("TRANSFER_HOLD_NUMBER", raising=False)
    call = agent.start(CALLER)["data"]["call_id"]
    plan = agent.turn(call, DEMAND)["data"]["handoff"]["transfer"]
    agent.client.post(f"/calls/{call}/handoff/{plan['id']}/done", json={"outcome": "handed_off"})
    rows = {r["what"]: r for r in why_rows(agent, call)}
    asked = rows["Caller asked for a person"]
    assert asked["why"].startswith("A demand") and asked["say"] == DEMAND
    rang = next(r for w, r in rows.items() if w.startswith("Rang "))
    assert rang["what"].endswith(("number ending 0100", "number ending 0111"))
    assert "no hold number was set" in rang["why"] and "Warm transfer on" in rang["why"]
    assert any(w.startswith("Person flag raised") for w in rows)
    assert any(w.startswith("Callback promised by ") for w in rows)
    assert rows["Handoff finished"]["result"].startswith("Handed off")
    page = admin(agent).client.get(f"/bench/calls/{key(call)}").text
    assert "Caller asked for a person" in page and "Handoff finished" in page


def test_with_the_hold_line_on_the_why_says_the_hold_line_rang(agent, monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    monkeypatch.setenv("TRANSFER_ON_CALL_NUMBER", "+12125550111")
    monkeypatch.setenv("TRANSFER_HOLD_NUMBER", "+12125550122")
    call = agent.start(CALLER)["data"]["call_id"]
    agent.turn(call, DEMAND)
    rang = next(r for r in why_rows(agent, call) if r["what"].startswith("Rang "))
    assert rang["what"] == "Rang the hold line, number ending 0122"
    assert "Hold line on for this call" in rang["why"]


def test_a_refused_transfer_is_a_why_row_with_the_rules_plain_reason(agent):
    call = agent.start(CALLER)["data"]["call_id"]
    agent.turn(call, "Can you route my call to the billing team?")
    agent.tool("request_transfer", call, reason="person", summary="Caller asked.")
    held = next(r for r in why_rows(agent, call) if r["kind"] == "Refused")
    assert held["why"] == nodes.GATE_TEXT["transfer_not_asked"][0]


def test_a_turn_takes_at_most_twenty_cuts(agent):
    call = agent.start(CALLER)["data"]["call_id"]
    body = {"speaker": "agent", "text": "Hi.", "timings": {},
            "cuts": [{"text": "x", "reason": "narrates"}] * 21}
    answer = agent.client.post(f"/calls/{call}/turn", json=body).json()
    assert answer["ok"] is False and answer["refused"] == "invalid_arguments"


def test_the_call_page_draws_the_graph_and_says_what_is_missing(agent):
    call = timed_call(agent)
    page = admin(agent).client.get(f"/bench/calls/{key(call)}").text
    assert 'data-node-graph' in page and 'id="d-n1"' in page
    assert "560 ms" in page
    steps = page[page.index("data-node-graph"):page.index("data-node-detail")]
    assert "missing" not in steps, "a timing the worker didn't send is a blank, not a word"
    assert "Rule proof" in page


def test_a_call_with_nothing_in_the_log_says_so(agent):
    call = timed_call(agent)
    agent.app.state.services.settings.log_path.unlink()
    g = graph(agent, call)
    assert not g["logged"]
    assert dict(g["nodes"][0]["facts"])["System prompt"].startswith("The /calls/start result")


def test_an_unknown_call_is_not_found(agent):
    admin(agent)
    assert agent.client.get("/bench/calls/nope").status_code == 404
    assert agent.client.get("/bench/calls/0123456789ab").status_code == 404


def test_a_call_is_addressed_by_a_key_that_carries_no_number(agent):
    call = timed_call(agent)
    assert "5550999" in call
    page = admin(agent).client.get("/bench").text
    assert f'href="/bench/calls/{key(call)}"' in page
    assert agent.client.get(f"/bench/calls/{call}").status_code == 404, "the raw id opens nothing"


# ---- calls list, live view -----------------------------------------------------------------

def test_calls_are_found_by_phone_digits_or_call_id(agent):
    call = timed_call(agent)
    other = agent.start("(917) 555-0100")["data"]["call_id"]
    admin(agent)
    response = agent.client.get("/bench?q=0999", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/bench/calls/{key(call)}"
    response = agent.client.get(f"/bench?q={other}", follow_redirects=False)
    assert response.headers["location"] == f"/bench/calls/{key(other)}"
    listed = agent.client.get("/bench").text
    assert f"/bench/calls/{key(call)}" in listed and f"/bench/calls/{key(other)}" in listed
    assert "<h2>Live</h2>" in listed, "neither call has ended"


def test_a_call_in_progress_streams_its_changes_until_the_cap_without_saying_it_ended(agent, monkeypatch):
    call = timed_call(agent)
    monkeypatch.setattr(bench, "STREAM_SECONDS", 0)
    body = admin(agent).client.get(f"/bench/calls/{key(call)}/stream").text
    assert body.startswith("retry: 1000\n\nevent: changed\ndata: {")
    assert "event: ended" not in body, "the browser reconnects; the call is still live"
    page = agent.client.get(f"/bench/calls/{key(call)}").text
    assert f'data-live="/bench/calls/{key(call)}/stream"' in page


def test_the_stream_cap_is_minutes_not_the_length_of_a_call():
    assert bench.STREAM_SECONDS == 5 * 60


def test_an_ended_call_stream_says_so_and_closes(agent):
    call = timed_call(agent)
    agent.end(call)
    body = admin(agent).client.get(f"/bench/calls/{key(call)}/stream").text
    assert body.count("event: changed") == 1 and body.rstrip().endswith("event: ended\ndata: {}")
    assert "data-live=" not in agent.client.get(f"/bench/calls/{key(call)}").text


# ---- replay as a scenario ------------------------------------------------------------------

def test_a_booked_call_becomes_a_valid_draft_scenario(agent):
    call = ready_to_book(agent, CALLER, in_area_address(2))
    assert book(agent, call, first_open(agent, call))["ok"]
    agent.end(call)
    response = admin(agent).client.get(f"/bench/calls/{key(call)}/scenario.yaml")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="replay-{key(call)}.yaml"'
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


def test_a_draft_from_a_call_id_with_a_number_names_neither_the_number_nor_the_id(agent):
    call = timed_call(agent)
    found = trace.timeline(agent.db(), call)
    text = bench_data.scenario_draft(found, redact, key(call))
    draft = yaml.safe_load(text)
    Scenario.model_validate(draft)
    assert draft["id"] == f"replay-{key(call)}"
    assert "5550999" not in text and "12125550999" not in text
    response = admin(agent).client.get(f"/bench/calls/{key(call)}/scenario.yaml")
    assert "5550999" not in response.headers["content-disposition"]


# ---- settings and evals --------------------------------------------------------------------

def test_every_voice_knob_reads_a_value_and_says_why():
    rows = bench_data.voice_knobs()
    assert len(rows) == len(bench_data.VOICE_KNOBS)
    for row in rows:
        assert row["value"] is not None, row["where"]
        assert row["does"] and row["why"] and row["source"], row["label"]
        assert row["shown"] not in ("", "-"), row["label"]


def test_the_settings_page_shows_live_values_and_never_a_secret(agent):
    page = admin(agent).client.get("/bench/settings").text
    config = yaml.safe_load(bench_data.MODELS.read_text())
    assert f"{config['voice']['hold_phrase_after_s']:g} s" in page
    live = config["profiles"][config["default"]]["model"]
    assert live in page and "Voice knobs" in page and "Why this value" in page
    assert "test-token" not in page, "the debug token shows only as set"


def test_the_evals_page_shows_each_run_against_the_floor(agent):
    page = admin(agent).client.get("/bench/evals").text
    suites = [r for r in bench_data.runs() if len(r.scenarios) >= bench_data.SUITE_MIN]
    assert f"/bench/evals/{suites[0].run_id}" in page
    small = next(r for r in bench_data.runs() if len(r.scenarios) < bench_data.SUITE_MIN)
    assert f"/bench/evals/{small.run_id}" not in page, "a rerun of a scenario or two waits for All"
    assert f"/bench/evals/{small.run_id}" in agent.client.get("/bench/evals?runs=all").text
    assert 'class="floor"' in page and "Floor 90%" in page
    assert "Reference" in page
    commercial = agent.client.get("/bench/evals?tested=commercial").text
    assert "Pass rate, commercial calls" in commercial


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


def test_a_model_reply_joined_after_the_safety_lines_keeps_the_safety_proof():
    """Review of PR #194: a joined row whose later part came from the model lost the proof tag."""
    def part(source, text, tone):
        return {"kind": "spoken", "source": source, "summary": text, "tone": tone, "view": {},
                "ms": None, "ms_label": "", "lane": "voice", "col": 1}

    row = nodes._reply_row([part("fixed", "If you're smelling gas, get everyone outside.", "warn"),
                            part("model", "Do you want me to connect you?", "")])
    assert row["proof"] == "Safety lines, word for word"
    assert ("Source", "the server's fixed words, then the model") in row["facts"]
