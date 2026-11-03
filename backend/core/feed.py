"""Live feeds: fan out messages to subscribers (apps over websockets, other agents)."""

import asyncio
import contextlib
import logging
from collections.abc import Callable

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


class Feed:
    def __init__(self, name: str, maxsize: int = 200):
        self.name = name
        self._maxsize = maxsize
        self._queues: set[asyncio.Queue[dict]] = set()

    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(self._maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._queues.discard(queue)

    def publish(self, message: dict) -> None:
        for queue in self._queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("%s feed queue full; dropping %s", self.name, message.get("kind"))


async def stream(ws: WebSocket, feed: Feed, hello: Callable[[], dict]) -> None:
    """Send `hello()` then every message on `feed` until the client goes away."""
    await ws.accept()
    queue = feed.subscribe()

    async def push() -> None:
        await ws.send_json(hello())
        while True:
            await ws.send_json(await queue.get())

    async def listen() -> None:  # only here to notice the client going away
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(push()), asyncio.create_task(listen())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        feed.unsubscribe(queue)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
