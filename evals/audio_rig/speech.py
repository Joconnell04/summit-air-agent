"""The rig's own mouth and ears: Deepgram renders the simulated caller's lines to audio and
transcribes what the agent said, so the caller model can answer it. Model ids and prices come from
the `audio_rig` section of config/models.yaml.

Endpoints (SOURCE: https://developers.deepgram.com/reference/text-to-speech-api and
https://developers.deepgram.com/reference/speech-to-text-api/listen): POST /v1/speak returns raw
linear16 with `container=none`; POST /v1/listen takes raw linear16 given its rate.
"""
import hashlib
import os
from pathlib import Path

import httpx

BASE_URL = "https://api.deepgram.com"


class SpeechError(RuntimeError):
    pass


class Deepgram:
    def __init__(self, config: dict, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.voice = config["caller_voice"]
        self.ears = config["agent_ears"]
        key = api_key or os.environ.get("DEEPGRAM_API_KEY", "")
        if not key and client is None:
            raise SpeechError("the audio rig needs DEEPGRAM_API_KEY for the caller's voice and ears")
        self.client = client or httpx.AsyncClient(
            base_url=BASE_URL, headers={"Authorization": f"Token {key}"}, timeout=30
        )
        self.chars = 0
        self.seconds = 0.0
        # RIG_SPEECH_CACHE=<folder> keeps each caller line's audio by its text, so two runs that
        # compare one change play the same audio wherever the caller says the same words.
        cache = os.environ.get("RIG_SPEECH_CACHE", "")
        self.cache = Path(cache) if cache else None

    async def say(self, text: str, rate: int) -> bytes:
        params = {"model": self.voice["model"], "encoding": "linear16", "sample_rate": rate,
                  "container": "none"}
        path = None
        if self.cache is not None:
            key = hashlib.sha256(f"{self.voice['model']}|{rate}|{text}".encode()).hexdigest()
            path = self.cache / f"{key}.pcm"
            if path.is_file():
                return path.read_bytes()
        response = await self.client.post("/v1/speak", params=params, json={"text": text})
        if response.status_code != 200:
            raise SpeechError(f"caller voice failed ({response.status_code}): {response.text[:200]}")
        self.chars += len(text)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        return response.content

    async def hear(self, pcm: bytes, rate: int) -> str:
        if not pcm:
            return ""
        params = {"model": self.ears["model"], "encoding": "linear16", "sample_rate": rate,
                  "channels": 1, "smart_format": "true"}
        response = await self.client.post(
            "/v1/listen", params=params, content=pcm, headers={"Content-Type": "application/octet-stream"}
        )
        if response.status_code != 200:
            raise SpeechError(f"agent transcription failed ({response.status_code}): {response.text[:200]}")
        self.seconds += len(pcm) / 2 / rate
        channel = response.json()["results"]["channels"][0]
        return channel["alternatives"][0]["transcript"].strip()

    def cost(self) -> dict[str, float]:
        return {
            "caller_voice": self.chars / 1000 * self.voice["price_per_1k_chars"],
            "agent_ears": self.seconds / 60 * self.ears["price_per_minute"],
        }

    async def aclose(self) -> None:
        await self.client.aclose()
