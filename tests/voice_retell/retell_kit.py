"""Builders for Retell's payloads, signed the way Retell signs them."""

import json

from fastapi.testclient import TestClient

from voice.retell import signature

KEY = "key_test_retell"
NOW_MS = 1_793_000_000_000
RETELL_NUMBER = "+16783366730"
# The tenant's spoken lines (agent.yaml `voice:`), as the LiveKit lane defines them.
SPOKEN = {
    "greeting_known": "Hi, this is Summit Air. Is this {first_name}?",
    "greeting_unknown": "Thanks for calling Summit Air, this is the virtual assistant. How can I help?",
    "server_down": "Sorry, our system is having trouble right now. Please call us back.",
}


def signed(client: TestClient, path: str, body: dict, key: str = KEY, at: int = NOW_MS):
    raw = json.dumps(body).encode()
    headers = {
        "X-Retell-Signature": signature.sign(raw, key, at),
        "Content-Type": "application/json",
    }
    return client.post(path, content=raw, headers=headers)


def utterance(role: str, text: str, start: float | None = None, gap: float = 0.3) -> dict:
    """A transcript_object entry; with start, each word gets a timing gap seconds long."""
    entry = {"role": role, "content": text}
    if start is not None:
        entry["words"] = [
            {"word": w, "start": start + i * gap, "end": start + (i + 1) * gap - 0.05}
            for i, w in enumerate(text.split())
        ]
    return entry


def call_obj(rid: str, *entries: dict, caller: str = "+12125550999", **extra) -> dict:
    return {
        "call_id": rid,
        "call_type": "phone_call",
        "from_number": caller,
        "to_number": RETELL_NUMBER,
        "direction": "inbound",
        "retell_llm_dynamic_variables": {"summit_call_id": "retell_" + rid},
        "transcript_object": list(entries),
        **extra,
    }


def inbound(client: TestClient, rid: str, caller: str = "+12125550999") -> dict:
    body = {
        "event": "call_inbound",
        "call_inbound": {"call_id": rid, "from_number": caller, "to_number": RETELL_NUMBER},
    }
    response = signed(client, "/retell/inbound", body)
    assert response.status_code == 200, response.text
    return response.json()["call_inbound"]


def tool(client: TestClient, name: str, obj: dict, **args) -> dict:
    response = signed(client, f"/retell/tools/{name}", {"name": name, "call": obj, "args": args})
    assert response.status_code == 200, response.text
    return response.json()


def event(client: TestClient, name: str, obj: dict) -> None:
    response = signed(client, "/retell/webhook", {"event": name, "call": obj})
    assert response.status_code == 204, response.text
