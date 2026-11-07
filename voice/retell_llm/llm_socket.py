"""The WebSocket Retell opens for each custom-LLM call (SOURCE:
https://docs.retellai.com/api-references/llm-websocket.md, read 2026-11-04).

    uv run uvicorn --factory voice.retell_llm.llm_socket:create_app --port 8113

Retell connects to `<llm_websocket_url>/<retell call id>`. It sends no auth header, so the URL
carries a key derived from the Retell API key, and a socket without it is closed at once. The call
is already open on the tool server when the socket opens (the rig, or the bridge's inbound
webhook, starts it); its id, prompt and first line come in the call's dynamic variables.

An arm can bring its own prompt and tools instead (issue #176, the server-led arm):
RETELL_LLM_PROMPT is a prompt file whose {{call_context}} is filled from the call's variables,
RETELL_LLM_TOOLS a YAML of descriptions for the tools whose steps the arm's flow runs, over the
server's own specs, and
RETELL_LLM_SERVER_LED=1 speaks every sentence a tool result carries as written (voice/retell_llm/
call.py). The tool server behind it runs the flow (SERVER_LED=1).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.tenant import voice_lines
from prompts.assemble import job_types, tool_specs
from voice.livekit.server import ToolServer
from voice.livekit.settings import config_path as models_config_path
from voice.livekit.settings import load_profile

from .call import LlmCall, QualityReply
from .model import Brain

log = logging.getLogger("voice.retell_llm")

ROOT = Path(__file__).resolve().parents[2]
# Retell rebuilds a socket that closed abnormally (auto_reconnect); the call is kept this long
# before it is closed on the tool server.
RECONNECT_GRACE_S = 6.0
CONFIG = {"response_type": "config", "config": {"auto_reconnect": True, "call_details": True}}


def socket_key(api_key: str) -> str:
    """The path segment Retell's socket must carry: known to whoever holds the API key."""
    return hmac.new(api_key.encode(), b"summit-air retell-llm socket", hashlib.sha256).hexdigest()[:32]


def tenant_lines(tenants_dir: Path, slug: str) -> dict:
    return voice_lines(tenants_dir / slug)


def arm_prompt(path: Path, variables: dict) -> str:
    """The arm's prompt, with the call's own part filled in as Retell would."""
    return path.read_text().replace("{{call_context}}", variables.get("call_context", ""))


def arm_tools(path: Path, specs: list[dict]) -> list[dict]:
    """The tool server's own specs (names, parameters, write flags, prompts/tools.yaml
    descriptions), with the arm's description for the few tools whose steps its flow runs
    (retell/socket/tools.yaml). Parameters always come from the server, so an argument it
    adds (read_back's job_type) reaches the model, and nothing drifts (voice/retell/tool_schemas.py)."""
    overrides = yaml.safe_load(path.read_text()) or {}
    names = {s["name"] for s in specs}
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise ValueError(f"tools the tool server doesn't have: {', '.join(unknown)}")
    return [{**s, "description": overrides.get(s["name"], s["description"])} for s in specs]


def hold_phrase_after_s(config_path: Path) -> float:
    """How long a step's tool work may run before the hold phrase
    (config/models.yaml voice.hold_phrase_after_s)."""
    return float(yaml.safe_load(config_path.read_text())["voice"].get("hold_phrase_after_s", 0.7))


def hold_phrase_from(config_path: Path) -> str:
    """What the hold phrase's wait is counted from: the caller's turn or the tool's start
    (config/models.yaml voice.hold_phrase_from, RETELL_LLM_HOLD_FROM overrides it)."""
    voice = yaml.safe_load(config_path.read_text())["voice"]
    return os.environ.get("RETELL_LLM_HOLD_FROM") or voice.get("hold_phrase_from", "caller")


def dead_air_checks(config_path: Path) -> int:
    """How many check-ins Retell asks for before the goodbye (config/models.yaml dead_air)."""
    timings = yaml.safe_load(config_path.read_text())["voice"]["dead_air"]
    return int(timings["hang_up_s"] // timings["check_in_s"])


def create_app(
    brain: Brain | None = None, transport=None, quality_brain: Brain | None = None
) -> FastAPI:
    # Each request and replaced turn is logged, to line up with Retell's own call logs.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = FastAPI()
    key = socket_key(os.environ["RETELL_API_KEY"])
    tool_server = os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8100")
    tenants = Path(os.environ.get("TENANTS_DIR") or ROOT / "tenants")
    default_tenant = os.environ.get("RETELL_LLM_TENANT", "summit-air")
    checks = dead_air_checks(ROOT / "config" / "models.yaml")
    hold_after = hold_phrase_after_s(ROOT / "config" / "models.yaml")
    hold_from = hold_phrase_from(ROOT / "config" / "models.yaml")
    prompt_file = os.environ.get("RETELL_LLM_PROMPT")
    tools_file = os.environ.get("RETELL_LLM_TOOLS")
    server_led = os.environ.get("RETELL_LLM_SERVER_LED") == "1"
    # The worker's 4 s; an arm on a busier box can allow more (a holding line covers the wait).
    tool_timeout = float(os.environ.get("RETELL_LLM_TOOL_TIMEOUT_S") or 4.0)
    calls: dict[str, LlmCall] = {}
    closing: dict[str, asyncio.Task] = {}
    shared = {"brain": brain}

    def the_brain() -> Brain:
        if shared["brain"] is None:
            shared["brain"] = Brain()
        return shared["brain"]

    def quality_reply(lines: dict) -> QualityReply | None:
        """SONNET_HANDOFF=1 (config/models.yaml voice.sonnet_handoff); off unless it is set."""
        if os.environ.get("SONNET_HANDOFF") != "1" or not lines.get("lookup_filler"):
            return None
        setting = yaml.safe_load(models_config_path().read_text())["voice"]["sonnet_handoff"]
        if "quality" not in shared:
            shared["quality"] = quality_brain or Brain(load_profile(setting["profile"]))
        return QualityReply(shared["quality"], lines["lookup_filler"], setting["max_per_call"],
                            setting["first_token_timeout_s"])

    def new_call(details: dict, send) -> tuple[LlmCall, str]:
        variables = details.get("retell_llm_dynamic_variables") or {}
        metadata = details.get("metadata") or {}
        call_id = variables.get("summit_call_id") or metadata.get("summit_call_id")
        slug = metadata.get("tenant") or default_tenant
        server = ToolServer(tool_server, call_id, timeout_s=tool_timeout, transport=transport)
        specs = tool_specs(job_types(tenants / slug))
        system = variables["instructions"]
        if prompt_file:
            # On a server-down call the bridge says its line as the greeting and hangs up itself.
            system = arm_prompt(ROOT / prompt_file, variables)
        if tools_file:
            specs = arm_tools(ROOT / tools_file, specs)
        lines = tenant_lines(tenants, slug)
        call = LlmCall(
            server, the_brain(), send, system, specs, lines, quality=quality_reply(lines),
            phone=details.get("call_type") == "phone_call", dead_air_checks=checks,
            server_led=server_led, hold_after_s=hold_after, hold_from=hold_from,
            call_start_ms=details.get("start_timestamp"),
        )
        return call, variables.get("greeting", "")

    async def close_later(retell_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        call = calls.pop(retell_id, None)
        closing.pop(retell_id, None)
        if call is not None:
            await call.close()

    async def run(task_name: str, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("%s failed", task_name)

    @app.websocket("/retell-llm/{given}/{retell_id}")
    async def socket(ws: WebSocket, given: str, retell_id: str) -> None:
        if not hmac.compare_digest(given, key):
            await ws.close(code=4403)
            return
        await ws.accept()
        lock = asyncio.Lock()

        async def send(event: dict) -> None:
            async with lock:
                await ws.send_text(json.dumps(event))

        await send(CONFIG)
        if retell_id in closing:
            closing.pop(retell_id).cancel()
        call = calls.get(retell_id)
        if call is not None:
            call.send = send
        tasks: set[asyncio.Task] = set()
        normal = False
        replying: dict[str, asyncio.Task] = {}

        def spawn(name: str, coro) -> asyncio.Task:
            task = asyncio.create_task(run(name, coro))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return task

        def reply(name: str, response_id: int, coro) -> None:
            """Retell drops every earlier response once it asks for a new one, so the turn still
            writing one is cancelled rather than left to finish first."""
            call.request(response_id)
            running = replying.get("task")
            if running is not None and not running.done():
                log.info("%s: response %s replaces the turn in progress", retell_id, response_id)
                running.cancel()
            replying["task"] = spawn(name, coro)

        try:
            while True:
                event = json.loads(await ws.receive_text())
                kind = event.get("interaction_type")
                if kind == "ping_pong":
                    await send({"response_type": "ping_pong", "timestamp": event["timestamp"]})
                elif kind == "call_details" and call is None:
                    call, greeting = new_call(event["call"], send)
                    calls[retell_id] = call
                    await call.begin(greeting)
                elif call is None:
                    continue
                elif kind == "response_required":
                    log.info("%s: response %s required", retell_id, event["response_id"])
                    reply("reply", event["response_id"],
                          call.respond(event["response_id"], event["transcript"]))
                elif kind == "reminder_required":
                    log.info("%s: reminder %s required", retell_id, event["response_id"])
                    reply("reminder", event["response_id"],
                          call.remind(event["response_id"], event["transcript"]))
                elif kind == "update_only":
                    spawn("update", call.seen(event["transcript"]))
        except WebSocketDisconnect as gone:
            normal = gone.code in (1000, 1001)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if retell_id in calls:
            closing[retell_id] = asyncio.create_task(
                close_later(retell_id, 0 if normal else RECONNECT_GRACE_S)
            )

    @app.get("/retell-llm/health")
    async def health() -> dict:
        return {"ok": True, "calls": len(calls)}

    return app
