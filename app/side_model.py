"""A side model: one narrow yes-or-no question about the caller's words, answered beside the live
call (DECISIONS.md 006, evals/decision-models/PLAN.md). The server still decides. The answer can
only raise priority (app/priority.py), and a slow or failed answer changes nothing, so the call
falls back to the phrase rules it had without one.

The question is asked as soon as a caller line arrives (`prefetch`), so the answer is usually back
before the agent reaches find_slots or book. The gate then waits at most `wait_s` for it.

Backends, picked by a profile name under `side` in config/models.yaml (SIDE_MODEL=<name>):
- anthropic: a narrow Messages API call that returns a probability as JSON.
- openai: OpenAI Decisions, `POST /v1/decisions`, a predicate question.
- typesafe: Jev, `POST /v1/systemone`, a noul question.
"""

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import httpx
import yaml

log = logging.getLogger("summit.side_model")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "prompts" / "side" / "questions.yaml"
# The gate's longest wait for an answer that is still on its way (ASSUMED; docs/latency.md budgets
# a lookup turn at 2.5 s, and find_slots already spends some of it).
WAIT_S = 0.4
# A request that has not answered by then is dropped (ASSUMED, above the 1.0 s slowest answer the
# earlier practice agent measured for OpenAI Decisions).
REQUEST_TIMEOUT_S = 2.0
KEEP = 500  # recent answers kept, oldest dropped first


def load_question(name: str, path: Path = QUESTIONS_PATH) -> dict:
    return yaml.safe_load(path.read_text())[name]


def load_profile(name: str, path: Path = CONFIG_PATH) -> dict:
    config = yaml.safe_load(path.read_text())
    profiles = config.get("side") or {}
    if name not in profiles:
        raise ValueError(f"no side model profile {name!r} in config/models.yaml")
    cfg = dict(profiles[name])
    cfg["key_env"] = config["providers"][cfg["provider"]]["key_env"]
    return cfg


def _anthropic(http: httpx.Client, cfg: dict, key: str, question: dict, text: str):
    body = {
        "model": cfg["model"],
        "max_tokens": 50,
        "thinking": {"type": "disabled"},
        "system": question["system"],
        "messages": [{"role": "user", "content": f"Caller's words so far:\n{text}"}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {"probability_yes": {"type": "integer"}},
                    "required": ["probability_yes"],
                    "additionalProperties": False,
                },
            }
        },
    }
    r = http.post(
        "https://api.anthropic.com/v1/messages",
        json=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    r.raise_for_status()
    reply = r.json()
    out = "".join(b.get("text", "") for b in reply["content"] if b["type"] == "text")
    usage = reply.get("usage") or {}
    tokens = (usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return min(max(int(json.loads(out)["probability_yes"]), 0), 100) / 100, tokens


def _openai(http: httpx.Client, cfg: dict, key: str, question: dict, text: str):
    body = {
        "model": cfg["model"],
        "input": f"Caller: {text}",
        "questions": [
            {"name": "answer", "type": "predicate", "instructions": question["instructions"]}
        ],
    }
    r = http.post(
        "https://api.openai.com/v1/decisions", json=body, headers={"Authorization": f"Bearer {key}"}
    )
    r.raise_for_status()
    reply = r.json()
    usage = reply.get("usage") or {}
    tokens = (usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    for answer in reply.get("answers", []):
        if answer.get("name") == "answer" and answer.get("type") == "predicate":
            return float(answer["probability"]), tokens
    raise ValueError("no predicate answer (refused or missing)")


def _typesafe(http: httpx.Client, cfg: dict, key: str, question: dict, text: str):
    body = {
        "model": cfg["model"],
        "state": f"Caller: {text}",
        "questions": {"answer": {"type": "noul", "instructions": question["instructions"]}},
    }
    r = http.post(
        "https://api.typesafe.ai/v1/systemone",
        json=body,
        headers={"Authorization": f"Bearer {key}"},
    )
    r.raise_for_status()
    reply = r.json()
    usage = reply.get("usage") or {}
    tokens = (usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return float(reply["answers"]["answer"]["noul"]), tokens


# Each returns the probability of yes and the (input, output) tokens the provider reported.
BACKENDS = {"anthropic": _anthropic, "openai": _openai, "typesafe": _typesafe}


class Asker:
    """One question to one backend, synchronous. Raises on any failure."""

    def __init__(self, cfg: dict, question: dict, http: httpx.Client | None = None,
                 key: str | None = None):
        if cfg["provider"] not in BACKENDS:
            raise ValueError(f"no side model backend for provider {cfg['provider']!r}")
        self.cfg = cfg
        self.question = question
        self.http = http or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self.key = key if key is not None else os.environ.get(cfg["key_env"], "")
        self.tokens = [0, 0]  # input and output, summed over every answer, for the cost

    def __call__(self, text: str) -> float:
        if not self.key:
            raise RuntimeError(f"{self.cfg['key_env']} is not set")
        backend = BACKENDS[self.cfg["provider"]]
        value, (used_in, used_out) = backend(self.http, self.cfg, self.key, self.question, text)
        self.tokens[0] += used_in
        self.tokens[1] += used_out
        return value

    def cost_usd(self) -> float:
        price = self.cfg["price"]
        return (self.tokens[0] * price["input"] + self.tokens[1] * price["output"]) / 1_000_000


class SideModel:
    """The server's side model: asks early, answers the gate from what came back in time.
    `ask(question, text)` returns the probability of yes; the question names an entry in
    prompts/side/questions.yaml, chosen by the tenant's priority config."""

    def __init__(self, ask, wait_s: float = WAIT_S, workers: int = 4):
        self.ask = ask
        self.wait_s = wait_s
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="side-model")
        self.answers: OrderedDict[str, Future] = OrderedDict()
        self.guard = threading.Lock()

    def _future(self, question: str, text: str) -> Future:
        key = hashlib.sha256(f"{question}\n{text}".encode()).hexdigest()
        with self.guard:
            future = self.answers.get(key)
            if future is None:
                future = self.pool.submit(self._timed, question, text)
                self.answers[key] = future
                while len(self.answers) > KEEP:
                    self.answers.popitem(last=False)
            return future

    def _timed(self, question: str, text: str) -> float:
        started = time.perf_counter()
        value = self.ask(question, text)
        ms = round((time.perf_counter() - started) * 1000)
        log.info(json.dumps({"event": "side_model", "question": question, "ms": ms}))
        return value

    def prefetch(self, question: str, text: str) -> None:
        if text.strip():
            self._future(question, text)

    def score(self, question: str, text: str) -> float | None:
        """The probability of yes, or None when it is not back within wait_s or failed."""
        if not text.strip():
            return None
        started = time.perf_counter()
        try:
            value = self._future(question, text).result(timeout=self.wait_s)
        except FutureTimeout:
            log.info(json.dumps({"event": "side_model_late", "question": question}))
            return None
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the phrase rules
            log.info(json.dumps({"event": "side_model_failed", "error": type(exc).__name__}))
            return None
        waited = round((time.perf_counter() - started) * 1000)
        log.info(json.dumps({"event": "side_model_answer", "question": question, "waited_ms": waited}))
        return value


def stripped_profile(path: Path | None = None) -> bool:
    """config/models.yaml says `voice.profile: stripped` (or the copy MODELS_CONFIG names): the
    one-page prompt with no side model, speech filter, owner guidance or stage notes."""
    config = yaml.safe_load((path or Path(os.environ.get("MODELS_CONFIG") or CONFIG_PATH)).read_text())
    return (config.get("voice") or {}).get("profile", "full") == "stripped"


def from_settings(profile: str, stripped: bool | None = None) -> SideModel | None:
    """The side model SIDE_MODEL names, or None when it is empty or the profile is stripped."""
    if stripped is None:
        stripped = stripped_profile()
    if not profile or stripped:
        return None
    cfg = load_profile(profile)
    askers: dict[str, Asker] = {}

    def ask(question: str, text: str) -> float:
        if question not in askers:
            askers[question] = Asker(cfg, load_question(question))
        return askers[question](text)

    return SideModel(ask)
