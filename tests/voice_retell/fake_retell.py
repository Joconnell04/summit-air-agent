"""A stand-in for Retell's API, enough for push.py: it stores what is created, fills in defaults
the way Retell does (so a naive comparison would see a change every time), and records every
request."""

import copy
import json
import re

import httpx

from voice.retell.api import BASE_URL, Retell

# Fields Retell adds on its side; push.py must not mistake them for differences.
LLM_DEFAULTS = {"version": 0, "is_published": False, "last_modification_timestamp": 1}
TOOL_DEFAULTS = {"execution_message_type": "prompt", "parameter_type": "json"}
AGENT_DEFAULTS = {"version": 0, "volume": 1, "language": "en-US", "is_published": False}


class FakeRetell:
    def __init__(self, recorded: dict | None = None):
        recorded = copy.deepcopy(recorded or {})
        self.llms: dict[str, dict] = recorded.get("llms", {})
        self.agents: dict[str, dict] = recorded.get("agents", {})
        self.numbers: list[dict] = recorded.get("numbers", [])
        self.voices: list[dict] = recorded.get("voices", [])
        self.requests: list[tuple[str, str, dict | None]] = []
        self.ids = 0

    def client(self) -> Retell:
        transport = httpx.MockTransport(self.handle)
        return Retell("key_test", httpx.Client(base_url=BASE_URL, transport=transport))

    def writes(self) -> list[tuple[str, str, dict | None]]:
        return [r for r in self.requests if r[0] in ("PATCH", "DELETE") or "/create-" in r[1]
                or r[1] == "/add-community-voice"]

    def new_id(self, prefix: str) -> str:
        self.ids += 1
        return f"{prefix}_{self.ids:04d}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else None
        self.requests.append((method, path, body))
        found = self.route(method, path, body)
        if found is None:
            return httpx.Response(404, json={"error": f"no route {method} {path}"})
        return httpx.Response(200, json=found)

    def route(self, method: str, path: str, body):
        if (method, path) == ("POST", "/v2/list-agents"):
            return {"items": list(self.agents.values()), "has_more": False}
        if (method, path) == ("GET", "/v2/list-phone-numbers"):
            return {"items": self.numbers, "has_more": False}
        if (method, path) == ("GET", "/list-voices"):
            return self.voices
        if (method, path) == ("POST", "/add-community-voice"):
            voice = {"voice_id": self.new_id("custom_voice"), "voice_type": "custom",
                     "provider": body["voice_provider"], "voice_name": body["voice_name"]}
            self.voices.append(voice)
            return voice
        if (method, path) == ("POST", "/create-retell-llm"):
            llm = {**LLM_DEFAULTS, **copy.deepcopy(body), "llm_id": self.new_id("llm")}
            llm["general_tools"] = [{**TOOL_DEFAULTS, **t} for t in llm.get("general_tools", [])]
            self.llms[llm["llm_id"]] = llm
            return llm
        if path in ("/create-agent", "/create-chat-agent") and method == "POST":
            agent = {**AGENT_DEFAULTS, **copy.deepcopy(body), "agent_id": self.new_id("agent")}
            self.agents[agent["agent_id"]] = agent
            return agent
        match = re.fullmatch(r"/(get|update)-(retell-llm|agent|chat-agent)/([\w-]+)", path)
        if match:
            verb, kind, ident = match.groups()
            store = self.llms if kind == "retell-llm" else self.agents
            if ident not in store:
                return None
            if verb == "update":
                store[ident].update(copy.deepcopy(body))
                if kind == "retell-llm" and "general_tools" in body:
                    store[ident]["general_tools"] = [
                        {**TOOL_DEFAULTS, **t} for t in body["general_tools"]
                    ]
            return store[ident]
        match = re.fullmatch(r"/update-phone-number/(\+\d+)", path)
        if match:
            number = next(n for n in self.numbers if n["phone_number"] == match.group(1))
            number.update(copy.deepcopy(body))
            return number
        return None
