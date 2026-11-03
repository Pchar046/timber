"""The Ears: live listening relay.

The browser streams 24 kHz 16-bit mono PCM to /ws/listen as binary frames. The
backend relays it to Muse Voice Transcribe (DIARIZATION), so the API key never
leaves the server, and turns what comes back into speech events on the bus:
  speech_heard     the growing transcript of the current turn (at most once a second)
  speech_complete  a finished turn, with its speaker label and whether it was the person
  speech_paused    the person stopped mid-sentence (about 2 s of silence, or a turn that
                   ended on a word like "the"), so Timber can offer the missing word
The browser can tag which label is the person by sending {"type": "me", "label": "A"}.
Labels are per session (A is whoever spoke first), so the tag is per session too.
"""

import asyncio
import contextlib
import json
import logging
import re
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.events import Event, bus
from ..meta.transcribe import TranscribeSession

log = logging.getLogger(__name__)

router = APIRouter(tags=["devices"])

PARTIAL_EVERY_S = 1.0
PAUSE_S = 2.0
# A sentence that ends on one of these words is waiting for more ("can you pass the").
OPEN_ENDINGS = frozenset("""
    the a an my your his her their our some any
    to of for with from at by into and but or because than
    need want pass give um uh er
""".split())


def unfinished(text: str) -> bool:
    """Does this sentence stop before it's done?"""
    if re.search(r"(\.\.\.|…|-|,)\s*$", text):
        return True
    words = re.findall(r"[a-z']+", text.lower())
    return bool(words) and words[-1] in OPEN_ENDINGS


class Listener:
    """One browser's listening session."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.me: str | None = None     # which diarization label is the person
        self.turn = 0
        self.label: str | None = None  # label of the turn that is ending
        self.turns: dict[int, str] = {}  # turn -> label, so a late "me" tag can be shown
        self._last_partial = 0.0
        self._text = ""                 # the current turn so far
        self._pause: asyncio.Task | None = None
        self._paused: set[int] = set()  # turns already reported as paused (once each)

    async def send(self, message: dict) -> None:
        with contextlib.suppress(Exception):  # the browser may already be gone
            await self.ws.send_json(message)

    def is_me(self, label: str | None) -> bool | None:
        """None while the person hasn't said which label is them."""
        return None if self.me is None or label is None else label == self.me

    # ---------- pauses ----------
    def _cancel_pause(self) -> None:
        if self._pause:
            self._pause.cancel()
            self._pause = None

    def paused(self, text: str, turn: int, label: str | None, demo: bool) -> None:
        """Report a mid-sentence stop, only when it may be the person speaking.

        Mid-turn the speaker label isn't known yet, so that only counts while nobody
        has been tagged as the person (e.g. testing alone).
        """
        mine = self.is_me(label)
        if turn in self._paused or mine is False or (mine is None and self.me is not None):
            return
        self._paused.add(turn)
        bus.publish(Event(type="speech_paused", source="mic",
                          data={"text": text, "turn": turn, "label": label, "is_me": mine, "demo": demo}))

    async def _wait_for_more(self, text: str, turn: int, demo: bool) -> None:
        await asyncio.sleep(PAUSE_S)
        if self.turn == turn and self._text == text:  # nothing new since: they stopped
            self.paused(text, turn, self.label, demo)

    def close(self) -> None:
        self._cancel_pause()

    # ---------- Transcribe events ----------
    async def on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "speechStart":
            self._cancel_pause()
            self.turn = event.get("turnId", self.turn + 1)
            self.label = None
            self._text = ""
        elif kind == "transcript":
            text = (event.get("transcript") or "").strip()
            if not text:
                return
            self._text = text
            self._cancel_pause()
            self._pause = asyncio.create_task(self._wait_for_more(text, self.turn, bool(event.get("demo"))))
            await self.send({"kind": "partial", "turn": self.turn, "text": text})
            now = time.time()
            if now - self._last_partial >= PARTIAL_EVERY_S:
                self._last_partial = now
                bus.publish(Event(type="speech_heard", source="mic",
                                  data={"text": text, "turn": self.turn, "label": None, "is_me": None,
                                        "demo": bool(event.get("demo"))}))
        elif kind == "speaker":
            self.label = event.get("label")
        elif kind == "speechComplete":
            self._cancel_pause()
            text = (event.get("transcript") or "").strip()
            turn = event.get("turnId", self.turn)
            if not text:
                return
            self.turns[turn] = self.label or "?"
            data = {"text": text, "turn": turn, "label": self.label,
                    "speaker": "You" if self.is_me(self.label) else f"Speaker {self.label or '?'}",
                    "is_me": self.is_me(self.label), "demo": bool(event.get("demo"))}
            bus.publish(Event(type="speech_complete", source="mic", data=data))
            await self.send({"kind": "turn", **data})
            if unfinished(text):  # the turn ended mid-sentence: same as a pause
                self.paused(text, turn, self.label, data["demo"])

    async def command(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("type") == "me":
            self.me = msg.get("label") or None
            await self.send({"kind": "me", "label": self.me})


@router.websocket("/ws/listen")
async def listen(ws: WebSocket) -> None:
    await ws.accept()
    listener = Listener(ws)
    async with TranscribeSession(listener.on_event, task="live listening") as session:
        await listener.send({"kind": "status", "status": session.status})
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await session.send_audio(msg["bytes"])
                elif msg.get("text"):
                    await listener.command(msg["text"])
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("listening session failed")
        finally:
            listener.close()
