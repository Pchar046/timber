"""Event bus: every input (watch, mic, phone, routine, timer) becomes an Event.

Listeners run synchronously on publish (state, storage). Async consumers such as
the Mind or a websocket call `subscribe()` and read from their own queue.
`publish` must be called from the event-loop thread.
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

EventType = Literal[
    "reading", "speech_heard", "speech_complete", "speech_paused", "gesture", "routine_due", "photo", "tick"
]

# What the Mind can decide (the Decision contract in ARCHITECTURE.md).
Action = Literal[
    "vibrate", "notify_phone", "notify_circle", "adjust_home",
    "speak_for_you", "whisper", "remember", "exercise", "none",
]


class Event(BaseModel):
    """{type, source, data, t}. A `reading` carries signals in data, e.g. {"heart_rate": 92}."""

    type: EventType
    source: str
    data: dict[str, Any] = Field(default_factory=dict)
    t: float = Field(default_factory=time.time)


Listener = Callable[[Event], None]


class EventBus:
    def __init__(self, history: int = 500):
        self._history: deque[Event] = deque(maxlen=history)
        self._listeners: list[Listener] = []
        self._queues: set[asyncio.Queue[Event]] = set()

    def on(self, listener: Listener) -> Listener:
        if listener not in self._listeners:  # safe across app restarts in one process
            self._listeners.append(listener)
        return listener

    def publish(self, event: Event) -> Event:
        self._history.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # one bad listener must not stop the others
                log.exception("event listener %s failed", getattr(listener, "__name__", listener))
        for queue in self._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("subscriber queue full; dropping %s event", event.type)
        return event

    def subscribe(self, maxsize: int = 1000) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._queues.discard(queue)

    def recent(self, limit: int = 50, type: str | None = None) -> list[Event]:
        """Newest first."""
        events = (e for e in reversed(self._history) if type is None or e.type == type)
        return [e for _, e in zip(range(limit), events)]


bus = EventBus()
