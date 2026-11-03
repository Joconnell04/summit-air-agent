"""The one response shape every endpoint returns, always with HTTP 200 (docs/tools.md).

{"ok": true, "say": "...", "data": {}, "refused": null}

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

    def body(self) -> dict:
        return {"ok": self.ok, "say": self.say, "data": self.data, "refused": self.refused}


def ok(say: str = "", **data) -> Result:
    return Result(True, say, data)


class Refused(Exception):
    """Raised by a tool when the server says no. Gate refusals carry the gate name for the log."""

    def __init__(self, code: str, say: str, gate: str | None = None, **data):
        super().__init__(code)
        self.result = Result(False, say, data, code, gate or code)
