"""The Retell Conversation Flow agent as code: the request bodies push.py sends. Pure functions of
their inputs, so the tests can check the graph without Retell.

The map behind it is docs/explorations/017-retell-conversation-flow.md. In short:

- turn_check, a function node, runs on every caller line before the agent answers. It posts the
  line to our /calls/{id}/turn through the bridge and returns where the call goes next as dynamic
  variables: `route`, `line`, `stage`, `notes`, and for a transfer its number, id and fallback.
- Every line the server speaks word for word is a static-sentence node saying `{{line}}`.
- The stage nodes (opening, booking, closing) are subagent nodes with every model tool. The bridge
  adds `route` and `line` to every tool answer, and equation edges on `route` move the call to a
  static line, an end node or a transfer as soon as the answer is back.
- The end and transfer nodes are reachable only through an edge on a value the bridge set from the
  server's answer, or on Retell's own record that the bridge didn't answer (the server-down end).
  No model choice leads to them, and Retell's own end-call and transfer tools are attached nowhere.

The prompt is the same per-call `{{instructions}}` our worker reads (prompts/assemble.py); the model
and voice come from config/models.yaml through voice/retell/build.py.
"""

from dataclasses import dataclass

from voice.retell import build as single

AGENT_NAME = "Summit Air (Retell flow)"
CHAT_AGENT_NAME = "Summit Air chat (Retell flow, evals)"

# The values `route` can take, and the node each one leads to.
ROUTES_FROM_TURN = {
    "safety": "safety_line",
    "handoff": "handoff_line",
    "line": "server_line",
    # A line said before the model answers the rest of what the caller said (recording stopped).
    "notice": "notice_line",
    # A line the caller can't talk over, with no transfer after it (a demand with no number).
    "firm": "firm_line",
    "end_line": "end_with_line",
}
ROUTES_FROM_TOOLS = {
    "line": "server_line",
    "end_line": "end_with_line",
    "end": "end_quiet",
    "transfer": "transfer_line",
}
STAGES = ("opening", "booking", "closing")
# Variables the bridge sets. Defaults make the first turn check's edges well defined.
VARIABLES = {
    "route": "model",
    "line": "",
    "stage": "opening",
    "notes": "",
    "transfer_number": "",
    "transfer_id": "",
    "transfer_summary": "",
    "handoff_id": "",
    "fallback": "",
}
# What a tool answer sets. Only the turn check sets the stage and the notes.
ROUTING = tuple(k for k in VARIABLES if k not in ("stage", "notes"))
# Retell's own "success" or "failure" for a function node's call, so a timeout or an unreachable
# bridge can't leave an earlier answer's route in place.
STATUS = {"turn_status": "", "failed_status": ""}

# What each stage node adds to the shared prompt. The server picks the stage (app/rules.py
# stage_now); the model is never asked which stage it is in.
STAGE_NOTES = {
    "opening": (
        "Where the call is: the opening. Listen, find out what's wrong, and follow the steps in "
        "order of need from your instructions."
    ),
    "booking": (
        "Where the call is: booking. Offer times, read the details back with read_back, and book, "
        "move or cancel a visit."
    ),
    "closing": (
        "Where the call is: closing. Say what happens next, ask once if there's anything else, and "
        "call end_call when the caller is done. A second problem goes back to the steps in your "
        "instructions."
    ),
}
SERVER_SPEAKS = (
    "The sentences read_back, book_appointment, reschedule_appointment and cancel_appointment "
    "return are said to the caller for you, word for word. Call them without saying anything first "
    "in the same reply, and don't repeat what they said. On a safety call the same holds for "
    "end_call's sign-off: add no goodbye of your own. On any other call, say the goodbye from your "
    "instructions and then call end_call."
)

TURN_TOOL = "caller_turn"


@dataclass(frozen=True)
class Tenant:
    """What the graph needs from the tenant's agent.yaml, read by push.py."""

    server_down: str
    ring_ms: int
    detect_ms: int


def stage_instruction(stage: str) -> str:
    return "\n\n".join(
        (
            STAGE_NOTES[stage],
            SERVER_SPEAKS,
            "[server] notes for this turn, from the server's records (empty when there are none):",
            "{{notes}}",
        )
    )


def _equation(variable: str, value: str) -> dict:
    return {
        "type": "equation",
        "operator": "&&",
        "equations": [{"left": "{{" + variable + "}}", "operator": "==", "right": value}],
    }


def _edge(edge_id: str, condition: dict, destination: str) -> dict:
    return {"id": edge_id, "transition_condition": condition, "destination_node_id": destination}


def _special(edge_id: str, prompt: str, destination: str) -> dict:
    """Retell's else, always and skip-response edges are prompt conditions with a fixed text."""
    return _edge(edge_id, {"type": "prompt", "prompt": prompt}, destination)


def _route_edges(prefix: str, routes: dict[str, str]) -> list[dict]:
    return [
        _edge(f"{prefix}_{route}", _equation("route", route), node) for route, node in routes.items()
    ]


def _static(node_id: str, name: str, text: str, **extra) -> dict:
    return {
        "id": node_id,
        "type": "conversation",
        "name": name,
        "instruction": {"type": "static_text", "text": text},
        **extra,
    }


def _custom_tool(
    tool_id: str,
    name: str,
    description: str,
    url: str,
    parameters: dict,
    variables: tuple[str, ...] = ROUTING,
    status: str | None = None,
) -> dict:
    """A custom function posting to the bridge. The answer's routing fields become dynamic
    variables, so the edges can read them."""
    tool = {
        "tool_id": tool_id,
        "type": "custom",
        "name": name,
        "description": description,
        "url": url,
        "method": "POST",
        "parameters": parameters,
        "args_at_root": False,
        "response_variables": {key: key for key in variables},
        "speak_during_execution": False,
        # The model speaks about a result unless an edge on `route` moves the call first, which it
        # does whenever the server sent a line (exploration 017, "What the prototype showed").
        "speak_after_execution": True,
        "timeout_ms": 10000,
        # Safe to retry: every write carries an Idempotency-Key from call, tool and arguments.
        "max_retry": 1,
    }
    if status:
        tool["execution_status_variable"] = status
    return tool


def flow_tools(specs: list[dict], base_url: str) -> list[dict]:
    """The model tools, then the three the flow's function nodes call."""
    tools = [
        _custom_tool(
            f"tool_{spec['name']}",
            spec["name"],
            spec["description"],
            f"{base_url}/retell/flow/tools/{spec['name']}",
            single.retell_schema(spec["parameters"]),
        )
        for spec in specs
    ]
    tools.append(
        _custom_tool(
            "tool_caller_turn",
            TURN_TOOL,
            "Posts the caller's line to the server's turn check. Called by the flow, not the model.",
            f"{base_url}/retell/flow/turn",
            {"type": "object", "properties": {}},
            variables=tuple(VARIABLES),
            status="turn_status",
        )
    )
    for route, key in (("handoff", "handoff_id"), ("transfer", "transfer_id")):
        tools.append(
            _custom_tool(
                f"tool_{route}_failed",
                f"{route}_failed",
                "Tells the server a transfer wasn't answered. Called by the flow, not the model.",
                f"{base_url}/retell/flow/{route}-failed",
                {"type": "object", "properties": {key: {"type": "string", "const": "{{" + key + "}}"}}},
                # The fallback the plan came with stays as it was, whatever this answer holds.
                variables=tuple(k for k in ROUTING if k != "fallback"),
                status="failed_status",
            )
        )
    return tools


def nodes(model_tool_ids: list[str], tenant: Tenant, chat: bool) -> list[dict]:
    """The graph. `chat` leaves out the greeting (a Retell chat starts with the caller) and the
    transfer nodes (a chat can't transfer)."""
    graph = []
    if not chat:
        graph.append(
            _static(
                "greeting",
                "Greeting",
                "{{greeting}}",
                else_edge=_special("greeting_else", "Else", "turn_check"),
            )
        )
    graph.append(
        {
            "id": "turn_check",
            "type": "function",
            "name": "Turn check",
            "tool_id": "tool_caller_turn",
            "tool_type": "local",
            "wait_for_result": True,
            "speak_during_execution": False,
            # Equation edges are checked top to bottom: a failed call ends the call before any
            # route left from an earlier turn can match, as our worker ends when the server is down.
            "edges": [_edge("turn_failed", _equation("turn_status", "failure"), "server_down")]
            + _route_edges("turn", ROUTES_FROM_TURN),
            "else_edge": _special("turn_else", "Else", "stage"),
        }
    )
    graph.append(
        {
            "id": "stage",
            "type": "branch",
            "name": "Stage",
            "edges": [
                _edge(f"stage_{s}", _equation("stage", s), s) for s in STAGES if s != "opening"
            ],
            "else_edge": _special("stage_else", "Else", "opening"),
        }
    )
    for stage in STAGES:
        graph.append(
            {
                "id": stage,
                "type": "subagent",
                "name": stage.capitalize(),
                "instruction": {"type": "prompt", "text": stage_instruction(stage)},
                "tool_ids": model_tool_ids,
                "edges": _route_edges(stage, ROUTES_FROM_TOOLS),
                "else_edge": _special(f"{stage}_else", "Else", "turn_check"),
            }
        )
    graph.append(
        _static(
            "safety_line",
            "Safety lines",
            "{{line}}",
            # The caller can't talk over the safety lines, as on our worker, and a static line
            # entered mid-utterance can otherwise lose the floor (exploration 017).
            interruption_sensitivity=0,
            else_edge=_special("safety_else", "Else", "turn_check"),
        )
    )
    graph.append(
        _static(
            "server_line",
            "Server line",
            "{{line}}",
            else_edge=_special("line_else", "Else", "turn_check"),
        )
    )
    graph.append(
        _static(
            "firm_line",
            "Server line, not interruptible",
            "{{line}}",
            interruption_sensitivity=0,
            else_edge=_special("firm_else", "Else", "turn_check"),
        )
    )
    graph.append(
        _static(
            "notice_line",
            "Notice, then the model answers",
            "{{line}}",
            # Its only way out is the skip-response edge, so the caller can't talk it off the floor.
            interruption_sensitivity=0,
            skip_response_edge=_special("notice_skip", "Skip response", "stage"),
        )
    )
    graph.append(
        {
            "id": "end_with_line",
            "type": "end",
            "name": "End with the server's line",
            "speak_during_execution": True,
            "instruction": {"type": "static_text", "text": "{{line}}"},
        }
    )
    graph.append(
        {
            "id": "end_quiet",
            "type": "end",
            "name": "End after the agent's goodbye",
            "speak_during_execution": False,
        }
    )
    graph.append(
        {
            "id": "server_down",
            "type": "end",
            "name": "Server down",
            "speak_during_execution": True,
            "instruction": {"type": "static_text", "text": tenant.server_down},
        }
    )
    if not chat:
        graph += _transfer_nodes(tenant)
    else:
        # A chat can't transfer and the bridge never sends a chat these routes; the nodes keep
        # every route value pointing somewhere.
        for route in ("handoff", "transfer"):
            graph.append(
                _static(
                    f"{route}_line",
                    f"{route.capitalize()} line",
                    "{{line}}",
                    else_edge=_special(f"{route}_line_else", "Else", "turn_check"),
                )
            )
    return graph


def _transfer_nodes(tenant: Tenant) -> list[dict]:
    """A cold handoff for a caller who demanded a person, and a warm transfer with the server's
    summary after a person flag (app/transfer.py decides both). A transfer nobody answers comes
    back through a function node that tells the server, and the caller hears its callback line; if
    that call fails too, the fallback line the server sent with the plan."""
    out = []
    graph_fallback = _static(
        "fallback_line",
        "Callback line",
        "{{fallback}}",
        interruption_sensitivity=0,
        else_edge=_special("fallback_else", "Else", "turn_check"),
    )
    for route in ("handoff", "transfer"):
        call_node, failed_node = f"{route}_call", f"{route}_failed"
        out.append(
            _static(
                f"{route}_line",
                f"{route.capitalize()} line",
                "{{line}}",
                interruption_sensitivity=0,
                skip_response_edge=_special(f"{route}_line_skip", "Skip response", call_node),
            )
        )
        if route == "handoff":
            option = {"type": "cold_transfer", "transfer_ring_duration_ms": tenant.ring_ms}
        else:
            # Retell connects the caller only once a person is detected, and the whisper is said
            # only to the person answering (call-transfer-node docs).
            option = {
                "type": "warm_transfer",
                "transfer_ring_duration_ms": tenant.ring_ms,
                "opt_out_human_detection": False,
                "agent_detection_timeout_ms": tenant.detect_ms,
                "private_handoff_option": {
                    "type": "static_message",
                    "message": "{{transfer_summary}}",
                },
            }
        out.append(
            {
                "id": call_node,
                "type": "transfer_call",
                "name": f"{route.capitalize()} to a person",
                "transfer_destination": {"type": "predefined", "number": "{{transfer_number}}"},
                "transfer_option": option,
                "edge": _special(f"{route}_call_failed", "Transfer failed", failed_node),
            }
        )
        out.append(
            {
                "id": failed_node,
                "type": "function",
                "name": f"{route.capitalize()} not answered",
                "tool_id": f"tool_{route}_failed",
                "tool_type": "local",
                "wait_for_result": True,
                "speak_during_execution": False,
                "edges": [
                    _edge(f"{route}_failed_down", _equation("failed_status", "failure"), "fallback_line"),
                    _edge(f"{route}_failed_line", _equation("route", "line"), "server_line"),
                ],
                "else_edge": _special(f"{route}_failed_else", "Else", "fallback_line"),
            }
        )
    out.append(graph_fallback)
    return out


def flow_body(specs: list[dict], base_url: str, model_id: str, tenant: Tenant, chat: bool) -> dict:
    model_tool_ids = [f"tool_{spec['name']}" for spec in specs]
    return {
        "model_config": {
            "type": "cascading",
            "primary": {"model": single.retell_model(model_id), "tier": "standard"},
            "temperature": single.TEMPERATURE,
        },
        "global_prompt": "{{instructions}}",
        "start_speaker": "user" if chat else "agent",
        "start_node_id": "turn_check" if chat else "greeting",
        "default_dynamic_variables": dict(VARIABLES) | STATUS,
        "tools": flow_tools(specs, base_url),
        "nodes": nodes(model_tool_ids, tenant, chat),
    }


def agent_body(flow_id: str, base_url: str, voice_id: str, voice_model: str, names=None) -> dict:
    """The baseline's voice settings, with the flow as its brain. No transcript_updated webhook:
    every caller line reaches the server through turn_check, in order, before the reply."""
    body = single.agent_body("", base_url, voice_id, voice_model, names)
    body["agent_name"] = AGENT_NAME
    body["response_engine"] = {"type": "conversation-flow", "conversation_flow_id": flow_id}
    body["webhook_events"] = ["call_started", "call_ended", "call_analyzed"]
    return body


def chat_agent_body(flow_id: str, base_url: str, analysis_model: str) -> dict:
    """`analysis_model` runs Retell's post-chat analysis, which nothing reads (config/models.yaml
    `retell.post_chat_analysis`)."""
    return {
        "agent_name": CHAT_AGENT_NAME,
        "response_engine": {"type": "conversation-flow", "conversation_flow_id": flow_id},
        "webhook_url": f"{base_url}/retell/webhook",
        "webhook_events": ["chat_started", "chat_ended"],
        "post_chat_analysis_model": analysis_model,
    }


def reachable(flow: dict) -> set[str]:
    """Node ids reachable from the start node by any edge, for the tests."""
    by_id = {n["id"]: n for n in flow["nodes"]}
    seen, todo = set(), [flow["start_node_id"]]
    while todo:
        node = by_id[todo.pop()]
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        todo += [e["destination_node_id"] for e in outgoing(node)]
    return seen


def outgoing(node: dict) -> list[dict]:
    edges = list(node.get("edges") or [])
    for key in ("else_edge", "always_edge", "skip_response_edge", "edge"):
        if node.get(key):
            edges.append(node[key])
    return edges
