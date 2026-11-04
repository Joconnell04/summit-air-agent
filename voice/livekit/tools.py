"""The model's tools. Each one posts to the tool server and hands the envelope back to the model;
none decides anything here. Safety text in a result is spoken by the worker, not handed to the
model to say. There is no built-in end-call or transfer tool: `end_call` asks the
server first and hangs up only when the server says so (DECISIONS.md 004), and the model asks for a
person with `flag_person`; after each one the worker asks the server whether the call goes to a
person now (transfer.py).
"""

import json
import time
from collections.abc import Callable

from livekit.agents import RunContext, function_tool

from prompts.assemble import SpokenReply, spoken_by_server

from .bridge import Bridge
from .server import ToolServer
from .transfer import Transfer


class ToolLog:
    """Tool time per agent turn, sent with the agent's line as a stage timing."""

    def __init__(self):
        self.ms = 0.0
        self.bridge = False
        # Tools waiting on the server now: the call isn't silent while one runs (voice/dead_air.py).
        self.running = 0

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
    hang_up: Callable[..., None],
    speak: Callable[[str], object],
    transfer: Transfer | None = None,
    say_parts: Callable[[list[dict]], object] | None = None,
    reply: SpokenReply | None = None,
):
    schema = {k: spec[k] for k in ("name", "description", "parameters")}

    @function_tool(raw_schema=schema)
    async def handler(raw_arguments: dict[str, object], context: RunContext) -> str | None:
        started = time.perf_counter()
        before = len(bridge.played)
        log.running += 1
        try:
            result = await bridge.run(server.tool(spec["name"], dict(raw_arguments), spec["write"]))
        finally:
            log.running -= 1
        log.ms += (time.perf_counter() - started) * 1000
        log.bridge = log.bridge or len(bridge.played) > before
        if reply is not None:
            reply.tool_done(spec["name"], result)
        ending = spec["name"] == "end_call" and result["ok"]
        spoken = None
        parts = result["data"].get("parts") if result["ok"] else None
        line = spoken_by_server(spec["name"], result)
        if parts and say_parts is not None:
            # A read-back the caller asked to hear slower: the server's sentence in parts, the
            # address at its own speed (app/pace.py), spoken word for word.
            spoken = say_parts(parts)
        elif line:
            # Safety text (the lines, the check question, the safety sign-off), the read-back
            # sentence and a booking's confirmation are spoken by the worker word for word, never
            # by the model. end_call only carries a sentence on a safety call.
            spoken = speak(line)
        if ending and result["data"].get("hang_up"):
            # The server agreed: hang up once this turn's speech, and any line above, has played.
            # After the safety sign-off the call has had its goodbye, so the worker adds none.
            hang_up(context, spoken, quiet=bool(result["data"].get("signed_off")))
        # After a person flag the server may put the caller through now, and the worker speaks
        # for the agent from there.
        put_through = spec["name"] == "flag_person" and result["ok"] and transfer is not None
        if put_through and await transfer.offer(context.speech_handle, result):
            return None
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
    hang_up: Callable[..., None],
    speak: Callable[[str], object],
    transfer: Transfer | None = None,
    say_parts: Callable[[list[dict]], object] | None = None,
    reply: SpokenReply | None = None,
) -> list:
    """One tool per spec. `hang_up` runs only when the server's end_call result says to; `speak`
    says a line word for word with interruptions off and returns its speech handle; `transfer`,
    when set, is asked after every person flag; `say_parts` speaks a sentence the server split by
    speed; `reply` hears each result, so the agent knows when to hold the model's text."""
    return [
        make_tool(spec, server, bridge, log, hang_up, speak, transfer, say_parts, reply)
        for spec in specs
    ]
