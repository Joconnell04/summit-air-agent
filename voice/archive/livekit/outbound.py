"""The outbound poller (journey 12): asks the tool server for the next call the system places and
hands each one to a worker through a LiveKit dispatch. The server decides who is called and when
(spec S-42, rechecked at claim time); this only moves the claim to a worker. Runs beside the worker:

    uv run --env-file .env python -m voice.archive.livekit.outbound
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from livekit import api

from .capacity import Lines
from .server import ToolServer
from .settings import Settings

log = logging.getLogger("voice.outbound")


async def poll_once(
    server: ToolServer,
    lines: Lines,
    worker: str,
    dispatch: Callable[[str, str], Awaitable[None]],
) -> dict | None:
    """Claim one call if a line is free, and dispatch a worker into a room named by its call id."""
    if lines.in_use() >= lines.cap:
        return None
    claim = await server.claim(worker)
    if claim:
        await dispatch(claim["call_id"], json.dumps(claim))
    return claim


async def main() -> None:
    settings = Settings()
    lines = Lines(settings.lines_dir, settings.max_calls)
    server = ToolServer(settings.tool_server, "", settings.tool_timeout_s)
    lk = api.LiveKitAPI()

    async def dispatch(room: str, metadata: str) -> None:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=settings.agent_name, room=room, metadata=metadata
            )
        )

    try:
        while True:
            try:
                claim = await poll_once(server, lines, settings.agent_name, dispatch)
                if claim:
                    log.info("dispatched outbound call %s", claim["call_id"])
            except api.TwirpError as exc:
                log.warning("dispatch failed: %s", exc.message)
            await asyncio.sleep(settings.claim_every_s)
    finally:
        await server.aclose()
        await lk.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
