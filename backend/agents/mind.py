"""The Mind: reads every event and decides what happens next, one step at a time.

Autism-mode escalation ladder (least intrusive first, per the person's choices):
  1. rising              -> vibrate (or whisper), notify_phone, notify_circle
  2. still climbing      -> adjust_home
  3. speech switched off -> speak_for_you when someone talks to them
  4. calm again          -> remember (save the episode)

Each step happens at most once per episode. Every decision is logged with its
reason and saved. The Mind calls the specialist agents; for now it calls Sense,
and the others act on its decisions as they are built.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..core import db, profile
from ..core.events import Event, bus
from .sense import sense

log = logging.getLogger(__name__)

Action = Literal[
    "vibrate", "notify_phone", "notify_circle", "adjust_home",
    "speak_for_you", "whisper", "remember", "exercise", "none",
]

AGENT_FOR: dict[str, str] = {
    "vibrate": "mind", "whisper": "mind", "notify_phone": "guardian",
    "notify_circle": "guardian", "adjust_home": "environment",
    "speak_for_you": "voice", "remember": "memory", "exercise": "coach", "none": "mind",
}

CLIMB_POINTS = 2       # score rise since the episode began that counts as "still climbing"
SPEECH_SILENCE_S = 10  # no words from the person this long while overloaded = speech off
CALM_READINGS = 3      # consecutive calm assessments that end an episode


class Decision(BaseModel):
    """{action, agent, level, reason, t}, plus optional details in data."""

    action: Action
    agent: str
    level: str
    reason: str
    t: float = Field(default_factory=time.time)
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class Episode:
    id: str
    start: float
    trigger: str
    start_score: int
    peak_level: str = "rising"
    peak_score: int = 0
    actions: list[str] = field(default_factory=list)
    calm_streak: int = 0
    overloaded_since: float | None = None
    last_own_speech: float | None = None
    speech_off_gesture: bool = False

    def speech_off(self, now: float) -> tuple[bool, str]:
        """Has the person's speech switched off? Returns (yes/no, why)."""
        if self.speech_off_gesture:
            return True, "you signalled that your speech is off"
        if self.overloaded_since is None:
            return False, ""
        quiet_since = max(self.overloaded_since, self.last_own_speech or 0)
        quiet = now - quiet_since
        if quiet >= SPEECH_SILENCE_S:
            return True, f"no words from you for {quiet:.0f}s while overloaded"
        return False, ""

    def summary(self, end: float | None = None) -> dict:
        return {
            "id": self.id, "start": self.start, "end": end, "trigger": self.trigger,
            "peak_level": self.peak_level, "peak_score": self.peak_score, "actions": self.actions,
        }


class Mind:
    def __init__(self) -> None:
        self.decisions: deque[Decision] = deque(maxlen=500)
        self.episode: Episode | None = None
        self._queues: set[asyncio.Queue[dict]] = set()

    # ---------- live feed for the apps ----------
    def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(200)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._queues.discard(queue)

    def status(self) -> dict:
        return {
            "kind": "state",
            "sense": sense.current,
            "episode": self.episode.summary() if self.episode else None,
        }

    def _broadcast(self, message: dict) -> None:
        for queue in self._queues:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("app feed queue full; dropping %s", message.get("kind"))

    # ---------- decisions ----------
    def decide(self, action: Action, level: str, reason: str, **data: Any) -> Decision:
        if self.episode is not None:
            data.setdefault("episode", self.episode.id)
            self.episode.actions.append(action)
        decision = Decision(action=action, agent=AGENT_FOR[action], level=level, reason=reason, data=data)
        self.decisions.append(decision)
        db.execute(
            "INSERT INTO decisions (action, agent, level, reason, data, t) VALUES (?, ?, ?, ?, ?, ?)",
            (decision.action, decision.agent, decision.level, decision.reason,
             json.dumps(decision.data), decision.t),
        )
        log.info("decision %s (%s): %s", action, level, reason)
        self._broadcast({"kind": "decision", "decision": decision.model_dump()})
        return decision

    def recent(self, limit: int = 50) -> list[Decision]:
        return list(reversed(self.decisions))[:limit]

    # ---------- the ladder ----------
    def _start_episode(self, level: str, score: int, why: str, trigger: str) -> Episode:
        ep = Episode(id=uuid.uuid4().hex[:8], start=time.time(), trigger=trigger,
                     start_score=score, peak_level=level, peak_score=score)
        self.episode = ep
        person = profile.get()
        first_touch = "vibrate" if person.mode == "autism" or person.comfort.haptic_only else "whisper"
        touch = "Gentle vibration first, no voice or sound." if first_touch == "vibrate" else "A calm word in the ear."
        self.decide(first_touch, level, f"{why} {touch}")
        self.decide("notify_phone", level, "First sign of overload: alerting your phone right away, as you asked.")
        circle = [m.name for m in person.circle if "alerts" in m.sees]
        if circle:
            self.decide("notify_circle", level,
                        f"Telling {', '.join(circle)} right away, as you asked.", to=circle)
        return ep

    def _end_episode(self) -> None:
        ep = self.episode
        assert ep is not None
        end = time.time()
        minutes = (end - ep.start) / 60
        self.decide("remember", "calm",
                    f"Calm again after {minutes:.1f} min (peak: {ep.peak_level}). Saving the episode.")
        db.execute(
            "INSERT OR REPLACE INTO episodes (id, start, end, peak_level, peak_score, trigger, actions) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ep.id, ep.start, end, ep.peak_level, ep.peak_score, ep.trigger, json.dumps(ep.actions)),
        )
        self.episode = None

    async def on_readings(self) -> None:
        self._ladder(await sense.assess())
        self._broadcast(self.status())

    def _ladder(self, result: dict) -> None:
        level, score, why = result["level"], result["rule"]["score"], result["reason"]
        now = time.time()
        ep = self.episode

        if ep is None:
            if level == "calm":
                return
            ep = self._start_episode(level, score, f"Load is {level}: {why}.", trigger="sense")

        if level == "calm":
            ep.calm_streak += 1
            if ep.calm_streak >= CALM_READINGS:
                self._end_episode()
            return
        ep.calm_streak = 0
        ep.peak_score = max(ep.peak_score, score)
        if level == "overloaded":
            ep.peak_level = "overloaded"
            ep.overloaded_since = ep.overloaded_since or now
        else:
            ep.overloaded_since = None

        climbing = score >= ep.start_score + CLIMB_POINTS
        if "adjust_home" not in ep.actions and (level == "overloaded" or climbing):
            because = "overloaded" if level == "overloaded" else f"still climbing (score {ep.start_score} → {score})"
            self.decide("adjust_home", level, f"Load is {because}: {why}. Lowering lights and sound at home.")

    def on_speech(self, event: Event) -> None:
        ep = self.episode
        if ep is None:
            return
        if event.data.get("is_me"):
            ep.last_own_speech = event.t
            ep.speech_off_gesture = False
            return
        if event.type != "speech_complete":  # wait for the whole question
            return
        off, why = ep.speech_off(event.t)
        if off:
            heard = event.data.get("text", "")
            speaker = event.data.get("speaker", "Someone")
            self.decide("speak_for_you", ep.peak_level,
                        f'{speaker} said: "{heard}". Speaking for you because {why}.',
                        heard=heard, speaker=speaker)

    def on_gesture(self, event: Event) -> None:
        kind = event.data.get("kind")
        if kind == "break" and self.episode is None:
            self._start_episode("rising", 0, "You asked for a break.", trigger="you")
        elif kind == "speech_off":
            if self.episode is None:
                self._start_episode("rising", 0, "You signalled your speech is off.", trigger="you")
            self.episode.speech_off_gesture = True
        elif kind == "speech_on" and self.episode is not None:
            self.episode.speech_off_gesture = False
        self._broadcast(self.status())

    # ---------- loop ----------
    async def handle(self, batch: list[Event]) -> None:
        """Handle events in order; a run of readings becomes one assessment."""
        readings = False
        for event in batch:
            if event.type == "reading":
                readings = True
            elif event.type in ("speech_heard", "speech_complete"):
                self.on_speech(event)
            elif event.type == "gesture":
                self.on_gesture(event)
        if readings:
            await self.on_readings()

    async def run(self) -> None:
        queue = bus.subscribe()
        try:
            while True:
                batch = [await queue.get()]
                while not queue.empty():
                    batch.append(queue.get_nowait())
                try:
                    await self.handle(batch)
                except Exception:
                    log.exception("mind step failed")
        finally:
            bus.unsubscribe(queue)


mind = Mind()
