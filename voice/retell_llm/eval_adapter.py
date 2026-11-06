"""The custom-LLM socket as an agent under test, with this harness playing Retell (issue #176).

Retell's simulation and chat don't reach a custom-LLM agent (SOURCE:
https://docs.retellai.com/integrate-llm/overview), so text runs speak Retell's socket protocol
to the deployed socket themselves: the call details, then a `response_required` with the
transcript for every caller line. Everything after the socket is the real thing: its model, its
speech filter and the tool server it runs against. Nothing here costs Retell anything; what Retell
adds on a phone call (ears, turn-taking, the voice) is left to the phone runs.

    RETELL_SOCKET_URL=wss://server-led.45-77-105-247.sslip.io/retell-llm \
    RETELL_EVAL_TOOL_SERVER=https://server-led.45-77-105-247.sslip.io \
    RETELL_EVAL_DEBUG_TOKEN=<SUMMIT_EVAL_TOKEN> \
      uv run --env-file .env python -m evals.run --stacks retell_llm --tags id:...

The reply delay is from sending the request to the socket's first words, so it holds the socket's
own time and the network between here and the server, not Retell's.
"""

import json
import os
import time
import uuid
from pathlib import Path

import httpx
from websockets.sync.client import connect

from evals.results import TurnTiming
from evals.stacks import AgentTurn
from evals.world import ToolServer
from prompts.assemble import instructions
from voice.retell import conversation
from voice.retell.chat import RETELL_NUMBER
from voice.retell.eval_adapter import RetellNotReady, forget_calls, reset_world

from .llm_socket import socket_key

NEEDED = ("RETELL_API_KEY", "RETELL_SOCKET_URL", "RETELL_EVAL_TOOL_SERVER", "RETELL_EVAL_DEBUG_TOKEN")
# How long a reply may take before the run calls it broken.
REPLY_TIMEOUT_S = 60
# The socket closes the call on the tool server once its socket closes; the record is read after.
CLOSE_WAIT_S = 2.0


class SocketAgent:
    def __init__(self, server_http: httpx.Client, call_id: str, caller_id: str, env: dict):
        self.server_http = server_http
        self.call_id = call_id
        self.caller_id = caller_id
        self.env = env
        self.ws = None
        self.transcript: list[dict] = []
        self.response_id = 0

    def start(self) -> AgentTurn:
        t0 = time.perf_counter()
        start = self.server_http.post("/calls/start", json={
            "call_id": self.call_id, "caller_id": self.caller_id,
            "dialed_number": RETELL_NUMBER, "direction": "inbound",
        }).json()
        if not start.get("ok"):
            raise RuntimeError(f"/calls/start failed for {self.call_id}: {start}")
        tenants = Path(self.env.get("TENANTS_DIR", "tenants"))
        variables = conversation.for_call(
            self.call_id, start, instructions, lambda slug: conversation.spoken_lines(tenants, slug)
        )
        key = socket_key(self.env["RETELL_API_KEY"])
        url = f"{self.env['RETELL_SOCKET_URL'].rstrip('/')}/{key}/eval_{uuid.uuid4().hex[:12]}"
        self.ws = connect(url, open_timeout=30)
        json.loads(self.ws.recv(timeout=REPLY_TIMEOUT_S))  # the socket's config
        self.ws.send(json.dumps({"interaction_type": "call_details", "call": {
            "call_type": "web_call", "metadata": {"summit_call_id": self.call_id},
            "retell_llm_dynamic_variables": variables,
        }}))
        greeting = self._collect(0)[0]
        delay = (time.perf_counter() - t0) * 1000
        self.transcript.append({"role": "agent", "content": greeting})
        return AgentTurn(greeting, TurnTiming(kind="plain", reply_delay_ms=delay,
                                              model_first_token_ms=delay))

    def _collect(self, response_id: int) -> tuple[str, bool, float]:
        """The response's text, whether it ends the call, and the ms to its first words."""
        parts, ended, first, t0 = [], False, 0.0, time.perf_counter()
        while True:
            event = json.loads(self.ws.recv(timeout=REPLY_TIMEOUT_S))
            if event.get("response_type") != "response" or event.get("response_id") != response_id:
                continue
            if event.get("content") and not first:
                first = (time.perf_counter() - t0) * 1000
            parts.append(event.get("content") or "")
            ended = ended or bool(event.get("end_call"))
            if event.get("content_complete"):
                return "".join(parts).strip(), ended, first

    def hear(self, caller_text: str) -> AgentTurn:
        self.transcript.append({"role": "user", "content": caller_text})
        self.response_id += 1
        t0 = time.perf_counter()
        self.ws.send(json.dumps({"interaction_type": "response_required",
                                 "response_id": self.response_id, "transcript": self.transcript}))
        text, ended, first = self._collect(self.response_id)
        total = (time.perf_counter() - t0) * 1000
        if text:
            self.transcript.append({"role": "agent", "content": text})
        delay = first or total
        return AgentTurn(text, TurnTiming(kind="plain", reply_delay_ms=delay,
                                          model_first_token_ms=delay), ended=ended)

    def end(self, reason: str) -> None:
        """The last transcript goes over, then the socket closes; the socket posts the last lines and
        closes the call on the tool server."""
        if self.ws is None:
            return
        self.ws.send(json.dumps({"interaction_type": "update_only", "transcript": self.transcript}))
        self.ws.close()
        time.sleep(CLOSE_WAIT_S)


def open_agent(call_id: str, caller_id: str, env: dict | None = None):
    """The socket under test and the tool server whose record the checks read, on a clean mock."""
    env = env if env is not None else os.environ
    missing = [k for k in NEEDED if not env.get(k)]
    if missing:
        raise RetellNotReady(f"socket runs need {', '.join(missing)} (voice/retell_llm/eval_adapter.py)")
    reset_world(env)
    forget_calls(env)
    token = env["RETELL_EVAL_DEBUG_TOKEN"]
    http = httpx.Client(base_url=env["RETELL_EVAL_TOOL_SERVER"], headers={"X-Debug-Token": token},
                        timeout=30)
    return SocketAgent(http, call_id, caller_id, env), ToolServer(http, token)
