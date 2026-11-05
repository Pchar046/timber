"""The Mind: reads every event and decides what happens next, one step at a time.

Escalation ladder (least intrusive first, per the person's choices):
  1. rising              -> first touch (Autism mode: vibrate; Dementia mode: a calm
                            whisper), notify_phone, notify_circle
  2. still climbing      -> adjust_home
  3. speech switched off -> speak_for_you when someone talks to them (Autism mode only)
  4. calm again          -> remember (save the episode)

Each step happens at most once per episode. Before any action, the Mind checks
the Calm-Self Contract; a forbidden action is logged as skipped, citing the
rule. Every decision is logged with its reason and saved. The Mind calls the specialist agents; for now it calls Sense,
and the others act on its decisions as they are built.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..core import contract, db, echo, modes, profile
from ..core.events import Action, Event, bus
from ..core.feed import Feed
from ..core.state import state
from ..devices.home import CALM_QUERY, home
from .sense import sense

log = logging.getLogger(__name__)

AGENT_FOR: dict[str, str] = {
    "vibrate": "mind", "whisper": "mind", "notify_phone": "guardian",
    "notify_circle": "guardian", "adjust_home": "environment",
    "speak_for_you": "voice", "remember": "memory", "exercise": "coach", "none": "mind",
}

CLIMB_POINTS = 2       # score rise since the episode began that counts as "still climbing"
SPEECH_SILENCE_S = 10  # no words from the person this long while overloaded = speech off
CALM_READINGS = 3      # consecutive calm assessments that end an episode
REOPEN_S = 60          # just after calming down, only the readings themselves can start a new episode


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
    tried: set[str] = field(default_factory=set)     # ladder steps considered (done or skipped)
    notified: set[str] = field(default_factory=set)  # care-circle names already told
    peaks: dict[str, float] = field(default_factory=dict)  # highest reading per signal
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
            "peaks": self.peaks,
        }


class Mind:
    def __init__(self) -> None:
        self.decisions: deque[Decision] = deque(maxlen=500)
        self.episode: Episode | None = None
        self._ended_t = 0.0
        self.feed = Feed("mind")  # state + decisions, for the apps and the Guardian
        sense.on_refresh = self.on_sense_refresh

    def status(self) -> dict:
        return {
            "kind": "state",
            "sense": sense.current,
            "episode": self.episode.summary() if self.episode else None,
            "mode": modes.current().public(),
        }

    # ---------- decisions ----------
    def decide(self, action: Action, level: str, reason: str, **data: Any) -> Decision:
        """Record one decision, after checking the contract (which stands in for consent)."""
        if self.episode is not None:
            self.episode.tried.add(action)
            if "blocked" in data:  # a skipped step counts as tried, so it isn't retried every reading
                self.episode.tried.add(data["blocked"])
        if action not in ("remember", "none"):
            verdict = contract.check(action, level, data.get("text"), data.get("to"))
            if not verdict.allowed:
                return self.decide("none", level, f"Skipped {action}: {verdict.reason}",
                                   blocked=action, rules=verdict.rules)
            if verdict.rules:
                reason = f"{reason} (Contract: {verdict.reason})"
                data["rules"] = verdict.rules
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
        self.feed.publish({"kind": "decision", "decision": decision.model_dump()})
        return decision

    def recent(self, limit: int = 50) -> list[Decision]:
        return list(reversed(self.decisions))[:limit]

    # ---------- the ladder ----------
    def _start_episode(self, level: str, score: int, why: str, trigger: str) -> Episode:
        ep = Episode(id=uuid.uuid4().hex[:8], start=time.time(), trigger=trigger,
                     start_score=score, peak_level=level, peak_score=score)
        self.episode = ep
        if modes.first_touch() == "vibrate":
            self.decide("vibrate", level, f"{why} Gentle vibration first, no voice or sound.")
        else:
            line = modes.whisper_line()
            self.decide("whisper", level, f'{why} A calm word in the ear: "{line}"', text=line)
        self.decide("notify_phone", level, "First sign of overload: alerting your phone right away.")
        self._notify_circle(level, why)
        return ep

    def _notify_circle(self, level: str, why: str) -> None:
        """Tell the people due at this level who haven't been told yet.

        Contact rules in the contract decide who; without any, the profile's
        care circle members who see alerts are told at the first sign.
        """
        ep = self.episode
        assert ep is not None
        rules = contract.contacts(level)
        if rules:
            due = [r.contact.name for r in rules if r.contact and r.contact.name not in ep.notified]
        else:
            due = [m.name for m in profile.get().circle if "alerts" in m.sees and m.name not in ep.notified]
        if due:
            ep.notified.update(due)
            self.decide("notify_circle", level, f"Telling {', '.join(due)} now.", to=due, why=why)

    def _end_episode(self) -> None:
        ep = self.episode
        assert ep is not None
        end = time.time()
        minutes = (end - ep.start) / 60
        self.decide("remember", "calm",
                    f"Calm again after {minutes:.1f} min (peak: {ep.peak_level}). Saving the episode.",
                    summary=ep.summary(end))
        db.execute(
            "INSERT OR REPLACE INTO episodes "
            "(id, start, end, peak_level, peak_score, trigger, actions, peaks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ep.id, ep.start, end, ep.peak_level, ep.peak_score, ep.trigger,
             json.dumps(ep.actions), json.dumps(ep.peaks)),
        )
        self.episode = None
        self._ended_t = end

    async def on_readings(self) -> None:
        self._ladder(sense.assess(), new_readings=True)
        self.feed.publish(self.status())

    async def on_sense_refresh(self) -> None:
        """Muse Spark's judgement just landed: re-run the ladder with it."""
        self._ladder(sense.assess(), new_readings=False)
        self.feed.publish(self.status())

    def _ladder(self, result: dict, new_readings: bool) -> None:
        level, score, why = result["level"], result["rule"]["score"], result["reason"]
        now = time.time()
        ep = self.episode

        if ep is None:
            if level == "calm":
                return
            if result["rule"]["level"] == "calm" and now - self._ended_t < REOPEN_S:
                return  # Muse still sees the last episode in the history; the readings say calm
            ep = self._start_episode(level, score, f"Load is {level}: {why}.", trigger="sense")

        if level == "calm":
            ep.calm_streak += new_readings  # only fresh readings count toward "calm again"
            if ep.calm_streak >= CALM_READINGS:
                self._end_episode()
            return
        ep.calm_streak = 0
        ep.peak_score = max(ep.peak_score, score)
        for name, sig in state.signals.items():
            ep.peaks[name] = max(ep.peaks.get(name, sig["value"]), sig["value"])
        if level == "overloaded":
            ep.peak_level = "overloaded"
            ep.overloaded_since = ep.overloaded_since or now
            self._notify_circle(level, why)  # anyone the contract says to tell only when overloaded
        else:
            ep.overloaded_since = None

        climbing = score >= ep.start_score + CLIMB_POINTS
        if "adjust_home" not in ep.tried and (level == "overloaded" or climbing):
            because = "overloaded" if level == "overloaded" else f"still climbing (score {ep.start_score} → {score})"
            devices = home.search(CALM_QUERY)  # whatever is nearby right now
            if devices:
                names = ", ".join(d.name for d in devices)
                self.decide("adjust_home", level, f"Load is {because}: {why}. Calming the room with: {names}.",
                            why=why, devices=[d.id for d in devices])
            else:
                self.decide("none", level, f"Load is {because}, but no lights or speakers are nearby to adjust.",
                            blocked="adjust_home")

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
        if event.data.get("is_me") is None:  # unknown speaker: might be the person themselves
            return
        if echo.is_echo(event.data.get("text", "")):  # the mic heard Timber itself
            return
        if "speak_for_you" not in modes.current().actions:  # Dementia mode helps find words instead
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
        self.feed.publish(self.status())

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
