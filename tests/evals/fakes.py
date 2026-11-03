"""Fake models for the harness tests: scripted replies, no network."""
import json
from collections.abc import Callable

from evals.models import Reply, Usage


def say(text: str, cost: float = 0.001) -> Reply:
    return Reply(text=text, tool_uses=[], content=[{"type": "text", "text": text}],
                 first_token_ms=50.0, total_ms=80.0, stop_reason="end_turn",
                 usage=Usage(100, 20, cost))


def use(name: str, text: str = "", **args) -> Reply:
    """A reply that calls one tool, optionally speaking first."""
    tool_id = f"tu_{name}_{abs(hash(json.dumps(args, sort_keys=True))) % 10_000}"
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": tool_id, "name": name, "input": args}
    ]
    return Reply(text=text, tool_uses=[{"id": tool_id, "name": name, "input": args}],
                 content=content, first_token_ms=40.0, total_ms=60.0, stop_reason="tool_use",
                 usage=Usage(100, 20, 0.001))


class ScriptedModel:
    """Returns replies in order; a callable entry decides from the last tool result."""

    def __init__(self, replies: list[Reply | Callable]):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def complete(self, system, messages, tools=None, max_tokens=1024, json_schema=None) -> Reply:
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if not self.replies:
            return say("Okay.")
        reply = self.replies.pop(0)
        return reply(messages) if callable(reply) else reply


def last_tool_result(messages: list[dict]) -> dict:
    block = messages[-1]["content"][-1]
    return json.loads(block["content"])


def grade_reply(score: int = 4, verdict: str = "pass", notes: str = "Fine.") -> Reply:
    return say(json.dumps({"score": score, "verdict": verdict, "notes": notes}))
