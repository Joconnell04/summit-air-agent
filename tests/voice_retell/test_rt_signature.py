"""Every request from Retell is signed; the bridge refuses anything else."""

from retell_kit import KEY, NOW_MS, call_obj, signed

from voice.retell import signature

BODY = b'{"event":"call_started"}'


def test_a_signature_made_with_the_key_verifies():
    header = signature.sign(BODY, KEY, NOW_MS)
    assert signature.verify(BODY, KEY, header, NOW_MS + 1000)


def test_a_changed_body_a_wrong_key_or_a_missing_header_fails():
    header = signature.sign(BODY, KEY, NOW_MS)
    assert not signature.verify(BODY + b" ", KEY, header, NOW_MS)
    assert not signature.verify(BODY, "another_key", header, NOW_MS)
    assert not signature.verify(BODY, KEY, None, NOW_MS)
    assert not signature.verify(BODY, KEY, "v=abc,d=123", NOW_MS)
    assert not signature.verify(BODY, "", header, NOW_MS)


def test_a_signature_older_than_five_minutes_fails():
    header = signature.sign(BODY, KEY, NOW_MS)
    assert signature.verify(BODY, KEY, header, NOW_MS + 299_000)
    assert not signature.verify(BODY, KEY, header, NOW_MS + 301_000)


def test_the_bridge_refuses_unsigned_or_wrongly_signed_requests(client):
    body = {"name": "check_service_area", "call": call_obj("abc"), "args": {"zip": "11231"}}
    assert client.post("/retell/tools/check_service_area", json=body).status_code == 401
    wrong = signed(client, "/retell/tools/check_service_area", body, key="not_the_key")
    assert wrong.status_code == 401
    assert signed(client, "/retell/webhook", {"event": "call_started"}, key="x").status_code == 401
    assert signed(client, "/retell/inbound", {"event": "call_inbound"}, key="x").status_code == 401
    stale = signed(client, "/retell/webhook", {"event": "call_started"}, at=NOW_MS - 400_000)
    assert stale.status_code == 401
