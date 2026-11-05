"""What a tool hands the session when the worker spoke for the agent."""

from livekit.agents.llm import ToolResult


def no_reply(result) -> bool:
    """The session is told not to generate another reply (voice/livekit/tools.py _no_reply)."""
    return isinstance(result, ToolResult) and result.reply_required is False
