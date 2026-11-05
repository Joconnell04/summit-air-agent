"""The eval suite's `pace` check (evals/checks.py) against real tool-server records: it passes when
the server moved the pace and the voice applied it, and fails when either part is broken. This is
how the five pace scenarios were confirmed able to fail without paying for broken runs."""

from toolkit import in_area_address

from app import pace as pace_rules
from app.trace import timeline
from evals.checks import Evidence, pace, safety_config


class Voice:
    """Plays the voice layer: applies the server's speed to every agent line, or breaks it."""

    def __init__(self, agent, call_id: str, broken: str | None = None):
        self.agent, self.call_id, self.broken = agent, call_id, broken
        self.speed = 1.0

    def caller(self, text: str) -> dict:
        result = self.agent.turn(self.call_id, text)
        if self.broken != "ignores_server":
            self.speed = result["data"]["pace"]["speed"]
        return result

    def says(self, text: str, speed: float | None = None) -> None:
        timings = {} if self.broken == "no_speed" else {"speed": speed or self.speed}
        self.agent.turn(self.call_id, text, speaker="agent", **timings)


def evidence(agent, call_id: str) -> Evidence:
    conn = agent.db()
    trace = timeline(conn, call_id)
    conn.close()
    return Evidence(trace, safety_config())


def slow_down_call(agent, broken: str | None = None) -> str:
    call = agent.start()["data"]["call_id"]
    voice = Voice(agent, call, broken)
    voice.says("Thanks for calling Summit Air. How can I help?")
    voice.caller("Hi, I need a tune-up. Can you slow down a little?")
    voice.says("Sure, I'll slow down. What's the address?")
    return call


def test_the_check_passes_when_the_server_moved_the_pace_and_the_voice_applied_it(agent):
    call = slow_down_call(agent)
    assert pace({"held": 0.85, "source": "request", "remembered": 0.85}, evidence(agent, call)) == []


def test_the_check_fails_when_the_voice_ignores_the_servers_pace(agent):
    call = slow_down_call(agent, broken="ignores_server")
    assert pace({"held": 0.85}, evidence(agent, call)) == [
        "agent line 3 was spoken at 1.0; the server's pace was 0.85"
    ]


def test_the_check_fails_when_agent_lines_carry_no_speed(agent):
    call = slow_down_call(agent, broken="no_speed")
    assert "agent line 1 carries no speed" in pace({"held": 0.85}, evidence(agent, call))


def test_the_check_fails_when_the_server_does_not_hear_the_request(agent, monkeypatch):
    monkeypatch.setattr(pace_rules, "hear", lambda *a: pace_rules.Heard())
    call = slow_down_call(agent)
    problems = pace({"held": 0.85, "source": "request", "remembered": 0.85}, evidence(agent, call))
    assert problems == [
        "the call ended at 1.0, expected 0.85",
        "the pace was set by default, expected request",
        "remembered for the caller: none, expected 0.85",
    ]


def test_the_check_fails_when_an_older_callers_pace_is_wrongly_remembered(agent):
    call = agent.start()["data"]["call_id"]
    Voice(agent, call).caller("Can you talk slower? I'm 80.")
    problems = pace({"held": 0.85, "source": "older_adult", "remembered": "none"},
                    evidence(agent, call))
    assert problems == [
        "the pace was set by request, expected older_adult",
        "remembered for the caller: 0.85, expected none",
    ]


def address_call(agent, slow_part: bool, back_to_pace: bool = True) -> str:
    call = agent.start()["data"]["call_id"]
    voice = Voice(agent, call)
    voice.caller("It's 101 Union St, 11231.")
    address = in_area_address()
    asked = agent.tool("read_back", call, address=address, phone="caller_id")
    voice.says(asked["say"])
    voice.caller("Sorry, can you say that again slower?")
    again = agent.tool("read_back", call, address=address, phone="caller_id")
    for part in again["data"]["parts"]:
        speed = part["speed"] if slow_part else None
        voice.says(part["text"], speed)
    voice.caller("Yes, that's right.")
    voice.says("Great.", None if back_to_pace else 0.85)
    return call


def test_the_check_passes_when_only_the_address_was_slower(agent):
    call = address_call(agent, slow_part=True)
    assert pace({"held": 1.0, "address": 0.7, "remembered": "none"}, evidence(agent, call)) == []


def test_the_check_fails_when_the_address_was_not_spoken_slower(agent):
    call = address_call(agent, slow_part=False)
    assert pace({"address": 0.7}, evidence(agent, call)) == [
        "the address was never spoken at its own pace"
    ]


def test_the_check_fails_when_the_pace_stays_slow_after_the_address(agent):
    call = address_call(agent, slow_part=True, back_to_pace=False)
    assert pace({"held": 1.0, "address": 0.7}, evidence(agent, call)) == [
        "agent line 10 was spoken at 0.85; the server's pace was 1.0"
    ]


def test_the_check_fails_when_no_address_request_was_recorded(agent, monkeypatch):
    monkeypatch.setattr(pace_rules, "hear", lambda *a: pace_rules.Heard())
    call = agent.start()["data"]["call_id"]
    Voice(agent, call).caller("Can you say the address slower?")
    assert pace({"address": 0.85}, evidence(agent, call)) == [
        "no request for the address at 0.85 was recorded",
        "the address was never spoken at its own pace",
    ]
