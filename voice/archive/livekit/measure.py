"""Measure the model's first token and the prompt cache on a scripted call, without the phone.

It builds the same request the worker builds on a call: the instructions and tool list from
prompts/assemble.py, the chat context turn by turn, sent through the LiveKit Anthropic plugin. The
tool server and the mock ServiceTitan run in process, so tool results are real. Each caller line is
a model turn; a turn that calls a tool has more than one step (step 0 is the turn's first request,
step 1 the request after a tool result), which is the split docs/latency.md ("First real call")
points at.

Paid but tiny: a run is about a dozen requests (cents on `fast`, under a dollar on `fast-prev`).
No LiveKit connection is made and no phone call is placed.

Run (the key is read from the environment, never printed):
    uv run --env-file .env python -m voice.archive.livekit.measure --profile fast
    uv run --env-file .env python -m voice.archive.livekit.measure --profile fast --as-deployed
    uv run --env-file .env python -m voice.archive.livekit.measure --profile fast-prev --as-deployed
"""

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from livekit.agents import llm, utils
from livekit.plugins import anthropic as lk_anthropic

from app.main import create_app
from app.settings import Settings
from app.tenant import voice_lines
from prompts import assemble
from servicetitan_mock.app import create_app as create_mock
from servicetitan_mock.seed import build_world
from voice.archive.livekit.bridge import Bridge
from voice.archive.livekit.settings import load_profile
from voice.archive.livekit.tools import ToolLog, build_tools, compact

ROOT = Path(__file__).resolve().parents[3]
TENANT_DIR = ROOT / "tenants" / "summit-air"
DIALED = "(646) 624-0418"
CALLER = "(212) 555-0999"
MAX_STEPS = 4

# A heat-out call: plain turns first, then the lookups and the booking, as on the first real call.
CALLER_LINES = [
    "Hi, my heat just went out and it's pretty cold in here.",
    "It's a gas boiler, I think. The radiators are all cold.",
    "Since last night. I checked the thermostat and it's fine.",
    "Just me and my wife at home.",
    "I'm at 104 Union Street in Brooklyn, 11231.",
    "Yes, that's right.",
    "The earliest you have is fine.",
    "Pat Caller. Yes, I'm the one who can approve the work.",
]


class Stack:
    """The tool server over a fresh mock world, in process."""

    def __init__(self, workdir: Path):
        world = workdir / "world.db"
        build_world(world, TENANT_DIR, date.today())
        mock = create_mock(world, TENANT_DIR, workdir / "world.log")
        config = Settings(
            db_path=workdir / "tool.db",
            tenants_dir=TENANT_DIR.parent,
            debug_token="measure",
            jobs_interval_seconds=0,
        )
        fsm = TestClient(mock, raise_server_exceptions=False)
        self.client = TestClient(
            create_app(config, fsm_clients={"summit-air": fsm}), raise_server_exceptions=False
        )

    def start(self, call_id: str) -> dict:
        body = {"call_id": call_id, "caller_id": CALLER, "dialed_number": DIALED}
        return self.client.post("/calls/start", json=body).json()["data"]

    def turn(self, call_id: str, speaker: str, text: str) -> dict:
        body = {"speaker": speaker, "text": text, "timings": {}}
        return self.client.post(f"/calls/{call_id}/turn", json=body).json()

    def tool(self, call_id: str, name: str, args: dict) -> dict:
        return self.client.post(f"/tools/{name}", json={"call_id": call_id, **args}).json()


def extra_for(variant: str) -> dict:
    """Request fields added on top of what the pre-fix worker sent, to test one change at a time."""
    if variant == "thinking-off":
        return {"thinking": {"type": "disabled"}}
    if variant == "effort-low":
        return {"output_config": {"effort": "low"}}
    return {}


async def step(model, ctx: llm.ChatContext, tools: list, extra: dict) -> dict:
    """One request. Returns the text, any tool calls and the stage numbers."""
    started = time.perf_counter()
    first = None
    text, calls, usage = "", [], None
    kwargs = {"extra_kwargs": extra} if extra else {}
    async with model.chat(chat_ctx=ctx, tools=tools, **kwargs) as stream:
        async for chunk in stream:
            if chunk.delta and (chunk.delta.content or chunk.delta.tool_calls) and first is None:
                first = time.perf_counter()
            if chunk.delta and chunk.delta.content:
                text += chunk.delta.content
            if chunk.delta:
                calls.extend(chunk.delta.tool_calls)
            if chunk.usage:
                usage = chunk.usage
    ended = time.perf_counter()
    return {
        "text": text.strip(),
        "calls": calls,
        "first_token_ms": round(((first or ended) - started) * 1000),
        "total_ms": round((ended - started) * 1000),
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "cache_read": usage.cache_read_tokens if usage else 0,
        "cache_write": usage.cache_creation_tokens if usage else 0,
        "output_tokens": usage.completion_tokens if usage else 0,
    }


async def run(args) -> list[dict]:
    stack = Stack(Path(tempfile.mkdtemp(prefix="measure-reply-")))
    call_id = f"measure-{int(time.time())}"
    start = stack.start(call_id)
    profile = load_profile(args.profile)
    if args.as_deployed:
        # What the worker built before the fix for issue #30: caching on, no thinking field.
        model = lk_anthropic.LLM(model=profile.model, caching="ephemeral")
        extra = extra_for(args.variant)
    else:
        from voice.archive.livekit.brain import build_llm

        model = build_llm(profile)
        extra = {}
    specs = assemble.tool_specs(assemble.job_types(TENANT_DIR))
    tools = build_tools(
        specs, None, Bridge([], None, lambda: True), ToolLog(), lambda *a: None, lambda t: None
    )
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content=assemble.instructions(start))
    ctx.add_message(role="assistant", content=assemble.greeting(start, _voice()))
    rows = []
    async with utils.http_context.open():
        for turn, line in enumerate(CALLER_LINES[: args.turns], start=1):
            result = stack.turn(call_id, "caller", line)
            notes = assemble.server_notes((result.get("data") or {}).get("hints", []))
            ctx.add_message(
                role="user", content=[line] + (["\n" + "\n".join(notes)] if notes else [])
            )
            for index in range(MAX_STEPS):
                out = await step(model, ctx, tools, extra)
                rows.append(
                    {"turn": turn, "step": index, **{k: v for k, v in out.items() if k != "calls"}}
                )
                if out["text"]:
                    ctx.add_message(role="assistant", content=out["text"])
                if not out["calls"]:
                    break
                for call in out["calls"]:
                    ctx.insert(
                        llm.FunctionCall(
                            call_id=call.call_id, name=call.name, arguments=call.arguments
                        )
                    )
                    result = stack.tool(call_id, call.name, json.loads(call.arguments or "{}"))
                    ctx.insert(
                        llm.FunctionCallOutput(
                            call_id=call.call_id,
                            name=call.name,
                            output=compact(result),
                            is_error=False,
                        )
                    )
            if out["text"]:
                stack.turn(call_id, "agent", out["text"])
            await asyncio.sleep(args.pause)
    return rows


def _voice() -> dict:

    return voice_lines(TENANT_DIR)


def report(rows: list[dict], label: str) -> str:
    lines = [
        f"{label}",
        "turn step first_token_ms total_ms prompt cache_read cache_write out",
    ]
    for r in rows:
        lines.append(
            f"{r['turn']:>4} {r['step']:>4} {r['first_token_ms']:>14} {r['total_ms']:>8} "
            f"{r['prompt_tokens']:>6} {r['cache_read']:>10} {r['cache_write']:>11} {r['output_tokens']:>4}"
        )
    for name, subset in (
        ("step 0, all turns", [r for r in rows if r["step"] == 0]),
        ("step 0, turns 2 on", [r for r in rows if r["step"] == 0 and r["turn"] > 1]),
        ("step 1 (after a tool)", [r for r in rows if r["step"] == 1]),
    ):
        if subset:
            ms = [r["first_token_ms"] for r in subset]
            lines.append(
                f"{name}: n={len(ms)} median {statistics.median(ms):.0f} ms, "
                f"min {min(ms)}, max {max(ms)}"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", default="fast")
    parser.add_argument("--turns", type=int, default=len(CALLER_LINES))
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between caller lines")
    parser.add_argument(
        "--as-deployed", action="store_true", help="the worker's request before the fix"
    )
    parser.add_argument(
        "--variant",
        default="none",
        choices=["none", "thinking-off", "effort-low"],
        help="with --as-deployed: one extra request field to test",
    )
    parser.add_argument("--out", help="write the rows as JSON here")
    args = parser.parse_args()
    rows = asyncio.run(run(args))
    label = f"profile {args.profile}, {'as deployed' if args.as_deployed else 'worker build_llm'}"
    if args.as_deployed and args.variant != "none":
        label += f" + {args.variant}"
    print(report(rows, label))
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
