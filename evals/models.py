"""Model calls for the harness, by the named profiles in config/models.yaml.

Every call streams, so the time to the first word is measured the way a caller would notice it, and
returns its token cost from the profile's price. Tests pass a fake that has the same `complete`.
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
# Cache writes cost 1.25x and reads 0.1x the input price
# (SOURCE: https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
CACHE_WRITE = 1.25
CACHE_READ = 0.1


class ModelError(RuntimeError):
    """A model call failed or a profile can't be used."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def profile(name: str, config: dict | None = None) -> dict:
    """A live profile ("fast") or a dotted offline one ("offline.grader")."""
    config = config or load_config()
    section, _, key = name.rpartition(".")
    table = config[section] if section else config["profiles"]
    if key not in table:
        raise ModelError(f"no model profile {name!r} in config/models.yaml")
    return table[key]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd


@dataclass
class Reply:
    text: str
    tool_uses: list[dict]  # {"id", "name", "input"}
    content: list[Any]  # the assistant blocks, appended to the history unchanged
    first_token_ms: float
    total_ms: float
    stop_reason: str
    usage: Usage = field(default_factory=Usage)


class Model(Protocol):
    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> Reply: ...


def cost(
    price: dict, input_tokens: int, output_tokens: int, cache_write: int = 0, cache_read: int = 0
) -> float:
    paid_input = input_tokens + CACHE_WRITE * cache_write + CACHE_READ * cache_read
    return (paid_input * price["input"] + output_tokens * price["output"]) / 1_000_000


class AnthropicModel:
    def __init__(self, cfg: dict, client: Any = None):
        self.cfg = cfg
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.client = client

    def thinking(self) -> dict:
        if self.cfg["thinking"]:
            return {"type": "adaptive"}
        return {"type": self.cfg.get("thinking_off", "disabled")}

    def complete(self, system, messages, tools=None, max_tokens=1024, json_schema=None) -> Reply:
        params: dict[str, Any] = {
            "model": self.cfg["model"],
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "thinking": self.thinking(),
            # Caches the system prompt, tools and history so each turn pays for its new lines only.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = tools
        if json_schema:
            params["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
        start = time.perf_counter()
        first = None
        with self.client.messages.stream(**params) as stream:
            for event in stream:
                if (
                    first is None
                    and event.type == "content_block_delta"
                    and event.delta.type in ("text_delta", "input_json_delta")
                ):
                    first = time.perf_counter()
            message = stream.get_final_message()
        end = time.perf_counter()
        if message.stop_reason == "refusal":
            raise ModelError(f"{self.cfg['model']} refused the request")
        u = message.usage
        usage = Usage(
            u.input_tokens
            + (u.cache_creation_input_tokens or 0)
            + (u.cache_read_input_tokens or 0),
            u.output_tokens,
            cost(
                self.cfg["price"],
                u.input_tokens,
                u.output_tokens,
                u.cache_creation_input_tokens or 0,
                u.cache_read_input_tokens or 0,
            ),
        )
        text = "".join(b.text for b in message.content if b.type == "text")
        tool_uses = [
            {"id": b.id, "name": b.name, "input": b.input}
            for b in message.content
            if b.type == "tool_use"
        ]
        return Reply(
            text=text.strip(),
            tool_uses=tool_uses,
            content=[b.model_dump(exclude_none=True) for b in message.content],
            first_token_ms=((first or end) - start) * 1000,
            total_ms=(end - start) * 1000,
            stop_reason=message.stop_reason or "",
            usage=usage,
        )


def for_profile(name: str, config: dict | None = None) -> Model:
    cfg = profile(name, config)
    if cfg["provider"] != "anthropic":
        # The harness only speaks to Anthropic so far; the model bake-off adds the others.
        raise ModelError(
            f"profile {name!r} uses {cfg['provider']}, which the harness can't call yet"
        )
    return AnthropicModel(cfg)
