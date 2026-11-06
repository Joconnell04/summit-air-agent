"""Replays a caller's recorded replies into a Retell web call under each set of turn-taking dials,
and reports which replies Retell heard and how long it took to answer.

Built after live call 3 (Retell call call_ca9bfcec0b968db42ac2f428bea), where a quiet one-word
"sure" never reached the agent. The clips are cut from that call's caller channel, so the test
plays the exact audio Retell missed. The probe agent is a test agent with a one-line prompt: it
asks a yes-or-no question every turn, so every reply it gets is short and the only thing that
varies between runs is the dials.

    uv run --env-file .env python -m voice.retell.turn_probe CLIPS_DIR OUT.json

Each clip is played once the agent has been quiet for QUIET_S. A clip counts as heard when the
agent starts speaking within ANSWER_WAIT_S of the clip's end and Retell's transcript has a caller
turn for it. "Over caller" is agent sound that starts before a clip has finished.
"""

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path

import httpx

from evals.audio_rig.webrtc import WebRtcPhone

from . import build
from .api import Retell
from .push import ensure_voice
from .rig_adapter import PROXY

RATE = 24000
FRAME = RATE // 50
AGENT_NAME = "Summit Air turn probe (Retell, test only)"
LLM_PROMPT = (
    "You are testing a phone line. Whatever the caller says, reply with one short sentence that "
    "repeats their answer in at most three words, then ask a new yes-or-no question about a "
    "furnace visit. Never say anything else."
)
BEGIN = "Quick question before we start. Would Thursday morning work for you?"
# Silence before a clip once the agent stops, and how long to wait for the answer (ASSUMED: a
# caller's usual pause, and longer than the slowest answer on live call 3, 4.1 s).
QUIET_S = 1.2
ANSWER_WAIT_S = 7.0
CLIPS = ["sure", "maybe", "idoubt", "thewhat", "isaidsure", "wondering"]

BASE = {**build.SPEECH, "reminder_trigger_ms": 30000, "reminder_max_count": 0}
VARIANTS = {
    "before": {"stt_mode": "fast", "responsiveness": 0.9, "enable_dynamic_responsiveness": True,
               "reminder_trigger_ms": 30000},
    "accurate": {"stt_mode": "accurate"},
    "no-denoise": {"denoising_mode": "no-denoise"},
    "accurate-no-denoise": {"stt_mode": "accurate", "denoising_mode": "no-denoise"},
    "speech-cancel": {"denoising_mode": "noise-and-background-speech-cancellation"},
    "resp-1": {"responsiveness": 1.0, "enable_dynamic_responsiveness": False,
               "enable_backchannel": False},
    "accurate-quiet": {"stt_mode": "accurate", "denoising_mode": "no-denoise",
                       "enable_backchannel": False},
    # What went live after Jackson's notes: accurate, responsiveness 1.0, backchannel kept on.
    "live": {},
    # The native agent's dials: the same, with Retell's Agent Handbook (voice/archive/retell_native).
    "native": {"handbook_config": {"conversational_personality": True, "natural_filler_words": True,
                                   "high_empathy": True, "echo_verification": True,
                                   "speech_normalization": True, "smart_matching": True,
                                   "ai_disclosure": True, "scope_boundaries": True}},
    # Which Handbook toggle splits a reply into two pieces with a pause between (Jackson's call 2).
    "native-no-conversational": {"handbook_config": {"conversational_personality": False,
                                 "natural_filler_words": True, "high_empathy": True,
                                 "echo_verification": True, "speech_normalization": True,
                                 "smart_matching": True, "ai_disclosure": True,
                                 "scope_boundaries": True}},
    "native-no-fillers": {"handbook_config": {"conversational_personality": True,
                          "natural_filler_words": False, "high_empathy": True,
                          "echo_verification": True, "speech_normalization": True,
                          "smart_matching": True, "ai_disclosure": True,
                          "scope_boundaries": True}},
    "accurate-quick": {"stt_mode": "accurate", "denoising_mode": "no-denoise",
                       "enable_backchannel": False, "responsiveness": 1.0,
                       "enable_dynamic_responsiveness": False},
}


def read_clip(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        if w.getframerate() != RATE or w.getnchannels() != 1:
            raise SystemExit(f"{path}: want mono {RATE} Hz")
        return w.readframes(w.getnframes())


class Ears:
    """Times of the agent's audio frames."""

    def __init__(self):
        self.sounds: list[float] = []
        self.ended = False

    def audio(self, pcm, rate, live=False):
        self.sounds.append(time.perf_counter())

    def done(self):
        pass

    def said(self, text):
        pass

    def hung_up(self):
        self.ended = True

    def last(self) -> float:
        return self.sounds[-1] if self.sounds else 0.0

    def first_after(self, t: float) -> float | None:
        return next((s for s in self.sounds if s > t), None)


class Mic:
    """A steady 20 ms stream to the call: silence, with a clip played on request."""

    def __init__(self, phone: WebRtcPhone):
        self.phone = phone
        self.queue: list[bytes] = []
        self.task = None

    async def run(self):
        silence = b"\0" * FRAME * 2
        start = time.perf_counter()
        n = 0
        while True:
            await self.phone.send(self.queue.pop(0) if self.queue else silence)
            n += 1
            await asyncio.sleep(max(0.0, start + n * 0.02 - time.perf_counter()))

    async def play(self, pcm: bytes) -> tuple[float, float]:
        frames = [pcm[i:i + FRAME * 2].ljust(FRAME * 2, b"\0") for i in range(0, len(pcm), FRAME * 2)]
        started = time.perf_counter()
        self.queue.extend(frames)
        while self.queue:
            await asyncio.sleep(0.01)
        return started, time.perf_counter()


async def one_call(retell: Retell, agent_id: str, clips: dict[str, bytes],
                   variables: dict | None = None) -> dict:
    """`variables` are the dynamic variables for an agent whose prompt needs them."""
    body = {"agent_id": agent_id}
    if variables:
        body["retell_llm_dynamic_variables"] = variables
    web = retell.request("POST", "/v3/create-web-call", body)
    call_id, token = web["call_id"], web["access_token"]
    session = {}

    async def offer(sdp):
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(PROXY.format(call_id=call_id),
                                headers={"Authorization": f"Bearer {token}"},
                                json={"identity": "client", "sdp": sdp})
        r.raise_for_status()
        session["id"] = r.json()["session_id"]
        return r.json()["sdp"]

    ears = Ears()
    phone = WebRtcPhone(RATE)
    await phone.connect(web.get("ice_servers") or [], offer, ears)
    mic = Mic(phone)
    mic.task = asyncio.create_task(mic.run())
    turns = []
    try:
        # The greeting.
        deadline = time.perf_counter() + 15
        while not ears.sounds and time.perf_counter() < deadline:
            await asyncio.sleep(0.05)
        for name, pcm in clips.items():
            while time.perf_counter() - ears.last() < QUIET_S:
                await asyncio.sleep(0.05)
            started, ended = await mic.play(pcm)
            over = next((s for s in ears.sounds if started < s < ended), None)
            await asyncio.sleep(ANSWER_WAIT_S)
            answer = ears.first_after(ended)
            turns.append({
                "clip": name,
                "answer_s": round(answer - ended, 2) if answer and answer - ended < ANSWER_WAIT_S else None,
                "over_caller_s": round(over - started, 2) if over else None,
            })
    finally:
        mic.task.cancel()
        await phone.close()
        if session.get("id"):
            async with httpx.AsyncClient(timeout=20) as http:
                await http.delete(f"{PROXY.format(call_id=call_id)}/{session['id']}",
                                  headers={"Authorization": f"Bearer {token}"})
    return {"call_id": call_id, "turns": turns}


def ensure_agent(retell: Retell, voice_id: str) -> str:
    listed = retell.request("POST", "/v2/list-agents", {})
    items = listed.get("items", []) if isinstance(listed, dict) else listed
    existing = next((a for a in items if a.get("agent_name") == AGENT_NAME), None)
    if existing:
        return existing["agent_id"]
    llm = retell.request("POST", "/create-retell-llm", {
        "model": "claude-5.5-haiku", "model_temperature": 0.3,
        "general_prompt": LLM_PROMPT, "begin_message": BEGIN,
    })
    agent = retell.request("POST", "/create-agent", {
        "agent_name": AGENT_NAME,
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": voice_id, **BASE,
    })
    return agent["agent_id"]


def transcript(retell: Retell, call_id: str) -> tuple[list[dict], float]:
    for _ in range(30):
        call = retell.request("GET", f"/v2/get-call/{call_id}")
        cost = (call.get("call_cost") or {}).get("combined_cost")
        if cost:
            return call.get("transcript_object") or [], cost / 100
        time.sleep(3)
    return call.get("transcript_object") or [], 0.0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clips", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--voice", default="3945925b-9143-4b7d-9ab5-cc4878001106",
                        help="Cartesia voice id (Max)")
    args = parser.parse_args()
    retell = Retell(os.environ["RETELL_API_KEY"])
    clips = {name: read_clip(args.clips / f"{name}.wav") for name in CLIPS}
    voice_id = ensure_voice(retell, args.voice, "Max")
    agent_id = ensure_agent(retell, voice_id)
    results = json.loads(args.out.read_text()) if args.out.exists() else []
    for p in range(args.passes):
        for name in args.variants.split(","):
            retell.request("PATCH", f"/update-agent/{agent_id}",
                           {**BASE, **VARIANTS[name], "voice_id": voice_id})
            call = await one_call(retell, agent_id, clips)
            call["transcript"], call["cost_usd"] = transcript(retell, call["call_id"])
            call.update(variant=name, attempt=p + 1)
            results.append(call)
            args.out.write_text(json.dumps(results, indent=1))
            heard = sum(t["answer_s"] is not None for t in call["turns"])
            print(f"{name} #{p + 1}: answered {heard}/{len(call['turns'])}, "
                  f"${call['cost_usd']:.2f}, {[t['answer_s'] for t in call['turns']]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
