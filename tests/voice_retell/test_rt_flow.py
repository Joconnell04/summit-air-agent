"""The Retell Conversation Flow agent (voice/retell_flow/): the graph keeps every gate on the
server's side, the bridge routes the call from the server's answers, and the push is idempotent."""

import pytest
import yaml
from fake_retell import FakeRetell
from fastapi.testclient import TestClient
from retell_kit import KEY, NOW_MS, SPOKEN, call_obj, inbound, signed, utterance
from test_rt_bridge import GREETING, HEAT_OUT, WHERE, booked_call, rows
from test_rt_push import BASE, VOICE, account, specs
from toolkit import TENANT_DIR, in_area_address

from app.tools import TOOLS
from voice.retell.api import RetellError, allowed
from voice.retell.ledger import Ledger
from voice.retell.push import PHONE_NUMBER
from voice.retell_flow import build
from voice.retell_flow.bridge import ROUTING, FlowBridge, FlowToolServer, build_flow_app
from voice.retell_flow.eval_adapter import FlowChatAgent
from voice.retell_flow.push import push

MODEL = "claude-haiku-5-5"
TENANT = build.Tenant(server_down=SPOKEN["server_down"], ring_ms=20000, detect_ms=10000)
SAFETY = yaml.safe_load((TENANT_DIR / "agent.yaml").read_text())["safety"]
GAS_LINES = " ".join(SAFETY["kinds"]["gas"]["lines"].split())


def flow(chat: bool = False) -> dict:
    return build.flow_body(specs(), BASE, MODEL, TENANT, chat)


def by_id(body: dict) -> dict[str, dict]:
    return {n["id"]: n for n in body["nodes"]}


# ---- the graph ---------------------------------------------------------------------------------


@pytest.mark.parametrize("chat", [False, True])
def test_every_node_is_reachable_and_every_edge_lands_on_a_node(chat):
    body = flow(chat)
    nodes = by_id(body)
    assert build.reachable(body) == set(nodes)
    for node in body["nodes"]:
        for edge in build.outgoing(node):
            assert edge["destination_node_id"] in nodes, (node["id"], edge)


@pytest.mark.parametrize("chat", [False, True])
def test_end_and_transfer_nodes_are_reached_only_by_a_route_the_server_set(chat):
    body = flow(chat)
    nodes = by_id(body)
    guarded = {
        i for i, n in nodes.items() if n["type"] in ("end", "transfer_call") or i.endswith("_line")
    }
    # Retell's own record that a function call failed; set by Retell, not by the model.
    status = {"{{turn_status}}", "{{failed_status}}"}
    for node in body["nodes"]:
        assert "global_node_setting" not in node, node["id"]
        for edge in build.outgoing(node):
            target = edge["destination_node_id"]
            if target not in guarded:
                continue
            condition = edge["transition_condition"]
            # A hand-off line leads to its own transfer once said; a failed report falls back to the
            # server's callback line.
            if condition == {"type": "prompt", "prompt": "Skip response"}:
                assert node["id"].endswith("_line") and nodes[target]["type"] == "transfer_call"
                continue
            if condition == {"type": "prompt", "prompt": "Else"}:
                assert node["type"] == "function" and target == "fallback_line", node["id"]
                continue
            assert condition["type"] == "equation", (node["id"], target)
            assert condition["equations"][0]["left"] in {"{{route}}"} | status


def test_every_route_has_an_edge_from_the_turn_check_and_from_every_stage():
    nodes = by_id(flow())
    first, *routes = nodes["turn_check"]["edges"]
    # A failed turn check is matched before any route an earlier turn left behind.
    assert first["transition_condition"]["equations"][0]["left"] == "{{turn_status}}"
    assert first["destination_node_id"] == "server_down"
    from_turn = {
        e["transition_condition"]["equations"][0]["right"]: e["destination_node_id"] for e in routes
    }
    assert from_turn == build.ROUTES_FROM_TURN
    for stage in build.STAGES:
        routes = {
            e["transition_condition"]["equations"][0]["right"]: e["destination_node_id"]
            for e in nodes[stage]["edges"]
        }
        assert routes == build.ROUTES_FROM_TOOLS
        # A caller line always goes back through the turn check before the next reply.
        assert nodes[stage]["else_edge"]["destination_node_id"] == "turn_check"


def test_the_server_lines_are_static_sentences_and_the_safety_lines_cant_be_interrupted():
    nodes = by_id(flow())
    for node_id in ("safety_line", "server_line", "firm_line", "notice_line", "handoff_line"):
        assert nodes[node_id]["instruction"] == {"type": "static_text", "text": "{{line}}"}
    for node_id in ("safety_line", "firm_line", "handoff_line", "transfer_line", "fallback_line"):
        assert nodes[node_id]["interruption_sensitivity"] == 0
    # After the recording line the model answers the rest of what the caller said.
    assert nodes["notice_line"]["skip_response_edge"]["destination_node_id"] == "stage"
    assert nodes["server_down"]["instruction"]["text"] == SPOKEN["server_down"]
    assert nodes["end_with_line"]["instruction"]["text"] == "{{line}}"
    assert nodes["end_quiet"]["speak_during_execution"] is False


def test_only_our_tools_are_attached_and_the_stage_nodes_get_only_the_model_tools():
    body = flow()
    assert {t["type"] for t in body["tools"]} == {"custom"}
    assert all(t["url"].startswith(BASE + "/retell/flow/") for t in body["tools"])
    model_tools = {f"tool_{name}" for name in TOOLS}
    for stage in build.STAGES:
        assert set(by_id(body)[stage]["tool_ids"]) == model_tools
    assert not any(n["type"] == "subagent" and n.get("tools") for n in body["nodes"])


def test_the_prompt_and_model_are_the_ones_our_worker_uses():
    body = flow()
    assert body["global_prompt"] == "{{instructions}}"
    assert body["model_config"]["primary"]["model"] == "claude-5.5-haiku"
    assert body["model_config"]["temperature"] == 0.3
    for stage in build.STAGES:
        assert "{{notes}}" in by_id(body)[stage]["instruction"]["text"]


def test_the_warm_transfer_connects_only_a_person_and_whispers_the_servers_summary():
    option = by_id(flow())["transfer_call"]["transfer_option"]
    assert option["type"] == "warm_transfer"
    assert option["opt_out_human_detection"] is False
    assert option["private_handoff_option"]["message"] == "{{transfer_summary}}"
    assert by_id(flow())["handoff_call"]["transfer_option"]["type"] == "cold_transfer"


def test_a_failed_transfer_report_still_says_the_callback_line():
    nodes = by_id(flow())
    for node_id in ("handoff_failed", "transfer_failed"):
        down = nodes[node_id]["edges"][0]
        assert down["transition_condition"]["equations"][0]["left"] == "{{failed_status}}"
        assert down["destination_node_id"] == "fallback_line"
    assert nodes["fallback_line"]["instruction"]["text"] == "{{fallback}}"


def test_a_chat_starts_at_the_turn_check_and_has_no_transfer():
    body = flow(chat=True)
    assert (body["start_node_id"], body["start_speaker"]) == ("turn_check", "user")
    assert not any(n["type"] == "transfer_call" for n in body["nodes"])
    voice = flow()
    assert (voice["start_node_id"], voice["start_speaker"]) == ("greeting", "agent")


# ---- the bridge --------------------------------------------------------------------------------


@pytest.fixture
def flow_bridge(tool_app, tmp_path, retell, later):
    return FlowBridge(
        KEY,
        FlowToolServer(TestClient(tool_app, raise_server_exceptions=False)),
        Ledger(tmp_path / "flow.db"),
        retell,
        instructions=lambda start: "INSTRUCTIONS",
        spoken=lambda slug: SPOKEN,
        notes=lambda hints, known=None: [f"[server] {h}" for h in hints if h == "commercial"],
        default_tenant="summit-air",
        tool_names=set(TOOLS),
        now_ms=lambda: NOW_MS,
        later=later,
    )


@pytest.fixture
def flow_client(flow_bridge):
    return TestClient(build_flow_app(flow_bridge))


def turn(client, obj: dict) -> dict:
    response = signed(client, "/retell/flow/turn", {"name": "caller_turn", "call": obj, "args": {}})
    assert response.status_code == 200, response.text
    return response.json()


def flow_tool(client, name: str, obj: dict, **args) -> dict:
    body = {"name": name, "call": obj, "args": args}
    response = signed(client, f"/retell/flow/tools/{name}", body)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_flow_routes_are_signed(flow_client):
    assert flow_client.post("/retell/flow/turn", json={}).status_code == 401
    assert flow_client.post("/retell/flow/tools/read_back", json={}).status_code == 401


def test_a_gas_line_routes_to_the_safety_lines_word_for_word(flow_client, tool_app):
    inbound(flow_client, "gas")
    gas = utterance("user", "Hang on, I think I smell gas in the kitchen.")
    answer = turn(flow_client, call_obj("gas", GREETING, gas))
    assert answer["route"] == "safety"
    assert answer["line"] == GAS_LINES
    assert set(ROUTING) <= set(answer)
    flags = rows(tool_app, "SELECT kind FROM flags WHERE call_id = 'retell_gas'")
    assert {"kind": "safety"} in flags


def test_after_the_lines_are_said_the_server_sees_them_and_the_call_goes_on(flow_client, tool_app):
    inbound(flow_client, "gas")
    gas = utterance("user", "I smell gas.")
    turn(flow_client, call_obj("gas", GREETING, gas))
    said = utterance("agent", GAS_LINES)
    answer = turn(flow_client, call_obj("gas", GREETING, gas, said, utterance("user", "Okay.")))
    assert answer["route"] == "model"
    call = rows(tool_app, "SELECT safety_state FROM calls WHERE id = 'retell_gas'")[0]
    assert call["safety_state"] == "lines_said"


def test_a_plain_line_stays_with_the_model_with_the_servers_stage_and_notes(flow_client):
    inbound(flow_client, "abc")
    answer = turn(flow_client, call_obj("abc", GREETING, HEAT_OUT))
    assert (answer["route"], answer["line"], answer["stage"]) == ("model", "", "opening")
    office = utterance("user", "I manage a restaurant on Atlantic Avenue and the AC died.")
    answer = turn(flow_client, call_obj("abc", GREETING, HEAT_OUT, office))
    assert answer["notes"].startswith("[server] This call can't book.")


def test_a_caller_line_the_server_cant_take_ends_the_call_on_the_server_down_line(
    tool_app, tmp_path, retell, later
):
    class Down(FlowToolServer):
        def turn(self, *args):
            return None

    down = FlowBridge(
        KEY, Down(TestClient(tool_app)), Ledger(tmp_path / "down.db"), retell,
        instructions=lambda start: "", spoken=lambda slug: SPOKEN, notes=lambda h, k=None: [],
        default_tenant="summit-air", tool_names=set(TOOLS), now_ms=lambda: NOW_MS, later=later,
    )
    answer = down.turn_check({"call": call_obj("abc", GREETING, HEAT_OUT)})
    assert (answer["route"], answer["line"]) == ("end_line", SPOKEN["server_down"])


def test_a_turn_check_with_no_new_caller_line_keeps_the_stage(flow_client):
    said = booked_call(flow_client)
    now = call_obj("abc", *said, utterance("user", "Great, what times?"))
    assert turn(flow_client, now)["stage"] == "booking"
    again = turn(flow_client, now)
    assert (again["route"], again["stage"]) == ("model", "booking")


def test_a_caller_who_objects_to_recording_hears_the_line_and_retell_keeps_nothing(
    flow_client, retell
):
    inbound(flow_client, "abc")
    objection = utterance("user", "Please don't record this call. My heat is out.")
    answer = turn(flow_client, call_obj("abc", GREETING, objection))
    assert (answer["route"], answer["line"]) == ("notice", "Okay, I've turned the recording off.")
    assert retell.unrecorded == ["abc"]


def test_a_demand_with_no_number_set_gets_the_callback_line_uninterrupted(flow_client, monkeypatch):
    monkeypatch.delenv("TRANSFER_OFFICE_NUMBER", raising=False)
    inbound(flow_client, "mad")
    demand = utterance("user", "Put me through to a manager right now, this is ridiculous.")
    answer = turn(flow_client, call_obj("mad", GREETING, demand))
    assert answer["route"] == "firm" and "call you" in answer["line"]


def test_the_read_back_is_the_servers_sentence_said_from_a_static_node(flow_client):
    inbound(flow_client, "abc")
    answer = flow_tool(
        flow_client,
        "read_back",
        call_obj("abc", GREETING, HEAT_OUT, WHERE),
        address=in_area_address(),
        phone="caller_id",
    )
    assert answer["ok"], answer
    assert (answer["route"], answer["line"]) == ("line", answer["say"])


def test_a_tool_the_model_speaks_about_clears_the_route(flow_client):
    inbound(flow_client, "abc")
    answer = flow_tool(flow_client, "check_service_area", call_obj("abc", GREETING, WHERE), zip="11231")
    assert answer["ok"]
    assert (answer["route"], answer["line"]) == ("model", "")
    assert set(ROUTING) <= set(answer)


def test_the_stage_moves_to_booking_on_the_servers_records(flow_client):
    said = booked_call(flow_client)
    answer = turn(flow_client, call_obj("abc", *said, utterance("user", "Great, what times?")))
    assert answer["stage"] == "booking"


def test_end_call_reaches_an_end_node_only_when_the_server_hangs_up(flow_client):
    inbound(flow_client, "gas")
    gas = utterance("user", "I smell gas.")
    turn(flow_client, call_obj("gas", GREETING, gas))
    # Before the lines are said the server refuses, and its line is said instead of hanging up.
    early = flow_tool(flow_client, "end_call", call_obj("gas", GREETING, gas), reason="caller_goodbye")
    assert not early["ok"] and early["route"] == "line"
    said = [GREETING, gas, utterance("agent", GAS_LINES), utterance("user", "Okay, bye.")]
    ended = flow_tool(flow_client, "end_call", call_obj("gas", *said), reason="caller_goodbye")
    assert ended["ok"] and ended["route"] == "end_line"
    assert ended["line"] == " ".join(SAFETY["sign_off"].split())


def test_an_ordinary_goodbye_ends_quietly_after_the_agents_own_words(flow_client):
    inbound(flow_client, "abc")
    bye = utterance("user", "Never mind, wrong number, bye.")
    ended = flow_tool(flow_client, "end_call", call_obj("abc", GREETING, bye), reason="caller_goodbye")
    assert (ended["route"], ended["line"]) == ("end", "")


def test_a_demand_for_a_person_is_handed_off_through_the_transfer_node(
    flow_client, tool_app, monkeypatch
):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    inbound(flow_client, "mad")
    demand = utterance("user", "Put me through to a manager right now, this is ridiculous.")
    answer = turn(flow_client, call_obj("mad", GREETING, demand))
    assert answer["route"] == "handoff"
    assert answer["transfer_number"] == "+12125550100"
    assert answer["handoff_id"].isdigit()
    assert "call you" in answer["fallback"]
    # Nobody picks up: the server's callback line is said next.
    body = {"name": "handoff_failed", "call": call_obj("mad"), "args": {"handoff_id": answer["handoff_id"]}}
    failed = signed(flow_client, "/retell/flow/handoff-failed", body).json()
    assert failed["route"] == "line" and "call you" in failed["line"]
    status = rows(tool_app, "SELECT status FROM handoffs WHERE call_id = 'retell_mad'")
    assert status == [{"status": "failed"}]


def test_a_failed_transfer_report_with_no_line_falls_back_without_wiping_the_fallback(flow_bridge):
    flow_bridge.tools.handoff_done = lambda *args: None
    answer = flow_bridge.transfer_failed("handoff", {"call": call_obj("x"), "args": {"handoff_id": "7"}})
    assert answer["route"] == "fallback"
    assert "fallback" not in answer
    failed_tool = next(t for t in flow()["tools"] if t["name"] == "handoff_failed")
    assert "fallback" not in failed_tool["response_variables"]


def test_if_retell_would_keep_the_recording_the_line_isnt_said(flow_client, retell):
    def refuse(call_id):
        raise RetellError("refused")

    retell.keep_no_recording = refuse
    inbound(flow_client, "abc")
    objection = utterance("user", "Please don't record this call. My heat is out.")
    assert turn(flow_client, call_obj("abc", GREETING, objection))["route"] == "model"


def test_a_transfer_that_goes_through_is_reported_when_the_call_ends(flow_client, tool_app, monkeypatch):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    inbound(flow_client, "mad")
    demand = utterance("user", "Put me through to a manager right now, this is ridiculous.")
    turn(flow_client, call_obj("mad", GREETING, demand))
    ended = call_obj("mad", GREETING, demand, disconnection_reason="call_transfer")
    response = signed(flow_client, "/retell/webhook", {"event": "call_ended", "call": ended})
    assert response.status_code == 204
    status = rows(tool_app, "SELECT status FROM handoffs WHERE call_id = 'retell_mad'")
    assert status == [{"status": "handed_off"}]


def test_in_a_chat_a_hand_off_ends_the_chat_on_the_line_as_the_harness_reports_it(
    flow_client, tool_app, monkeypatch
):
    monkeypatch.setenv("TRANSFER_OFFICE_NUMBER", "+12125550100")
    chat = {
        "chat_id": "c1",
        "retell_llm_dynamic_variables": {"summit_call_id": "eval-mad"},
        "message_with_tool_calls": [
            {"role": "user", "content": "Put me through to a manager right now, this is ridiculous."}
        ],
    }
    response = signed(flow_client, "/retell/flow/turn", {"name": "caller_turn", "chat": chat})
    answer = response.json()
    assert answer["route"] == "end_line" and answer["line"]
    status = rows(tool_app, "SELECT status FROM handoffs WHERE call_id = 'eval-mad'")
    assert status == [{"status": "handed_off"}]


# ---- the push ----------------------------------------------------------------------------------


def run_push(fake: FakeRetell, **kwargs):
    return push(fake.client(), specs(), BASE, MODEL, VOICE, TENANT, analysis_model="gpt-5.4-nano",
                **kwargs)


def test_a_first_push_creates_the_flows_and_agents_and_leaves_the_number_alone():
    fake = account()
    _, ids = run_push(fake, eval_url=BASE + "/eval")
    assert len(fake.flows) == 2
    agent = fake.agents[ids["agent"]]
    assert agent["response_engine"]["type"] == "conversation-flow"
    assert agent["webhook_events"] == ["call_started", "call_ended", "call_analyzed"]
    chat = fake.agents[ids["chat_agent"]]
    assert fake.flows[chat["response_engine"]["conversation_flow_id"]]["start_node_id"] == "turn_check"
    number = fake.numbers[0]
    assert number["inbound_agents"][0]["agent_id"] != ids["agent"]


def test_a_second_push_sends_no_writes_and_only_allowed_endpoints():
    fake = account()
    run_push(fake, eval_url=BASE + "/eval")
    before = len(fake.writes())
    log, _ = run_push(fake, eval_url=BASE + "/eval")
    assert log == [] and len(fake.writes()) == before
    for method, path, _ in fake.requests:
        assert allowed(method, path), (method, path)


def test_a_response_variable_dropped_from_a_tool_is_dropped_on_retell():
    fake = account()
    _, ids = run_push(fake)
    flow_id = fake.agents[ids["agent"]]["response_engine"]["conversation_flow_id"]
    stale = next(t for t in fake.flows[flow_id]["tools"] if t["name"] == "handoff_failed")
    stale["response_variables"]["fallback"] = "fallback"
    log, _ = run_push(fake)
    assert log == [f"updated flow {flow_id}: tools"]
    fresh = next(t for t in fake.flows[flow_id]["tools"] if t["name"] == "handoff_failed")
    assert "fallback" not in fresh["response_variables"]


def test_bind_number_puts_the_flow_agent_on_678():
    fake = account()
    _, ids = run_push(fake, bind_number=True)
    number = next(n for n in fake.numbers if n["phone_number"] == PHONE_NUMBER)
    assert number["inbound_agents"][0]["agent_id"] == ids["agent"]
    assert number["inbound_webhook_url"] == BASE + "/retell/inbound"


# ---- the eval adapter --------------------------------------------------------------------------


class Ended:
    status_code = 400

    def raise_for_status(self):
        raise AssertionError("a chat the end node closed is not an error")


class Http:
    def patch(self, path):
        return Ended()


def test_ending_a_chat_the_end_node_already_closed_is_not_an_error():
    agent = FlowChatAgent(Http(), None, "+12125550999", "eval-x", {})
    agent.end("agent_end")


def test_a_caller_hanging_up_is_recorded_as_the_callers_end_before_the_chat_closes():
    ended = []

    class Server:
        def end(self, call_id, reason):
            ended.append((call_id, reason))

    agent = FlowChatAgent(Http(), None, "+12125550999", "eval-x", {})
    agent.server = Server()
    agent.end("caller_hangup")
    assert ended == [("eval-x", "caller_hangup")]
