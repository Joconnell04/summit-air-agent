"""The LiveKit worker that answers (646) 624-0418: Deepgram hears, Claude decides, Cartesia speaks,
and every rule is the tool server's (docs/architecture.md).

    uv run --env-file .env python -m voice.livekit.worker dev     # local, against make server
    uv run --env-file .env python -m voice.livekit.worker start   # production (deploy/summit-air)
"""

import asyncio
import json
import logging
from pathlib import Path

import yaml
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    BackgroundAudioPlayer,
    ConversationItemAddedEvent,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RunContext,
    StopResponse,
    UserInputTranscribedEvent,
    cli,
    inference,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.amd import AMD, AMDCategory

from prompts import assemble
from voice import names as local_names

from . import clips
from .brain import build_llm
from .bridge import Bridge
from .call import Call, SummitAgent
from .capacity import Lines
from .server import ToolServer
from .settings import Settings, fallback_tenant, load_voice
from .tools import ToolLog, build_tools
from .turns import LlmLog

log = logging.getLogger("voice.worker")

# Rust buffers this much audio after a clip's last frame is handed over (BackgroundAudioPlayer).
CLIP_TAIL_S = 0.4
VOICEMAIL_SECONDS = 60
SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP

settings = Settings()


def stt_options(voice: dict, names: local_names.Names) -> dict:
    """Deepgram's settings: the tenant's local names as keyterms, so they are written as spelled."""
    options = {"model": voice["stt"]["model"], "endpointing_ms": voice["stt"]["endpointing_ms"]}
    if names.keyterms():
        options["keyterm"] = names.keyterms()
    return options


def tts_options(voice: dict, names: local_names.Names) -> dict:
    """Cartesia's settings: the tenant's pronunciation dictionary, when it has one. Cartesia swaps
    the names inside its own service, so the text the server wrote is never changed."""
    options = {"model": voice["tts"]["model"], "voice": voice["tts"]["voice_id"]}
    if names.cartesia_dictionary_id:
        options["pronunciation_dict_id"] = names.cartesia_dictionary_id
    return options


def build_session(vad, voice: dict, names: local_names.Names) -> AgentSession:
    from livekit.plugins import cartesia, deepgram

    return AgentSession(
        stt=deepgram.STT(**stt_options(voice, names)),
        llm=build_llm(),
        tts=cartesia.TTS(**tts_options(voice, names)),
        vad=vad,
        turn_handling={
            "turn_detection": inference.TurnDetector(),
            "endpointing": voice["endpointing"]["normal"],
            "interruption": voice["interruption"],
            "preemptive_generation": {"enabled": True, "preemptive_tts": False},
        },
    )


def load_vad(voice: dict):
    from livekit.plugins import silero

    return silero.VAD.load(
        min_silence_duration=voice["vad"]["min_silence_s"],
        activation_threshold=voice["vad"]["activation"],
    )


def check_names(tenants_dir: Path) -> None:
    """Loads every tenant's local names, so a list Deepgram or Cartesia would refuse stops the
    worker when it starts rather than on a call."""
    for folder in sorted(tenants_dir.iterdir()):
        if (folder / "agent.yaml").exists():
            local_names.load(folder)


def prewarm(proc: JobProcess) -> None:
    check_names(settings.tenants_dir)
    proc.userdata["vad"] = load_vad(load_voice())


server = AgentServer()
server.setup_fnc = prewarm


def tenant_dir(start: dict | None) -> Path:
    """The call's tenant folder, or the fallback when the server couldn't say."""
    if start and start.get("tenant"):
        return settings.tenants_dir / start["tenant"]["slug"]
    return fallback_tenant(settings)


def voice_lines(folder: Path) -> dict:
    return yaml.safe_load((folder / "agent.yaml").read_text())["voice"]


async def play_clip(player: BackgroundAudioPlayer, path: str) -> None:
    await player.play(path).wait_for_playout()
    await asyncio.sleep(CLIP_TAIL_S)


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    call_id = ctx.room.name
    meta = json.loads(ctx.job.metadata or "{}")
    tools_server = ToolServer(settings.tool_server, call_id, settings.tool_timeout_s)
    voice = load_voice()
    lines = Lines(settings.lines_dir, settings.max_calls)
    # A call the system places carries the claim from /outbound/claim (voice/livekit/outbound.py).
    outbound = meta if meta.get("outbound_id") else None
    if not lines.take(call_id):
        if outbound:
            # The poller only claims below the cap, so this is a race: report it as not placed.
            await tools_server.outbound_done(outbound["outbound_id"], False, False)
            await tools_server.aclose()
            ctx.shutdown("at capacity")
            return
        await take_voicemail(ctx, tools_server, voice)
        return

    async def free_line() -> None:
        lines.free(call_id)

    ctx.add_shutdown_callback(free_line)

    if outbound:
        start = await tools_server.start(outbound["phone"], outbound["dialed_from"], "outbound")
    else:
        caller = await ctx.wait_for_participant(kind=SIP)
        start = await tools_server.start(
            caller.attributes.get("sip.phoneNumber", ""),
            caller.attributes.get("sip.trunkPhoneNumber", ""),
        )
    if not start["ok"]:
        log.warning("call %s: start refused (%s)", call_id, start["refused"])
        await tools_server.aclose()
        if outbound:
            ctx.shutdown("tool server unreachable")
            return
        await say_and_hang_up(ctx, voice, voice_lines(tenant_dir(None))["server_down"])
        return

    data = start["data"]
    folder = tenant_dir(data)
    spoken = voice_lines(folder)
    player = BackgroundAudioPlayer()
    session = build_session(
        ctx.proc.userdata.get("vad") or load_vad(voice), voice, local_names.load(folder)
    )
    bridge = Bridge(
        clips.bridge_clips(folder),
        play=lambda path: play_clip(player, path),
        silent=lambda: session.agent_state != "speaking" and session.user_state != "speaking",
        after_s=voice["bridge"]["after_s"],
        min_turns_between=voice["bridge"]["min_turns_between"],
    )
    tool_log = ToolLog()
    llm_log = LlmLog()
    call = Call(tools_server, bridge, tool_log, voice["endpointing"], llm_log)
    call.session = session
    ended = {"reason": "caller_hangup"}

    async def close() -> None:
        await call.flush()
        await tools_server.end(ended["reason"])
        await tools_server.aclose()

    ctx.add_shutdown_callback(close)

    def speak(text: str):
        return session.say(text, allow_interruptions=False)

    def hang_up(context: RunContext, last_line) -> None:
        """After this turn's speech: the worker's own last line if it said one, else the tenant's
        goodbye when the model said nothing, then the line drops."""

        async def run() -> None:
            await context.speech_handle.wait_for_playout()
            spoke = any(
                isinstance(item, ChatMessage) and item.role == "assistant" and item.text_content
                for item in context.speech_handle.chat_items
            )
            if last_line is not None:
                await last_line.wait_for_playout()
            elif not spoke:
                await speak(spoken["goodbye"]).wait_for_playout()
            ended["reason"] = "agent_end"
            await ctx.delete_room()

        call.hang_up_task = asyncio.create_task(run())

    def server_down() -> None:
        async def run() -> None:
            await speak(spoken["server_down"]).wait_for_playout()
            ended["reason"] = "error"
            await ctx.delete_room()

        call.hang_up_task = asyncio.create_task(run())

    call.server_down = server_down

    # The session closes when the caller hangs up; end the job then, so the call is closed on the
    # server at once rather than when LiveKit empties the room.
    session.on("close", lambda _ev: ctx.shutdown("session closed"))

    @session.on("metrics_collected")
    def on_metrics(ev: MetricsCollectedEvent) -> None:
        llm_log.record(ev.metrics)

    @session.on("conversation_item_added")
    def on_item(ev: ConversationItemAddedEvent) -> None:
        if isinstance(ev.item, ChatMessage) and ev.item.role == "assistant":
            call.agent_line(ev.item)

    specs = assemble.tool_specs(assemble.job_types(folder))
    agent = SummitAgent(
        call,
        assemble.instructions(data),
        build_tools(specs, tools_server, bridge, tool_log, hang_up, speak),
    )

    if outbound:
        await place_outbound(ctx, session, agent, player, outbound, spoken, tools_server)
        return

    await session.start(room=ctx.room, agent=agent)
    await player.start(room=ctx.room, agent_session=session)
    # A dropped safety call whose lines weren't said: they come first on this call too.
    if (data.get("safety") or {}).get("text"):
        session.say(data["safety"]["text"], allow_interruptions=False)
        return
    session.say(assemble.greeting(data, spoken))
    if start["say"]:
        session.say(start["say"])


async def place_outbound(
    ctx, session, agent, player, claim: dict, spoken: dict, tools_server: ToolServer
) -> None:
    """Journey 12. The server chose this call and checked its rules when it was claimed; one dial,
    a voicemail at most, and the result goes back to the server."""
    if not settings.outbound_trunk_id:
        log.warning("outbound call skipped: LIVEKIT_OUTBOUND_TRUNK_ID is not set")
        await tools_server.outbound_done(claim["outbound_id"], False, False)
        ctx.shutdown("no outbound trunk")
        return
    await session.start(room=ctx.room, agent=agent)
    async with AMD(session, llm=None, stt=None, participant_identity="callee") as detector:
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=settings.outbound_trunk_id,
                    sip_call_to=claim["phone"],
                    participant_identity="callee",
                    wait_until_answered=True,
                )
            )
        except api.TwirpError as exc:
            log.warning("outbound call not answered: %s", exc.message)
            await tools_server.outbound_done(claim["outbound_id"], False, False)
            ctx.shutdown("not answered")
            return
        result = await detector.execute()
    await player.start(room=ctx.room, agent_session=session)
    if result.category != AMDCategory.HUMAN:
        voicemail = result.category == AMDCategory.MACHINE_VM
        if voicemail:
            line = spoken["outbound_voicemail"].format(phone=claim["dialed_from"])
            await session.say(line, allow_interruptions=False).wait_for_playout()
        await tools_server.outbound_done(claim["outbound_id"], False, voicemail)
        await ctx.delete_room()
        return
    await tools_server.outbound_done(claim["outbound_id"], True, False)
    session.say(claim["opening_line"])


class VoicemailAgent(Agent):
    """Listens only: no model replies while a voicemail is left."""

    def __init__(self):
        super().__init__(instructions="")

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        raise StopResponse()


async def take_voicemail(ctx: JobContext, tools_server: ToolServer, voice: dict) -> None:
    """Every line is busy (spec S-47): a recorded message with the safety line first, then a
    voicemail that lands on the owner view as a flagged callback. No voice stream is used."""
    from livekit.plugins import deepgram

    caller = await ctx.wait_for_participant(kind=SIP)
    start = await tools_server.start(
        caller.attributes.get("sip.phoneNumber", ""),
        caller.attributes.get("sip.trunkPhoneNumber", ""),
    )
    heard: list[str] = []
    session = AgentSession(stt=deepgram.STT(model=voice["stt"]["model"]))

    @session.on("user_input_transcribed")
    def on_text(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final and ev.transcript.strip():
            heard.append(ev.transcript.strip())

    folder = tenant_dir(start["data"] if start["ok"] else None)
    minutes = voice_lines(folder)["voicemail_callback_minutes"]

    async def close() -> None:
        if start["ok"]:
            await leave_voicemail(tools_server, " ".join(heard), minutes)
            await tools_server.end("caller_hangup")
        await tools_server.aclose()

    ctx.add_shutdown_callback(close)
    await session.start(room=ctx.room, agent=VoicemailAgent())
    player = BackgroundAudioPlayer()
    await player.start(room=ctx.room, agent_session=session)
    await play_clip(player, str(clips.folder(folder) / "at_capacity.wav"))
    await asyncio.sleep(VOICEMAIL_SECONDS)
    await ctx.delete_room()


async def leave_voicemail(tools_server: ToolServer, text: str, callback_minutes: int) -> dict:
    """Records what the caller said and flags a person to call back."""
    words = text.strip() or "(no message left)"
    await tools_server.turn("caller", words, {})
    reason = f"Voicemail while every line was busy: {words}"[:300]
    args = {"reason": reason, "priority": "High", "callback_in_minutes": callback_minutes}
    return await tools_server.tool("flag_person", args, write=True)


async def say_and_hang_up(ctx: JobContext, voice: dict, line: str) -> None:
    """The tool server is unreachable: say so with the voice alone, then end the call."""
    from livekit.plugins import cartesia

    tts = voice["tts"]
    session = AgentSession(tts=cartesia.TTS(model=tts["model"], voice=tts["voice_id"]))
    await session.start(room=ctx.room, agent=Agent(instructions=""))
    await session.say(line, allow_interruptions=False).wait_for_playout()
    await ctx.delete_room()


if __name__ == "__main__":
    cli.run_app(server)
