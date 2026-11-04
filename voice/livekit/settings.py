"""Worker settings from the environment. Every value has a default that works on the summit-air
server; nothing here prints a value."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Profile:
    """A model profile from config/models.yaml."""

    name: str
    provider: str
    model: str
    thinking: bool
    # How the profile switches thinking off when `thinking` is false (config/models.yaml).
    thinking_off: str = "disabled"


def load_profile(name: str = "", path: Path = ROOT / "config/models.yaml") -> Profile:
    """The named profile, MODEL_PROFILE, or the file's default, in that order."""
    config = yaml.safe_load(path.read_text())
    chosen = name or os.environ.get("MODEL_PROFILE") or config["default"]
    entry = config["profiles"][chosen]
    return Profile(
        chosen,
        entry["provider"],
        entry["model"],
        entry["thinking"],
        entry.get("thinking_off", "disabled"),
    )


def load_voice(path: Path = ROOT / "config/models.yaml") -> dict:
    """The speech-to-text and voice settings from config/models.yaml."""
    return yaml.safe_load(path.read_text())["voice"]


@dataclass(frozen=True)
class Settings:
    tool_server: str = field(
        default_factory=lambda: _env("TOOL_SERVER_URL", "http://127.0.0.1:8100")
    )
    # Must match the agent name in the LiveKit dispatch rule (scripts/phone/setup.py).
    agent_name: str = field(default_factory=lambda: _env("AGENT_NAME", "summit-air"))
    tenants_dir: Path = field(
        default_factory=lambda: Path(_env("TENANTS_DIR", str(ROOT / "tenants")))
    )
    # The tenant whose recorded lines play when the tool server can't say which tenant a call is
    # for. Empty is fine when there is only one tenant folder.
    default_tenant: str = field(default_factory=lambda: _env("DEFAULT_TENANT", ""))
    # Calls at once before the next caller hears the recorded line and leaves a voicemail (S-47).
    # 3 is the Cartesia Pro stream limit (docs/scaling.md).
    max_calls: int = field(default_factory=lambda: int(_env("MAX_CALLS", "3")))
    lines_dir: Path = field(
        default_factory=lambda: Path(_env("LINES_DIR", "/tmp/summit-air-lines"))
    )
    tool_timeout_s: float = 4.0
    # The LiveKit outbound trunk for journey 12 calls; empty turns outbound calls off.
    outbound_trunk_id: str = field(default_factory=lambda: _env("LIVEKIT_OUTBOUND_TRUNK_ID", ""))
    # The LiveKit project, for joining the room a transfer's person is dialed into.
    livekit_url: str = field(default_factory=lambda: _env("LIVEKIT_URL", ""))
    # The bucket LiveKit uploads each call's recording to (app/recordings.py reads the same
    # variables to collect it). No bucket, no recording.
    recording_bucket: str = field(default_factory=lambda: _env("RECORDING_S3_BUCKET", ""))
    recording_endpoint: str = field(default_factory=lambda: _env("RECORDING_S3_ENDPOINT", ""))
    recording_region: str = field(default_factory=lambda: _env("RECORDING_S3_REGION", ""))
    recording_access_key: str = field(default_factory=lambda: _env("RECORDING_S3_ACCESS_KEY", ""))
    recording_secret: str = field(default_factory=lambda: _env("RECORDING_S3_SECRET", ""))
    # How often the outbound poller asks the server for a call to place.
    claim_every_s: float = field(default_factory=lambda: float(_env("CLAIM_EVERY_S", "15")))


def fallback_tenant(settings: Settings) -> Path:
    """The tenant folder to use when the tool server is unreachable."""
    if settings.default_tenant:
        return settings.tenants_dir / settings.default_tenant
    folders = [p for p in settings.tenants_dir.iterdir() if (p / "agent.yaml").exists()]
    if len(folders) != 1:
        raise RuntimeError("set DEFAULT_TENANT: there is more than one tenant")
    return folders[0]
