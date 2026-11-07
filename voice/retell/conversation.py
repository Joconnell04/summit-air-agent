"""What the Retell agent reads and says first on a call, from the same sources as the LiveKit
worker: the instructions from prompts/assemble.py and the spoken lines in the tenant's agent.yaml
(`voice:`). Retell takes them per call as dynamic variables: {{instructions}} is the whole prompt,
{{greeting}} the first thing it says, and {{call_context}} only the prompt's "This call" part, for
the native agent (voice/archive/retell_native on branch archive/voice-stacks), whose prompt is fixed and short."""

import json
from collections.abc import Callable
from pathlib import Path

from app.tenant import voice_lines
from prompts.assemble import greeting, sections


def spoken_lines(tenants_dir: Path, slug: str) -> dict:
    return voice_lines(tenants_dir / slug)


def opening(start: dict, say: str, lines: dict) -> str:
    """The first line. A call that carries on a dropped safety call opens with the safety lines;
    otherwise the recording notice when the server sends one (two-party consent), the greeting,
    and anything else the server wants said (repeated drops)."""
    safety = start.get("safety") or {}
    if safety.get("text"):
        return safety["text"]
    parts = (start.get("recording_notice"), greeting(start, lines), say)
    return " ".join(part for part in parts if part)


def variables(call_id: str, instructions: str, first_line: str) -> dict[str, str]:
    return {"summit_call_id": call_id, "instructions": instructions, "greeting": first_line}


def for_call(
    call_id: str,
    start: dict,
    instructions: Callable[[dict], str],
    spoken: Callable[[str], dict],
) -> dict[str, str]:
    """Dynamic variables for a call or chat, from its /calls/start answer."""
    data = start["data"]
    first = opening(data, start.get("say") or "", spoken(data["tenant"]["slug"]))
    context = next((text for name, text in sections(data) if name == "This call"), "")
    return {**variables(call_id, instructions(data), first), "call_context": context}


def server_down(call_id: str, lines: dict) -> dict[str, str]:
    """No lookup and no tools: say the server-down line and nothing else (the bridge then ends
    the call, as the LiveKit worker does)."""
    line = " ".join(lines["server_down"].split())
    return {**variables(call_id, f"Say only this, then stop: {json.dumps(line)}", line),
            "call_context": ""}
