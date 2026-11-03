"""Paid check of the local names list (tenants/<slug>/names.yaml): renders each name in a read-back
sentence with the live voice, once without the pronunciation dictionary and once with it, then
transcribes every clip back with Deepgram, with and without the keyterms.

About $0.09 of Cartesia for the 15 names (30 clips, ASSUMED from about 1,800 characters on the Pro
plan, exploration 011) and a few cents of Deepgram. Nothing is played; listen to the clips yourself.

    uv run --env-file .env python -m voice.livekit.name_clips OUT_DIR [tenant]

The speech requests send the Cartesia-Version the LiveKit plugin sends, so they show what a call
would hear.
"""

import json
import re
import sys
from pathlib import Path

import httpx
from livekit.plugins.cartesia.constants import API_VERSION as PLUGIN_CARTESIA_VERSION

from voice import names as local_names

from .clips import SAMPLE_RATE, pcm_from_wav, seconds, trim, write_wav
from .settings import ROOT, load_voice


def sentence(name: local_names.Name) -> str:
    return f"Let me read that back: 412 {name.written}. Is that right?"


def slug(name: local_names.Name) -> str:
    return re.sub(r"[^a-z]+", "-", name.written.lower()).strip("-")


def render(http: httpx.Client, key: str, text: str, dict_id: str | None) -> bytes:
    voice = load_voice()["tts"]
    body = {
        "model_id": voice["model"],
        "transcript": text,
        "voice": {"mode": "id", "id": voice["voice_id"]},
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE},
    }
    if dict_id:
        body["pronunciation_dict_id"] = dict_id
    response = http.post(
        "https://api.cartesia.ai/tts/bytes",
        headers={"X-API-Key": key, "Cartesia-Version": PLUGIN_CARTESIA_VERSION},
        json=body,
        timeout=60,
    )
    response.raise_for_status()
    return trim(pcm_from_wav(response.content))


def transcribe(http: httpx.Client, key: str, path: Path, keyterms: list[str]) -> str:
    params = [("model", load_voice()["stt"]["model"]), ("smart_format", "true")]
    params += [("keyterm", term) for term in keyterms]
    response = http.post(
        "https://api.deepgram.com/v1/listen",
        params=params,
        headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
        content=path.read_bytes(),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


def main() -> int:
    import os

    if len(sys.argv) < 2:
        sys.exit("usage: python -m voice.livekit.name_clips OUT_DIR [tenant]")
    out = Path(sys.argv[1])
    tenant = ROOT / "tenants" / (sys.argv[2] if len(sys.argv) > 2 else "summit-air")
    names = local_names.load(tenant)
    if not names.cartesia_dictionary_id:
        sys.exit("no cartesia_dictionary_id in names.yaml; run python -m voice.names push first")
    out.mkdir(parents=True, exist_ok=True)
    cartesia, deepgram = os.environ["CARTESIA_API_KEY"], os.environ["DEEPGRAM_API_KEY"]
    rows = []
    with httpx.Client() as http:
        for name in names.names:
            text = sentence(name)
            row = {"written": name.written, "says": name.says, "sentence": text}
            for kind, dict_id in (("plain", None), ("dict", names.cartesia_dictionary_id)):
                path = out / f"{slug(name)}-{kind}.wav"
                write_wav(path, render(http, cartesia, text, dict_id))
                row[kind] = {
                    "file": path.name,
                    "seconds": round(seconds(path), 2),
                    "heard": transcribe(http, deepgram, path, []),
                    "heard_with_keyterms": transcribe(http, deepgram, path, names.keyterms()),
                }
            rows.append(row)
            print(f"{name.written}: plain {row['plain']['heard']!r} | dict {row['dict']['heard']!r}")
    (out / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
