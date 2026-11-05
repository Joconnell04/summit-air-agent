"""The rig's own mouth and ears: Deepgram renders the simulated caller's lines to audio and
transcribes what the agent said, so the caller model can answer it. Model ids and prices come from
the `audio_rig` section of config/models.yaml.

Endpoints (SOURCE: https://developers.deepgram.com/reference/text-to-speech-api and
https://developers.deepgram.com/reference/speech-to-text-api/listen): POST /v1/speak returns raw
linear16 with `container=none`; POST /v1/listen takes raw linear16 given its rate.
"""
import os

import httpx

BASE_URL = "https://api.deepgram.com"
# Silence put before the agent's audio when it is transcribed. A word that starts on the very
# first sample was lost without it ("Thanks for calling" heard as "For calling", 6 of 20 Retell
# greetings, MEASURED by the latency lane); word times are moved back by the same amount.
LEAD_S = 0.3


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

    async def say(self, text: str, rate: int) -> bytes:
        params = {"model": self.voice["model"], "encoding": "linear16", "sample_rate": rate,
                  "container": "none"}
        response = await self.client.post("/v1/speak", params=params, json={"text": text})
        if response.status_code != 200:
            raise SpeechError(f"caller voice failed ({response.status_code}): {response.text[:200]}")
        self.chars += len(text)
        return response.content

    async def hear(self, pcm: bytes, rate: int) -> str:
        return (await self.hear_words(pcm, rate))[0]

    async def hear_words(self, pcm: bytes, rate: int) -> tuple[str, list[tuple[str, float, float]]]:
        """The transcript, and each word with its start and end in seconds into `pcm`."""
        if not pcm:
            return "", []
        params = {"model": self.ears["model"], "encoding": "linear16", "sample_rate": rate,
                  "channels": 1, "smart_format": "true"}
        padded = b"\0" * (round(LEAD_S * rate) * 2) + pcm
        response = await self.client.post(
            "/v1/listen", params=params, content=padded,
            headers={"Content-Type": "application/octet-stream"}
        )
        if response.status_code != 200:
            raise SpeechError(f"agent transcription failed ({response.status_code}): {response.text[:200]}")
        self.seconds += len(padded) / 2 / rate
        best = response.json()["results"]["channels"][0]["alternatives"][0]
        words = [(w.get("punctuated_word") or w["word"], max(0.0, float(w["start"]) - LEAD_S),
                  max(0.0, float(w["end"]) - LEAD_S))
                 for w in best.get("words", [])]
        return best["transcript"].strip(), words

    def cost(self) -> dict[str, float]:
        return {
            "caller_voice": self.chars / 1000 * self.voice["price_per_1k_chars"],
            "agent_ears": self.seconds / 60 * self.ears["price_per_minute"],
        }

    async def aclose(self) -> None:
        await self.client.aclose()
