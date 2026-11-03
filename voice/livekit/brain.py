"""The model the worker talks to, built from a profile in config/models.yaml."""

from livekit.agents.types import NOT_GIVEN
from livekit.plugins import anthropic

from .settings import Profile, load_profile


def thinking_request(profile: Profile) -> dict:
    """The `thinking` field every Anthropic request carries. Current models think by default when
    the field is left out, which put 1 to 2 s in front of the first word (docs/latency.md), so a
    profile with `thinking: false` says so on every request."""
    if profile.thinking:
        return {"type": "adaptive"}
    return {"type": profile.thinking_off}


class ClaudeLLM(anthropic.LLM):
    """The LiveKit Anthropic plugin with one request field it has no setting for."""

    def __init__(self, *, thinking: dict, **kwargs):
        super().__init__(**kwargs)
        self._thinking = thinking

    def chat(self, *, extra_kwargs=NOT_GIVEN, **kwargs):
        # Fields set by the caller of chat() win; the session sets none.
        merged = {"thinking": self._thinking, **(extra_kwargs or {})}
        return super().chat(extra_kwargs=merged, **kwargs)


def build_llm(profile: Profile | None = None):
    profile = profile or load_profile()
    if profile.provider == "anthropic":
        # Prompt caching keeps the long, fixed instructions from adding to every reply's delay.
        # No temperature: current Claude models refuse it.
        return ClaudeLLM(
            model=profile.model, caching="ephemeral", thinking=thinking_request(profile)
        )
    if profile.provider == "openai":
        from livekit.plugins import openai

        # The plugin keeps reasoning at its lowest for reasoning models.
        return openai.LLM(model=profile.model)
    if profile.provider == "gemini":
        from livekit.plugins import google

        return google.LLM(model=profile.model)
    raise ValueError(f"unknown provider {profile.provider}")
