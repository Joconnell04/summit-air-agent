"""A test call over the real phone line: a simulated caller (the eval suite's `offline.caller`
profile) dials the agent's number through the LiveKit outbound trunk, so the call goes out to the
phone network and back in through Twilio and the inbound trunk, like any caller's.

    uv run --env-file .env python -m voice.livekit.test_call [--to +16466240418] [--minutes 4]

It places one real call (a few cents). The agent's side is logged by the tool server as usual;
read it with `make trace CALL=<room>` on the server. The caller is an AI, not a person, so this
checks the wiring and the flow, not how a person finds the call.
"""

import argparse
import asyncio
import os
import uuid

import yaml
from livekit import api, rtc
from livekit.agents import Agent, AgentSession, utils

from .settings import ROOT, load_voice

PERSONA = """You are Pat Rivera, calling a home heating company on the phone. Speak like a real
caller: short, plain sentences, one at a time, and only answer what you're asked.

- Your gas furnace is due for its yearly tune-up. It works fine; nothing is wrong. You've had it
  about eight years. You haven't tried anything.
- You own the house: {address}. You're the only one home and you approve the work. No pets, no gate
  code.
- For a contact number, say "the number I'm calling from".
- When they read back your address and number, say yes if it's right.
- Take the first day and arrival window they offer.
- When they ask if there's anything else, say "No, that's it, thanks. Bye." and then stay quiet.
- Never say you're an AI or a test. Never make up a problem."""


class Caller(Agent):
    def __init__(self, address: str):
        super().__init__(instructions=PERSONA.format(address=address))


def address_in_area(tenant: str) -> str:
    """A made-up street in the first ZIP of the tenant's service area."""
    zones = yaml.safe_load((ROOT / "tenants" / tenant / "config.yaml").read_text())["zones"]
    return f"142 Main Street, {zones[0]['zips'][0]}"


def caller_llm():
    from livekit.plugins import anthropic

    profile = yaml.safe_load((ROOT / "config/models.yaml").read_text())["offline"]["caller"]
    return anthropic.LLM(model=profile["model"])


async def main(to: str, minutes: float, tenant: str) -> None:
    from livekit.plugins import cartesia, deepgram, silero

    voice = load_voice()
    room_name = f"testcall-{uuid.uuid4().hex[:8]}"
    lk = api.LiveKitAPI()
    token = (
        api.AccessToken()
        .with_identity("test-caller")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    async with utils.http_context.open():
        session = AgentSession(
            stt=deepgram.STT(model=voice["stt"]["model"]),
            llm=caller_llm(),
            tts=cartesia.TTS(model=voice["tts"]["model"]),
            vad=silero.VAD.load(),
            turn_handling={"turn_detection": "vad"},
        )
        await session.start(room=room, agent=Caller(address_in_area(tenant)))

        @session.on("conversation_item_added")
        def show(ev) -> None:
            text = getattr(ev.item, "text_content", None)
            if text:
                who = "caller" if ev.item.role == "assistant" else "agent "
                print(f"{who}: {text}", flush=True)

        print(f"dialing {to} from room {room_name}", flush=True)
        await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=os.environ["LIVEKIT_OUTBOUND_TRUNK_ID"],
                sip_call_to=to,
                participant_identity="summit-air-line",
                wait_until_answered=True,
            )
        )
        hung_up = asyncio.Event()
        room.on("participant_disconnected", lambda _p: hung_up.set())
        try:
            await asyncio.wait_for(hung_up.wait(), timeout=minutes * 60)
            print("the agent hung up", flush=True)
        except TimeoutError:
            print("time limit reached", flush=True)
        await session.aclose()
    await room.disconnect()
    await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    await lk.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", default=os.environ.get("TWILIO_PHONE_NUMBER", ""))
    parser.add_argument("--minutes", type=float, default=4)
    parser.add_argument("--tenant", default=os.environ.get("DEFAULT_TENANT", "summit-air"))
    args = parser.parse_args()
    asyncio.run(main(args.to, args.minutes, args.tenant))
