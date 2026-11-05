"""The demo console's backend: one live socket for everything, and the store story.

/ws/console merges every live feed (the Mind's state and decisions, devices joining,
leaving and acting, each Meta model call, and the story's chapters) so the console can
draw Timber reaching into the world as it happens.

POST /api/demo/story plays the Autism-mode story with no clicks: Patrick leaves home
(his home devices drop out of range), walks into a busy store (the store's devices offer
themselves as tools), load climbs, his words switch off, the cashier asks a question and
Timber answers, the room calms, and he's home again, where he asks where his keys are
and SAM 3.1 finds them in a photo. The body signals, devices and photo are simulated (the
photo was made with Muse Image); everything Timber does in response is the real system,
on real Meta models.
"""

import asyncio
import base64
import contextlib
import logging
import random
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .agents.find import find
from .agents.mind import mind
from .core import modes
from .core.events import Event, bus
from .core.feed import Feed
from .core.db import DATA_DIR
from .core.state import BASELINE_READINGS, state
from .devices.gateway import Reading, ingest_one
from .devices.home import KIND_ACTIONS, Device, home
from .meta import client
from .meta import log as model_log

log = logging.getLogger(__name__)

router = APIRouter(tags=["demo"])
story_feed = Feed("story")

STORE_DEVICES = [  # what a store near Patrick announces (simulated, clearly labelled)
    ("store_quiet_light", "Quiet-zone light", "light", {"on": True, "brightness": 100}),
    ("store_music", "Store music", "speaker", {"playing": True, "volume": 70}),
]
CASHIER = "Do you need a bag?"
KEYS_PHOTO = DATA_DIR / "demo" / "keys_on_table.png"

# chapter, caption, heart rate, noise dB, movement, seconds
CHAPTERS = [
    ("home", "Patrick is at home. Calm. Timber is quietly listening.", 72, 48, 0.1, 6),
    ("leave", "He heads out. His home's lamp, speaker and thermostat drop out of range.", 74, 52, 0.3, 4),
    ("store", "He walks into a busy store. The store's light and music offer themselves to Timber as tools.",
     74, 53, 0.3, 5),
    ("rising", "It's getting loud. Timber acts first: a gentle vibration, his trusted person is told, "
     "and it dims the store's light and pauses the music.", 92, 77, 0.35, 9),
    ("overloaded", "Overloaded. His words switch off. Timber stays with him.", 116, 88, 0.6, 12),
    ("cashier", f'The cashier asks: "{CASHIER}" Timber answers for him, from his own words.', 114, 86, 0.6, 9),
    ("calm", "The room is calmer. He settles. Timber puts the store back and saves the moment.", 76, 54, 0.15, 10),
    ("back", "Back home. The store's tools are gone, and his own are back.", 72, 48, 0.1, 4),
    ("keys", '"Where are my keys?" He takes a photo. SAM 3.1 finds them, and Muse Spark says where.',
     72, 48, 0.1, 9),
]


class Story:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.chapter: dict | None = None

    @property
    def running(self) -> bool:
        return bool(self.task and not self.task.done())

    def start(self) -> bool:
        if self.running:
            return False
        self.task = asyncio.create_task(self.play())
        self.task.add_done_callback(self._done)
        return True

    def stop(self) -> None:
        if self.running:
            self.task.cancel()

    def _done(self, task: asyncio.Task) -> None:
        self.chapter = None
        story_feed.publish({"kind": "story", "chapter": None, "caption": None, "t": time.time()})
        if not task.cancelled() and task.exception():
            log.error("story failed", exc_info=task.exception())

    def say(self, chapter: str, caption: str, index: int) -> None:
        self.chapter = {"kind": "story", "chapter": chapter, "caption": caption, "index": index,
                        "of": len(CHAPTERS), "t": time.time()}
        story_feed.publish(self.chapter)

    @staticmethod
    def reading(hr: float, noise: float, move: float) -> None:
        for device, metric, value in (("story watch", "heart_rate", round(hr + random.uniform(-2, 2))),
                                      ("story mic", "noise_db", round(noise + random.uniform(-2, 2), 1)),
                                      ("story phone", "movement", move)):
            ingest_one(Reading(device=device, metric=metric, value=value))

    async def play(self) -> None:
        hr = state.baselines.get("heart_rate")
        if not hr or not hr.ready:  # a calm baseline first, quickly and quietly
            for _ in range(BASELINE_READINGS):
                self.reading(72, 48, 0.1)
                await asyncio.sleep(0.02)
        if mind.episode is not None:  # start from calm
            for _ in range(6):
                self.reading(72, 48, 0.1)
                await asyncio.sleep(0.5)

        for index, (chapter, caption, hr, noise, move, seconds) in enumerate(CHAPTERS):
            self.say(chapter, caption, index)
            if chapter == "leave":
                for device in [d for d in home.present() if not d.ttl_s]:
                    home.remove(device.id)
            elif chapter == "store":
                for device_id, name, kind, device_state in STORE_DEVICES:
                    home.register(Device(id=device_id, name=name, kind=kind, room="store", ttl_s=600,
                                         actions=KIND_ACTIONS[kind], state=dict(device_state)))
            elif chapter == "cashier":
                bus.publish(Event(type="speech_complete", source="story", data={
                    "text": CASHIER, "speaker": "Cashier", "is_me": False, "label": "B"}))
            elif chapter == "back":
                for device_id, *_ in STORE_DEVICES:
                    home.remove(device_id)
                home.seed()
            elif chapter == "keys" and KEYS_PHOTO.exists():
                photo = "data:image/png;base64," + base64.b64encode(KEYS_PHOTO.read_bytes()).decode()
                asyncio.create_task(find("Where are my keys?", photo))

            end = time.time() + seconds
            while time.time() < end:
                self.reading(hr, noise, move)
                await asyncio.sleep(1)
            if chapter == "calm":  # wait until the Mind has closed the episode (and put the store back)
                for _ in range(20):
                    if mind.episode is None:
                        break
                    self.reading(hr, noise, move)
                    await asyncio.sleep(1)
                await asyncio.sleep(1.5)
        await asyncio.sleep(2)


story = Story()


@router.post("/api/demo/story")
async def play_story() -> dict:
    """Play the store story (about a minute). Runs once at a time."""
    return {"started": story.start(), "chapters": len(CHAPTERS)}


@router.post("/api/demo/stop")
async def stop_story() -> dict:
    story.stop()
    return {"stopped": True}


def hello() -> dict:
    return {
        "kind": "hello", "live": client.live(), "state": mind.status(), "mode": modes.current().public(),
        "devices": [d.model_dump() for d in home.present()], "home_log": list(reversed(home.log))[:20],
        "decisions": [d.model_dump() for d in reversed(mind.recent(15))],
        "models": list(reversed(model_log.recent(30))), "model_summary": model_log.summary(),
        "story": story.chapter,
    }


@router.websocket("/ws/console")
async def console(ws: WebSocket) -> None:
    """Every live feed, merged, for the demo console."""
    await ws.accept()
    feeds = [mind.feed, home.feed, model_log.feed, story_feed]
    merged: asyncio.Queue[dict] = asyncio.Queue(500)
    subscriptions = [(feed, feed.subscribe()) for feed in feeds]

    async def pump(queue: asyncio.Queue) -> None:
        while True:
            message = await queue.get()
            with contextlib.suppress(asyncio.QueueFull):
                merged.put_nowait(message)

    async def push() -> None:
        await ws.send_json(hello())
        while True:
            await ws.send_json(await merged.get())

    async def listen() -> None:  # only here to notice the client going away
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(pump(q)) for _, q in subscriptions]
    tasks += [asyncio.create_task(push()), asyncio.create_task(listen())]
    try:
        await asyncio.wait(tasks[-2:], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for feed, queue in subscriptions:
            feed.unsubscribe(queue)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
