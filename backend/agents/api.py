"""HTTP and websocket routes for the agents."""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .mind import mind
from .sense import sense

router = APIRouter(prefix="/api", tags=["agents"])
ws_router = APIRouter(tags=["agents"])


@router.get("/sense")
def get_sense(limit: int = 50) -> dict:
    """Current load level, plus recent level changes (newest first)."""
    return {"current": sense.current, "changes": list(reversed(sense.changes))[:limit]}


@router.get("/decisions")
def get_decisions(limit: int = 50) -> dict:
    """The Mind's decisions, newest first, plus the episode in progress (if any)."""
    episode = mind.episode.summary() if mind.episode else None
    return {"episode": episode, "decisions": mind.recent(limit)}


@ws_router.websocket("/ws/person")
async def person_feed(ws: WebSocket) -> None:
    """Live feed for the person's phone: state changes and the Mind's decisions."""
    await ws.accept()
    queue = mind.subscribe()

    async def push() -> None:
        await ws.send_json(mind.status())
        while True:
            await ws.send_json(await queue.get())

    async def listen() -> None:  # only here to notice the phone going away
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(push()), asyncio.create_task(listen())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        mind.unsubscribe(queue)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
