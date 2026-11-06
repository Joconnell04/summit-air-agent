"""A thin client for the Retell endpoints this project uses, and only those.

Lists go through the versioned endpoints (POST /v2/list-agents, GET /v2/list-retell-llms,
GET /v2/list-phone-numbers) and read the paginated `items` field; the unversioned list endpoints
are removed and Retell emails a warning for every use (CLAUDE.md). Create, get and update have no
versioned form in Retell's current spec, so they use the paths it documents. Any other path is
refused before a request is sent, and a test holds the list to this set.
"""

import json as json_module
import re

import httpx

BASE_URL = "https://api.retellai.com"

ALLOWED = (
    ("GET", r"/v2/list-retell-llms"),
    ("POST", r"/v2/list-agents"),
    ("GET", r"/v2/list-phone-numbers"),
    ("GET", r"/get-retell-llm/[\w-]+"),
    ("POST", r"/create-retell-llm"),
    ("PATCH", r"/update-retell-llm/[\w-]+"),
    ("POST", r"/create-agent"),
    # The audio rig's web calls (voice/retell/rig_adapter.py).
    ("POST", r"/v3/create-web-call"),
    # The socket arm's scripted caller: a Retell agent dialing one of our Retell numbers from
    # another (retell/arms/socket/retell_caller.py).
    ("POST", r"/v2/create-phone-call"),
    ("GET", r"/get-agent/[\w-]+"),
    ("PATCH", r"/update-agent/[\w-]+"),
    ("POST", r"/create-chat-agent"),
    ("GET", r"/get-chat-agent/[\w-]+"),
    ("PATCH", r"/update-chat-agent/[\w-]+"),
    ("PATCH", r"/update-phone-number/\+\d+"),
    ("GET", r"/v2/get-call/[\w-]+"),
    # Whether a call is live on a line before a test run points it elsewhere (retell/arms/socket/line.py).
    ("POST", r"/v3/list-calls"),
    ("PATCH", r"/v2/update-live-call/[\w-]+"),
    ("POST", r"/v2/stop-call/[\w-]+"),
    ("POST", r"/create-chat"),
    ("POST", r"/create-chat-completion"),
    ("GET", r"/get-chat/[\w-]+"),
    ("PATCH", r"/update-chat/[\w-]+"),
    ("PATCH", r"/end-chat/[\w-]+"),
    # The Conversation Flow agent (voice/retell_flow/push.py). It finds its flow through the agent
    # that holds it, so it needs no list.
    ("POST", r"/create-conversation-flow"),
    ("GET", r"/get-conversation-flow/[\w-]+"),
    ("PATCH", r"/update-conversation-flow/[\w-]+"),
    ("GET", r"/list-voices"),
    ("POST", r"/add-community-voice"),
    # The native agent's knowledge base (voice/retell_native/push.py), found by its content name.
    ("GET", r"/list-knowledge-bases"),
    ("POST", r"/create-knowledge-base"),
    ("GET", r"/get-knowledge-base/[\w-]+"),
)


class RetellError(RuntimeError):
    pass


def allowed(method: str, path: str) -> bool:
    return any(m == method and re.fullmatch(p, path) for m, p in ALLOWED)


class Retell:
    def __init__(self, api_key: str, client: httpx.Client | None = None, timeout: float = 20.0):
        self.client = client or httpx.Client(base_url=BASE_URL, timeout=timeout)
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def request(self, method: str, path: str, json: dict | None = None, params=None):
        if not allowed(method, path):
            raise RetellError(f"{method} {path} is not an endpoint this project uses")
        response = self.client.request(method, path, json=json, params=params, headers=self.headers)
        if response.status_code >= 400:
            raise RetellError(f"{method} {path}: {response.status_code} {response.text[:300]}")
        return response.json() if response.content else None

    def create_knowledge_base(self, name: str, texts: list[dict]) -> dict:
        """The one multipart request (create-knowledge-base takes form data, not JSON)."""
        path = "/create-knowledge-base"
        if not allowed("POST", path):
            raise RetellError(f"POST {path} is not an endpoint this project uses")
        response = self.client.post(path, headers=self.headers, files={
            "knowledge_base_name": (None, name),
            "knowledge_base_texts": (None, json_module.dumps(texts)),
        })
        if response.status_code >= 400:
            raise RetellError(f"POST {path}: {response.status_code} {response.text[:300]}")
        return response.json()

    def list_all(self, method: str, path: str) -> list[dict]:
        """Every item of a paginated list, following pagination_key until has_more is false."""
        items, key = [], None
        while True:
            if method == "GET":
                page = self.request("GET", path, params={"pagination_key": key} if key else None)
            else:
                page = self.request("POST", path, json={"pagination_key": key} if key else {})
            items += page.get("items", [])
            key = page.get("pagination_key")
            if not page.get("has_more") or not key:
                return items

    # ---- live calls and chats ------------------------------------------------------------------

    def say_next(self, call_id: str, context: str) -> None:
        """Hand the agent text for its next reply and make it reply now, cutting in if it is
        talking (Update Live Call, call_control)."""
        self.request(
            "PATCH",
            f"/v2/update-live-call/{call_id}",
            json={"call_control": {"additional_context": context, "trigger_response": True}},
        )

    def add_context(self, call_id: str, context: str) -> None:
        """Text the agent reads with its next reply, without cutting in."""
        self.request(
            "PATCH",
            f"/v2/update-live-call/{call_id}",
            json={"call_control": {"additional_context": context}},
        )

    def keep_no_recording(self, call_id: str) -> None:
        """Retell keeps no recording or transcript of this call once its webhooks have gone out
        (Update Live Call, data_storage_setting; it can only be made stricter)."""
        self.request(
            "PATCH",
            f"/v2/update-live-call/{call_id}",
            json={"fields_to_override": {"data_storage_setting": "basic_attributes_only"}},
        )

    def stop_call(self, call_id: str) -> None:
        self.request("POST", f"/v2/stop-call/{call_id}")

    def get_call(self, call_id: str) -> dict:
        return self.request("GET", f"/v2/get-call/{call_id}")
