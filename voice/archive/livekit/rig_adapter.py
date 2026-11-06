"""Our LiveKit stack on the simulated audio rig (evals/audio_rig, DECISIONS.md 026), as the
calibration entrant. Loaded by name so shared code never imports this folder.

The real worker runs, unchanged, as a local process under its own agent name, so the production
worker never picks up a rig room. Its tool server is the rig's router, which sends each call to
that scenario's scratch world. Each call is a room named by the call id; the rig joins it as a SIP
participant carrying the caller's number, as a phone call through the trunk would, and the worker
is dispatched to it.

`RIG_VOICE` (JSON, merged into the `voice` section of config/models.yaml) runs the worker on other
turn-taking values from a temporary copy of the file, labelled by `RIG_LABEL` in the run name, so
a setting can be measured before it becomes the default.
"""

import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from evals.audio_rig.room import RoomPhone

from .settings import ROOT

RATE = 24000
DIALED = "(646) 624-0418"
READY = "registered worker"
JOIN_TIMEOUT_S = 30


def caller_token(room: str, caller_id: str, key: str, secret: str) -> str:
    """A token that joins as the phone line would: kind SIP, with the numbers the worker reads."""
    from livekit import api

    return (
        api.AccessToken(key, secret)
        .with_identity(f"sip-{secrets.token_hex(4)}")
        .with_kind("sip")
        .with_attributes({"sip.phoneNumber": caller_id, "sip.trunkPhoneNumber": DIALED})
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )


def merged(base: dict, changes: dict) -> dict:
    """`base` with `changes` laid over it, key by key at every level."""
    out = dict(base)
    for key, value in changes.items():
        out[key] = merged(base.get(key) or {}, value) if isinstance(value, dict) else value
    return out


def trial_config(changes: dict, folder: Path) -> Path:
    """A copy of config/models.yaml with `changes` merged into its `voice` section."""
    config = yaml.safe_load((ROOT / "config/models.yaml").read_text())
    config["voice"] = merged(config["voice"], changes)
    path = folder / "models.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def token_counts(trace: dict) -> dict[str, int]:
    """The Claude tokens the worker logged with its agent lines (voice/archive/livekit/turns.py LlmLog):
    the first request of each turn only."""
    totals = {"prompt": 0, "read": 0, "write": 0, "out": 0}
    for e in trace["events"]:
        timings = e.get("timings") or {}
        if e["type"] == "turn" and e.get("speaker") == "agent":
            totals["prompt"] += int(timings.get("prompt_tokens", 0) or 0)
            totals["read"] += int(timings.get("cache_read_tokens", 0) or 0)
            totals["write"] += int(timings.get("cache_write_tokens", 0) or 0)
            totals["out"] += int(timings.get("llm_output_tokens", 0) or 0)
    return totals


class Line:
    def __init__(self, entrant: "Entrant"):
        self.entrant = entrant
        self.phone = RoomPhone(RATE)
        self.call_id = ""
        self.server = None
        self.opened = 0.0

    async def open(self, call_id, caller_id, world, listener):
        from livekit import api

        self.call_id, self.server = call_id, world.server
        env = self.entrant.env
        lk = api.LiveKitAPI(env["LIVEKIT_URL"], env["LIVEKIT_API_KEY"], env["LIVEKIT_API_SECRET"])
        try:
            await lk.room.create_room(api.CreateRoomRequest(name=call_id))
            await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                agent_name=self.entrant.agent_name, room=call_id, metadata=json.dumps({})))
        finally:
            await lk.aclose()
        token = caller_token(call_id, caller_id, env["LIVEKIT_API_KEY"], env["LIVEKIT_API_SECRET"])
        await self.phone.connect(env["LIVEKIT_URL"], token, listener)
        # The rig times the greeting from here, so wait for the worker to join the room (a cold
        # worker took more than the greeting wait once, MEASURED).
        deadline = time.monotonic() + JOIN_TIMEOUT_S
        while not self.phone.room.remote_participants and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        self.opened = time.perf_counter()
        return world.server

    def queued_s(self) -> float:
        return self.phone.queued_s()

    async def send(self, pcm: bytes) -> None:
        await self.phone.send(pcm)

    async def close(self, reason: str) -> None:
        from livekit import api

        await self.phone.close()
        env = self.entrant.env
        lk = api.LiveKitAPI(env["LIVEKIT_URL"], env["LIVEKIT_API_KEY"], env["LIVEKIT_API_SECRET"])
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=self.call_id))
        except api.TwirpError:
            pass  # the worker deleted it when it hung up
        finally:
            await lk.aclose()

    async def cost(self) -> dict[str, float]:
        """Claude tokens from the call record (MEASURED, first request per turn), speech minutes
        and characters at the rig's per-unit prices (config/models.yaml `audio_rig.livekit`)."""
        trace = await asyncio.to_thread(self.server.final_trace, self.call_id)
        price = self.entrant.brain["price"]
        t = token_counts(trace)
        fresh = max(t["prompt"] - t["read"] - t["write"], 0)
        model = (fresh + 1.25 * t["write"] + 0.1 * t["read"]) * price["input"] + t["out"] * price[
            "output"]
        minutes = (time.perf_counter() - self.opened) / 60
        chars = sum(len(e.get("text", "")) for e in trace["events"]
                    if e["type"] == "turn" and e.get("speaker") == "agent")
        speech = self.entrant.cfg
        return {"brain_model": model / 1e6, "platform": minutes * speech["stt_per_minute"]
                + chars / 1000 * speech["tts_per_1k_chars"]}


class Entrant:
    name = "livekit-rig"
    profile = "fast"
    in_rate = RATE
    local_world = True
    final_record = True
    signals_done = True

    def __init__(self, ctx):
        self.ctx = ctx
        self.env = ctx.env
        self.cfg = ctx.config["audio_rig"]["livekit"]
        self.brain = ctx.config["profiles"][self.profile]
        self.model = self.brain["model"]
        missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
                   if not self.env.get(k)]
        if missing:
            raise RuntimeError(f"LiveKit rig runs need {', '.join(missing)}")
        self.agent_name = f"summit-air-rig-{secrets.token_hex(3)}"
        self.voice_changes = json.loads(self.env.get("RIG_VOICE") or "{}")
        if self.voice_changes:
            self.name = f"livekit-rig-{self.env.get('RIG_LABEL') or 'trial'}"
        self.worker = None
        self.log_path = Path(tempfile.mkstemp(prefix="rig-worker-", suffix=".log")[1])

    def _spawn(self) -> None:
        env = {**os.environ, **self.env, "TOOL_SERVER_URL": self.ctx.router_url,
               "AGENT_NAME": self.agent_name, "LINES_DIR": tempfile.mkdtemp(prefix="rig-lines-"),
               "LIVEKIT_OUTBOUND_TRUNK_ID": ""}
        if self.voice_changes:
            folder = Path(tempfile.mkdtemp(prefix="rig-config-"))
            env["MODELS_CONFIG"] = str(trial_config(self.voice_changes, folder))
        log = self.log_path.open("w")
        self.worker = subprocess.Popen(
            [sys.executable, "-m", "voice.archive.livekit.worker", "start"],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

    async def start(self) -> None:
        await asyncio.to_thread(self._spawn)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.worker.poll() is not None:
                raise RuntimeError(f"the LiveKit worker exited; log at {self.log_path}")
            if READY in self.log_path.read_text():
                print(f"LiveKit worker ready as {self.agent_name}", flush=True)
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(f"the LiveKit worker did not register in 90 s; log at {self.log_path}")

    async def stop(self) -> None:
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.worker.kill()

    def line(self) -> Line:
        return Line(self)


def entrant(ctx) -> Entrant:
    return Entrant(ctx)
