"""The model's tools. Each one posts to the tool server and hands the envelope back to the model;
none decides anything here. Safety text in a result is spoken by the worker, not handed to the
model to say. There is no built-in end-call or transfer tool: `end_call` asks the
server first and hangs up only when the server says so (DECISIONS.md 004), and V1 has no live
transfer, so the model asks for a person with `flag_person` (docs/call-journeys.md, traits).
"""

import json
import time
from collections.abc import Callable

from livekit.agents import RunContext, function_tool

from .bridge import Bridge
from .server import ToolServer


class ToolLog:
    """Tool time per agent turn, sent with the agent's line as a stage timing."""

    def __init__(self):
        self.ms = 0.0
        self.bridge = False

    def take(self) -> tuple[float, bool]:
        ms, bridge = self.ms, self.bridge
        self.ms, self.bridge = 0.0, False
        return ms, bridge


def compact(result: dict) -> str:
    """What the model reads: the envelope, without empty fields."""
    return json.dumps({k: v for k, v in result.items() if v not in (None, "", {})}, default=str)


def make_tool(
    spec: dict,
    server: ToolServer,
    bridge: Bridge,
    log: ToolLog,
    hang_up: Callable[[RunContext, object], None],
    speak: Callable[[str], object],
):
    schema = {k: spec[k] for k in ("name", "description", "parameters")}

    @function_tool(raw_schema=schema)
    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str | None:
        started = time.perf_counter()
        before = len(bridge.played)
        result = await bridge.run(server.tool(spec["name"], dict(raw_arguments), spec["write"]))
        log.ms += (time.perf_counter() - started) * 1000
        log.bridge = log.bridge or len(bridge.played) > before
        ending = spec["name"] == "end_call" and result["ok"]
        spoken = None
        if result["say"] and (result["refused"] == "gate_safety" or ending):
            # Safety text (the lines, the check question, the safety sign-off) is spoken by the
            # worker word for word, never by the model. end_call only carries a sentence on a
            # safety call.
            spoken = speak(result["say"])
        if ending and result["data"].get("hang_up"):
            # The server agreed: hang up once this turn's speech, and any line above, has played.
            hang_up(context, spoken)
        if spoken is not None or ending:
            # None tells the session not to generate another reply.
            return None
        return compact(result)

    return handler


def build_tools(
    specs: list[dict],
    server: ToolServer,
    bridge: Bridge,
    log: ToolLog,
    hang_up: Callable[[RunContext, object], None],
    speak: Callable[[str], object],
) -> list:
    """One tool per spec. `hang_up` runs only when the server's end_call result says to; `speak`
    says a line word for word with interruptions off and returns its speech handle."""
    return [make_tool(spec, server, bridge, log, hang_up, speak) for spec in specs]
