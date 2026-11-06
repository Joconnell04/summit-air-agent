"""The one response shape every endpoint returns, always with HTTP 200 (docs/tools.md).

{"ok": true, "say": "...", "data": {}, "refused": null}

A server-led instance (SERVER_LED=1, app/flow.py) adds `next`, the next step in plain words.

A non-2xx would make voice platforms retry and leave dead air, so a refusal is ok: false with
`refused` naming why and `say` telling the agent what to say instead.
"""

from dataclasses import dataclass, field


@dataclass
class Result:
    ok: bool
    say: str = ""
    data: dict = field(default_factory=dict)
    refused: str | None = None
    gate: str | None = None  # the gate that fired, for the log; not sent to the agent
    next: str = ""

    def body(self) -> dict:
        # `next` right after `say`, so the model reads the two together before the data.
        body = {"ok": self.ok, "say": self.say, "next": self.next, "data": self.data,
                "refused": self.refused}
        if not self.next:
            del body["next"]
        return body


def ok(say: str = "", **data) -> Result:
    return Result(True, say, data)


class Refused(Exception):
    """Raised by a tool when the server says no. Gate refusals carry the gate name for the log."""

    def __init__(self, code: str, say: str, gate: str | None = None, **data):
        super().__init__(code)
        self.result = Result(False, say, data, code, gate or code)

    def with_next(self, text: str) -> "Refused":
        self.result.next = text
        return self
