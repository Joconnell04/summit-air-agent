"""Pre-recorded clips: the bridge lines and the busy-line message (agent.yaml, voice.clips).

They are recorded once with the live voice and committed, so playing one needs no voice stream.
Regenerate after changing the text:

    uv run --env-file .env python -m voice.archive.livekit.clips [tenant]
"""

import array
import sys
import wave
from pathlib import Path

import httpx
import yaml

from .bridge import MAX_CLIP_SECONDS
from .settings import ROOT, load_voice

SAMPLE_RATE = 24000


def folder(tenant_dir: Path) -> Path:
    return tenant_dir / "clips"


def seconds(path: Path) -> float:
    with wave.open(str(path)) as clip:
        return clip.getnframes() / clip.getframerate()


def bridge_clips(tenant_dir: Path) -> list[str]:
    """Paths of the tenant's bridge clips, in the order they are offered. A clip that is missing or
    too long is left out, so a bad recording can never hold up a reply."""
    texts = yaml.safe_load((tenant_dir / "agent.yaml").read_text())["voice"]["clips"]
    found = []
    for name in texts:
        path = folder(tenant_dir) / f"{name}.wav"
        if name.startswith("bridge_") and path.exists() and seconds(path) <= MAX_CLIP_SECONDS:
            found.append(str(path))
    return found


def record(tenant_dir: Path, api_key: str) -> list[Path]:
    voice = load_voice()["tts"]
    texts = yaml.safe_load((tenant_dir / "agent.yaml").read_text())["voice"]["clips"]
    folder(tenant_dir).mkdir(exist_ok=True)
    written = []
    for name, text in texts.items():
        response = httpx.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={"X-API-Key": api_key, "Cartesia-Version": "2025-04-16"},
            json={
                "model_id": voice["model"],
                "transcript": " ".join(text.split()),
                "voice": {"mode": "id", "id": voice["voice_id"]},
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        path = folder(tenant_dir) / f"{name}.wav"
        write_wav(path, trim(pcm_from_wav(response.content)))
        written.append(path)
    return written


def pcm_from_wav(body: bytes) -> bytes:
    """The samples of a streamed WAV, whose header gives no real length."""
    return body[body.index(b"data") + 8 :]


def trim(pcm: bytes, threshold: int = 300, keep_s: float = 0.05) -> bytes:
    """Cut the silence the voice adds before and after the words, keeping a short margin."""
    samples = array.array("h", pcm)
    loud = [i for i, v in enumerate(samples) if abs(v) > threshold]
    if not loud:
        return pcm
    margin = int(keep_s * SAMPLE_RATE)
    return samples[max(loud[0] - margin, 0) : loud[-1] + margin].tobytes()


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)


if __name__ == "__main__":
    import os

    tenant = ROOT / "tenants" / (sys.argv[1] if len(sys.argv) > 1 else "summit-air")
    for path in record(tenant, os.environ["CARTESIA_API_KEY"]):
        print(f"{path.name}: {seconds(path):.2f} s")
