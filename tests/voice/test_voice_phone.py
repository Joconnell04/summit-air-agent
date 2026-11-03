"""The fallback switch the watchdog uses when the worker is down (docs/architecture.md, hosting).
Twilio is replaced by a fake that keeps the number's routing."""

import httpx

from scripts.phone import fallback, twilio
from scripts.phone.setup import e164


class FakeTwilio:
    def __init__(self, trunk_sid: str | None):
        self.number = {"sid": "PN1", "trunk_sid": trunk_sid, "voice_url": None}

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/IncomingPhoneNumbers.json"):
            return httpx.Response(200, json={"incoming_phone_numbers": [self.number]})
        if request.url.path.endswith("/IncomingPhoneNumbers/PN1.json"):
            form = dict(httpx.QueryParams(request.content.decode()))
            self.number["trunk_sid"] = form.get("TrunkSid") or None
            self.number["voice_url"] = form.get("VoiceUrl", self.number["voice_url"])
            return httpx.Response(200, json=self.number)
        if request.url.path.endswith("/Trunks"):
            return httpx.Response(
                200, json={"trunks": [{"sid": "TK1", "friendly_name": "summit-air"}]}
            )
        return httpx.Response(404)


def run(fake: FakeTwilio, mode: str, monkeypatch) -> str:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC0")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "x")
    with twilio.client(httpx.MockTransport(fake.handle)) as http:
        return fallback.switch(http, mode, e164("(646) 624-0418"), "summit-air")


def test_on_moves_the_number_off_the_trunk_to_the_recorded_line(monkeypatch):
    fake = FakeTwilio("TK1")
    assert run(fake, "on", monkeypatch) == "fallback"
    assert fake.number["trunk_sid"] is None
    assert fake.number["voice_url"].startswith("https://twimlets.com/echo?Twiml=")


def test_off_puts_the_number_back_on_the_trunk(monkeypatch):
    fake = FakeTwilio(None)
    assert run(fake, "off", monkeypatch) == "worker"
    assert fake.number["trunk_sid"] == "TK1"


def test_switching_twice_changes_nothing_the_second_time(monkeypatch):
    fake = FakeTwilio("TK1")
    run(fake, "on", monkeypatch)
    url = fake.number["voice_url"]
    assert run(fake, "on", monkeypatch) == "fallback"
    assert fake.number["voice_url"] == url


def test_the_fallback_line_starts_with_the_safety_line_and_takes_a_voicemail():
    xml = fallback.twiml("If you smell gas, hang up & call 911. Leave a message.")
    assert xml.startswith("<Response><Say>If you smell gas")
    assert "&amp;" in xml and "<Record" in xml


def test_numbers_are_normalized_to_e164():
    assert e164("(646) 624-0418") == "+16466240418"
