"""The owner's Background noise switch (Settings): Retell plays a quiet office sound under the
agent's voice. Retell keeps this on the voice agent, not on a call, so turning the switch updates
the company's live Retell agent, named in its agent.yaml (`retell.agent`), and the next call
hears it. push.py never copies the sound from Jackson's agent, so a push leaves the switch alone.

Shared server code doesn't import a voice layer, so this is its own small client, on the
versioned list endpoint (CLAUDE.md).
"""

import os

import httpx

from .tenant import Tenant

BASE_URL = "https://api.retellai.com"


class AmbientError(RuntimeError):
    pass


def _agent_id(http: httpx.Client, headers: dict, name: str) -> str:
    found, key = [], None
    while True:
        page = http.post("/v2/list-agents", headers=headers,
                         json={"pagination_key": key} if key else {})
        page.raise_for_status()
        body = page.json()
        found += [a for a in body.get("items", []) if a.get("agent_name") == name]
        key = body.get("pagination_key")
        if not body.get("has_more") or not key:
            break
    if len(found) != 1:
        raise AmbientError(f"Expected one Retell agent named {name!r}, found {len(found)}.")
    return found[0]["agent_id"]


def apply(tenant: Tenant, on: bool, api_key: str | None = None,
          http: httpx.Client | None = None) -> None:
    """Set the live agent's background sound to the company's choice, or none."""
    retell = tenant.agent.get("retell") or {}
    name = retell.get("agent")
    noise = retell.get("background_noise") or {}
    if not name or not noise.get("sound"):
        raise AmbientError("No Retell agent or sound is set for this company.")
    key = api_key or os.environ.get("RETELL_API_KEY", "")
    if not key:
        raise AmbientError("RETELL_API_KEY is not set on the server.")
    http = http or httpx.Client(base_url=BASE_URL, timeout=20)
    headers = {"Authorization": f"Bearer {key}"}
    try:
        agent_id = _agent_id(http, headers, name)
        body = {"ambient_sound": noise["sound"] if on else None}
        if on:
            body["ambient_sound_volume"] = float(noise.get("volume", 0.5))
        response = http.patch(f"/update-agent/{agent_id}", headers=headers, json=body)
        response.raise_for_status()
    except httpx.HTTPError as err:
        raise AmbientError(f"Retell didn't take the change ({err}).") from err
