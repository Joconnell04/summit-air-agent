"""Reading Retell's call and chat objects: which of our calls it is and what was said."""

from dataclasses import dataclass, field

# Our call id is the one the bridge chose when the call or chat started (dynamic variable
# summit_call_id); a call that reached us without one gets the Retell id with this prefix.
PREFIX = "retell_"
SPEAKERS = {"user": "caller", "agent": "agent"}


@dataclass
class Line:
    speaker: str  # caller or agent
    text: str
    # Retell's word timings, seconds from the start of the call. Empty for chats, and for agent
    # text that hasn't been spoken yet.
    words: list[dict] = field(default_factory=list)


def retell_id(obj: dict) -> str:
    return obj.get("call_id") or obj.get("chat_id") or ""


def our_call_id(obj: dict) -> str:
    variables = obj.get("retell_llm_dynamic_variables") or {}
    return variables.get("summit_call_id") or PREFIX + retell_id(obj)


def lines(obj: dict) -> list[Line]:
    """Caller and agent lines in order. A call payload carries transcript_object,
    transcript_with_tool_calls or both; with the tool entries left out they hold the same lines in
    the same order (MEASURED on the first real call), so line numbers stay stable from one event to
    the next. Chats carry message_with_tool_calls."""
    raw = obj.get("transcript_object")
    if raw is None:
        raw = obj.get("transcript_with_tool_calls")
    if raw is None:
        raw = obj.get("message_with_tool_calls") or []
    out = []
    for entry in raw:
        speaker = SPEAKERS.get(entry.get("role"))
        text = (entry.get("content") or "").strip()
        if speaker and text:
            out.append(Line(speaker, text, list(entry.get("words") or [])))
    return out
