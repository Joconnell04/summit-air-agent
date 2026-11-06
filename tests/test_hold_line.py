"""The placeholder office line (scripts/phone/hold_line.py): fixed TwiML in the voice URL, the
hold line said once, a short reminder about every 40 seconds, and another project's number and
any trunked number left alone."""

import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

from scripts.phone import hold_line


def test_the_twiml_says_the_hold_line_then_reminds_on_a_steady_gap():
    root = ET.fromstring(hold_line.twiml())
    said = [e.text for e in root if e.tag == "Say"]
    assert said[1] == "Thanks for holding. Please wait for the next available team member."
    assert said.count("Thanks for holding, someone will be with you shortly.") == hold_line.REPEATS
    pauses = [int(e.get("length")) for e in root if e.tag == "Pause"]
    assert set(pauses[1:]) == {hold_line.GAP_SECONDS}
    assert 30 <= hold_line.GAP_SECONDS <= 45
    assert root[-1].tag == "Hangup"


def test_the_voice_url_carries_the_twiml_exactly():
    url = urlparse(hold_line.voice_url())
    assert url.netloc == "twimlets.com" and url.path == "/echo"
    assert parse_qs(url.query)["Twiml"] == [hold_line.twiml()]


def test_another_projects_number_is_refused(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["hold_line", "--number", "+16787376865"])
    assert hold_line.main() == 2
    assert "another project" in capsys.readouterr().err


def test_the_voice_url_fits_twilios_url_limit():
    assert len(hold_line.voice_url()) < 2048
