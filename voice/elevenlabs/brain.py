"""Our brain behind ElevenLabs Agents' custom-LLM mode: an OpenAI-style streaming chat endpoint
that ElevenLabs calls for every agent reply. It answers with the same conversation the LiveKit
worker runs: the same model profile (config/models.yaml), the same prompt and tools
(prompts/assemble.py), the same turn check on every caller line and the same tool server.

ElevenLabs only supplies ears, turn-taking and the voice. What it sends as history is read for the
caller's newest words and nothing else; the conversation the model sees is kept here, with our
tool calls in it. Safety text from the server (the lines, a gate refusal, the safety sign-off) is
streamed as given with no model step, as the worker speaks it. ElevenLabs can only hang up through
its end-call tool, which it runs only when this endpoint emits that tool call, and this endpoint
emits it only after the tool server's `end_call` said to hang up (DECISIONS.md 004).

Endpoint shape (SOURCE: https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm):
ElevenLabs posts an OpenAI chat-completions body with `stream: true` to `<url>/chat/completions`
and reads server-sent `chat.completion.chunk` events; with `custom_llm_extra_body` on, the body
carries what the conversation's start passed in `elevenlabs_extra_body`.
"""

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from prompts import assemble

from .server import UNREACHABLE, ToolServer

ROOT = Path(__file__).resolve().parents[2]
MAX_HOPS = 6
SPOKEN_ACTIONS = ("speak_lines", "speak_variant", "ask_check")
DIALED = "(646) 624-0418"
CALL_ID = re.compile(r"summit_call_id=(\S+)")


def load_brain(path: Path = ROOT / "config/models.yaml") -> dict:
    """The live profile named by `realtime.elevenlabs.brain`, with its name."""
    config = yaml.safe_load(path.read_text())
    name = config["realtime"]["elevenlabs"]["brain"]
    return {"name": name, **config["profiles"][name]}


def thinking(profile: dict) -> dict:
    """Sent on every request: current models think when the field is left out (docs/latency.md)."""
    if profile["thinking"]:
        return {"type": "adaptive"}
    return {"type": profile.get("thinking_off", "disabled")}


@dataclass
class Conversation:
    call_id: str
    system: str
    tools: list[dict]
    writes: set[str]
    voice: dict
    messages: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def caller_words(messages: list[dict]) -> str:
    """The caller's lines since the agent last spoke, from ElevenLabs' history."""
    words = []
    for message in reversed(messages):
        if message.get("role") != "user":
            if words or message.get("role") == "assistant":
                break
            continue
        content = message.get("content") or ""
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        words.append(content.strip())
    return " ".join(w for w in reversed(words) if w)


def call_id_of(body: dict) -> str:
    extra = body.get("elevenlabs_extra_body") or {}
    if extra.get("summit_call_id"):
        return extra["summit_call_id"]
    for message in body.get("messages", []):
        if message.get("role") == "system":
            found = CALL_ID.search(str(message.get("content", "")))
            if found:
                return found.group(1)
    return ""


def spoken_by_code(name: str, result: dict) -> str | None:
    say = result.get("say") or ""
    if result.get("refused") == "gate_safety" and say:
        return say
    if name == "end_call" and result.get("ok") and say:
        return say
    return None


def cost(profile: dict, usage) -> float:
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    paid = usage.input_tokens + 1.25 * write + 0.1 * read
    return (paid * profile["price"]["input"] + usage.output_tokens * profile["price"]["output"]) / 1e6


class Brain:
    def __init__(self, tool_server: ToolServer, client=None, profile: dict | None = None,
                 tenants_dir: Path = ROOT / "tenants"):
        self.server = tool_server
        self.profile = profile or load_brain()
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic()
        self.client = client
        self.tenants_dir = tenants_dir
        self.calls: dict[str, Conversation] = {}
        self._running: set[asyncio.Task] = set()

    async def start(self, call_id: str, caller_id: str, dialed: str = DIALED) -> str:
        """Start the call on the tool server and return the first line ElevenLabs should say."""
        started = await self.server.start(call_id, caller_id, dialed)
        if not started["ok"]:
            raise RuntimeError(f"/calls/start refused: {started['refused']}")
        data = started["data"]
        folder = self.tenants_dir / data["tenant"]["slug"]
        specs = assemble.tool_specs(assemble.job_types(folder))
        voice = yaml.safe_load((folder / "agent.yaml").read_text())["voice"]
        conv = Conversation(
            call_id=call_id,
            system=assemble.instructions(data),
            tools=[{"name": s["name"], "description": s["description"],
                    "input_schema": s["parameters"]} for s in specs],
            writes={s["name"] for s in specs if s["write"]},
            voice=voice,
        )
        self.calls[call_id] = conv
        safety = (data.get("safety") or {}).get("text")
        first = safety or " ".join(x for x in (assemble.greeting(data, voice), started["say"]) if x)
        conv.messages.append({"role": "user", "content": "(The call has connected.)"})
        conv.messages.append({"role": "assistant", "content": first})
        await self.server.turn(call_id, "agent", first)
        return first

    async def reply(self, call_id: str, heard: str):
        """Yield ("text", words) pieces to speak, then ("end_call", None) if the server agreed to
        hang up.

        The turn runs in its own task. ElevenLabs drops a request when the caller talks over the
        agent; the turn still finishes, so a tool that was called gets its result into the history
        and the agent's line reaches the tool server, as when a caller interrupts the worker."""
        conv = self.calls[call_id]
        pieces: asyncio.Queue = asyncio.Queue()

        async def run() -> None:
            try:
                async with conv.lock:
                    async for piece in self._reply(conv, heard):
                        await pieces.put(piece)
            finally:
                await pieces.put(None)

        task = asyncio.create_task(run())
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        while (piece := await pieces.get()) is not None:
            yield piece
        await task

    async def _reply(self, conv: Conversation, heard: str):
        t0 = time.perf_counter()
        result = await self.server.turn(conv.call_id, "caller", heard)
        if result["refused"] == UNREACHABLE:
            # No server means no safety check, so the call can't go on (voice/livekit/worker.py).
            yield "text", conv.voice["server_down"]
            yield "end_call", None
            return
        data = result.get("data") or {}
        safety = data.get("safety") or {}
        if safety.get("action") in SPOKEN_ACTIONS and result["say"]:
            conv.messages.append({"role": "user", "content": heard})
            conv.messages.append({"role": "assistant", "content": result["say"]})
            yield "text", result["say"]
            await self._agent_line(conv, result["say"], t0, None)
            return
        notes = assemble.server_notes(data.get("hints", []))
        conv.messages.append({"role": "user", "content": "\n".join([heard, *notes])})
        spoken: list[str] = []
        first_ms = None
        hang_up = False
        for _ in range(MAX_HOPS):
            params = {
                "model": self.profile["model"], "max_tokens": 1024, "system": conv.system,
                "messages": conv.messages, "tools": conv.tools, "thinking": thinking(self.profile),
                "cache_control": {"type": "ephemeral"},
            }
            async with self.client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        if first_ms is None:
                            first_ms = (time.perf_counter() - t0) * 1000
                        yield "text", event.delta.text
                        spoken.append(event.delta.text)
                message = await stream.get_final_message()
            conv.input_tokens += message.usage.input_tokens
            conv.output_tokens += message.usage.output_tokens
            conv.cost_usd += cost(self.profile, message.usage)
            conv.messages.append({"role": "assistant",
                                  "content": [b.model_dump(exclude_none=True) for b in message.content]})
            uses = [b for b in message.content if b.type == "tool_use"]
            if not uses:
                break
            results, from_code = [], None
            for use in uses:
                envelope = await self._tool(conv, use.name, dict(use.input))
                if use.name == "end_call" and envelope.get("ok"):
                    hang_up = hang_up or bool((envelope.get("data") or {}).get("hang_up"))
                from_code = from_code or spoken_by_code(use.name, envelope)
                compact = {k: v for k, v in envelope.items() if v not in (None, "", {})}
                results.append({"type": "tool_result", "tool_use_id": use.id,
                                "content": json.dumps(compact, default=str)})
            conv.messages.append({"role": "user", "content": results})
            if spoken and not "".join(spoken).endswith((" ", "\n")):
                yield "text", " "
                spoken.append(" ")
            if from_code:
                conv.messages.append({"role": "assistant", "content": from_code})
                yield "text", from_code
                spoken.append(from_code)
                break
            if hang_up:
                break
        text = "".join(spoken).strip()
        if not text and hang_up:
            text = conv.voice["goodbye"]
            yield "text", text
        if text:
            await self._agent_line(conv, text, t0, first_ms)
        if hang_up:
            yield "end_call", None

    async def _tool(self, conv: Conversation, name: str, args: dict) -> dict:
        if name not in {t["name"] for t in conv.tools}:
            return {"ok": False, "say": "", "data": {}, "refused": "unknown_tool"}
        return await self.server.tool(conv.call_id, name, args, name in conv.writes)

    async def _agent_line(self, conv: Conversation, text: str, t0: float, first_ms) -> None:
        # From the request to the end of the reply; ElevenLabs' own clock covers the audio.
        timings = {"turn_ms": round((time.perf_counter() - t0) * 1000, 1)}
        if first_ms is not None:
            timings["llm_first_token_ms"] = round(first_ms, 1)
        await self.server.turn(conv.call_id, "agent", text, timings)

    async def finish(self, call_id: str, reason: str) -> None:
        await self.server.end(call_id, reason)


def chunk(cid: str, model: str, delta: dict, finish: str | None = None) -> str:
    body = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body)}\n\n"


async def completion_events(brain: Brain, call_id: str, heard: str, model: str):
    """The reply as OpenAI server-sent events, ending with an end_call tool call when the server
    allowed the hang-up."""
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    yield chunk(cid, model, {"role": "assistant", "content": ""})
    ended = False
    # A request with no new caller words (ElevenLabs asking again after its own tool ran) gets an
    # empty reply rather than a turn the caller never spoke.
    pieces = brain.reply(call_id, heard) if heard else _nothing()
    async for kind, text in pieces:
        if kind == "text" and text:
            yield chunk(cid, model, {"content": text})
        elif kind == "end_call":
            ended = True
    if ended:
        # ElevenLabs' own end-call tool; it hangs up after what was said above has played.
        call = {"index": 0, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                "function": {"name": "end_call",
                             "arguments": json.dumps({"reason": "The call is finished."})}}
        yield chunk(cid, model, {"tool_calls": [call]})
        yield chunk(cid, model, {}, "tool_calls")
    else:
        yield chunk(cid, model, {}, "stop")
    yield "data: [DONE]\n\n"


async def _nothing():
    return
    yield


def build_app(brain: Brain, token: str) -> FastAPI:
    """`token` is a path segment only ElevenLabs is given (in the agent's custom-LLM URL), since
    the endpoint is reached through a public tunnel."""
    app = FastAPI()

    def check(path_token: str) -> None:
        if path_token != token:
            raise HTTPException(status_code=404)

    @app.post("/{path_token}/calls")
    async def start(path_token: str, request: Request) -> dict:
        """Start a call before its conversation opens; returns the first line to say. This is
        where ElevenLabs' conversation-start webhook would land on a real phone call."""
        check(path_token)
        body = await request.json()
        first = await brain.start(body["call_id"], body["caller_id"], body.get("dialed") or DIALED)
        return {"first_message": first}

    @app.post("/{path_token}/calls/{call_id}/end")
    async def end(path_token: str, call_id: str, request: Request) -> dict:
        check(path_token)
        await brain.finish(call_id, (await request.json()).get("reason", "caller_hangup"))
        conv = brain.calls.pop(call_id, None)
        if conv is None:
            return {"cost_usd": 0.0}
        return {"cost_usd": conv.cost_usd, "input_tokens": conv.input_tokens,
                "output_tokens": conv.output_tokens}

    @app.post("/{path_token}/v1/chat/completions")
    async def completions(path_token: str, request: Request):
        check(path_token)
        body = await request.json()
        call_id = call_id_of(body)
        if call_id not in brain.calls:
            raise HTTPException(status_code=404, detail="unknown call")
        heard = caller_words(body.get("messages", []))
        model = body.get("model") or brain.profile["model"]
        return StreamingResponse(completion_events(brain, call_id, heard, model),
                                 media_type="text/event-stream")

    return app
