"""The number router: livekit puts back exactly what was saved, and the other entries move the
number off the LiveKit trunk without touching it."""

import json

import httpx
import pytest

from scripts import route_number

LIVEKIT_TRUNK = "TK_livekit"
SAVED = {
    "number": {"phone_number": "+16466240418", "sid": "PN1", "trunk_sid": LIVEKIT_TRUNK,
               "voice_url": None, "voice_method": "POST", "voice_application_sid": None,
               "voice_fallback_url": None},
    "trunk": {"sid": LIVEKIT_TRUNK, "friendly_name": "summit-air",
              "domain_name": "summit-air-1.pstn.twilio.com",
              "origination": ["sip:x.sip.livekit.cloud;transport=tcp"]},
}


class FakeTwilio:
    def __init__(self):
        self.number = {**SAVED["number"], "voice_url": "", "voice_application_sid": "",
                       "voice_fallback_url": ""}
        self.trunks = [{"sid": LIVEKIT_TRUNK, "friendly_name": "summit-air",
                        "domain_name": SAVED["trunk"]["domain_name"]}]
        self.writes: list[tuple[str, dict]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            form = dict(httpx.QueryParams(request.content.decode()))
            self.writes.append((path, form))
            if path.endswith(f"/IncomingPhoneNumbers/{self.number['sid']}.json"):
                self.number.update(trunk_sid=form["TrunkSid"] or None, voice_url=form["VoiceUrl"])
                return httpx.Response(200, json=self.number)
        if path.endswith("/IncomingPhoneNumbers.json"):
            return httpx.Response(200, json={"incoming_phone_numbers": [self.number]})
        if path.endswith("/Trunks"):
            return httpx.Response(200, json={"trunks": self.trunks})
        raise AssertionError(f"unexpected {request.method} {path}")


@pytest.fixture
def twilio(monkeypatch, tmp_path):
    fake = FakeTwilio()
    snapshot = tmp_path / "routing-before.json"
    snapshot.write_text(json.dumps(SAVED))
    monkeypatch.setattr(route_number, "SNAPSHOT", snapshot)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "(646) 624-0418")
    monkeypatch.setattr(route_number.twilio, "client",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


def test_livekit_changes_nothing_when_the_number_is_already_there(twilio, capsys):
    route_number.main("livekit")
    assert twilio.writes == []
    assert "-> livekit (no change)" in capsys.readouterr().out


def test_deepgram_then_livekit_puts_the_number_back_on_its_trunk(twilio, capsys):
    route_number.main("deepgram")
    assert twilio.number["trunk_sid"] is None
    assert twilio.number["voice_url"].endswith("/deepgram/twiml")
    route_number.main("livekit")
    assert twilio.number["trunk_sid"] == LIVEKIT_TRUNK and not twilio.number["voice_url"]
    out = capsys.readouterr().out
    assert "-> deepgram" in out and out.rstrip().endswith("-> livekit")


def test_an_entry_without_its_key_changes_nothing(twilio, monkeypatch):
    monkeypatch.delenv("VAPI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="VAPI_API_KEY"):
        route_number.main("vapi")
    assert twilio.writes == []
