"""Guardian: tells the care circle, keeps outgoing words safe, and closes each episode.

- On `notify_circle` it alerts right away. The alert is built from live readings
  (no model on this path), checked, saved, and pushed to /circle.
- Every outgoing message is checked for medical language and against the
  Calm-Self Contract before it leaves.
- On `remember` it asks Muse Spark to estimate the mood during the episode and
  what helped. Both are always labelled as estimates.
"""

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta

from ..core import contract, db, profile
from ..core.feed import Feed
from ..core.safety import medical_term
from ..core.state import state
from ..meta import spark
from .mind import mind
from .sense import sense

log = logging.getLogger(__name__)

MOODS = ("settled", "uneasy", "strained", "overwhelmed")  # an estimate, never a diagnosis
CALMING = ("vibrate", "whisper", "adjust_home", "speak_for_you")
LEVEL_WORDS = {"rising": "rising", "overloaded": "very high"}
CIRCLE = "care circle"

ESTIMATE_SYSTEM = f"""You are Guardian, part of Timber, a daily-living support tool. Summarise
one finished episode of overload for the person's care circle
(family and partners) so they can support and understand them.
- mood: estimate the person's mood during the episode from the signals only. Exactly one of
  {list(MOODS)}. It is an estimate, not a diagnosis.
- helped: which of Timber's actions likely helped, chosen ONLY from "actions_taken".
- note: at most 25 words, written TO the family ABOUT the person: use their name ("person"),
  never "you", and no pronouns. Kind, factual, only from the data given. No medical words,
  no advice, no guessing about thoughts or feelings beyond the mood estimate.
JSON: {{"mood": "...", "helped": [...], "note": "..."}}"""


def _facts() -> str:
    """Live readings in plain words, third person, e.g. 'heart rate 104 (usual 72), noise 86 dB'."""
    parts = []
    signals = state.snapshot()["signals"]
    for key, label, unit in (("heart_rate", "heart rate", ""), ("noise_db", "noise", " dB")):
        sig = signals.get(key)
        if not sig:
            continue
        usual = f" (usual {sig['baseline']['mean']:.0f})" if sig["baseline"]["ready"] else ""
        parts.append(f"{label} {sig['value']:.0f}{unit}{usual}")
    return ", ".join(parts)


class Guardian:
    def __init__(self) -> None:
        self.feed = Feed("circle")

    # ---------- every outgoing message passes through here ----------
    def check_outgoing(self, text: str, audience: str | None) -> tuple[bool, str]:
        """None audience = unknown listener, so every never-say rule applies."""
        term = medical_term(text)
        if term:
            return False, f'contains the medical word "{term}"'
        verdict = contract.check("notify_circle", text=text, audience=audience)
        if not verdict.allowed:
            return False, verdict.reason
        return True, ""

    # ---------- alerts ----------
    def send_alert(self, decision: dict) -> dict:
        name = profile.get().name
        level = decision["level"]
        to = decision["data"].get("to") or []
        facts = _facts()
        message = f"{name}'s load is {LEVEL_WORDS.get(level, level)}"
        message += f": {facts}." if facts else "."
        message += f" Timber is supporting {name}."
        ok, why = self.check_outgoing(message, CIRCLE)
        status = "delivered in app"
        if not ok:
            log.warning("alert text blocked (%s); sending the plain version", why)
            message, status = f"{name} may need support right now. Timber is with {name}.", f"simplified: {why}"
        alert = {
            "episode": decision["data"].get("episode"), "level": level, "to": to,
            "message": message, "status": status, "t": time.time(),
        }
        db.execute(
            "INSERT INTO alerts (episode, level, sent_to, message, status, t) VALUES (?, ?, ?, ?, ?, ?)",
            (alert["episode"], level, json.dumps(to), message, status, alert["t"]),
        )
        self.feed.publish({"kind": "alert", "alert": alert})
        return alert

    # ---------- closing an episode ----------
    async def close_episode(self, summary: dict) -> dict:
        minutes = ((summary["end"] or time.time()) - summary["start"]) / 60
        taken = [a for a in summary["actions"] if a in CALMING]
        baseline = {k: v["baseline"]["mean"] for k, v in state.snapshot()["signals"].items()
                    if v["baseline"]["ready"]}
        fallback = {
            "mood": ("overwhelmed" if "speak_for_you" in taken else "strained")
            if summary["peak_level"] == "overloaded" else "uneasy",
            "helped": taken,
            "note": f"{minutes:.0f}-minute episode, peak {summary['peak_level']}. Back to calm now.",
        }
        facts = {
            "person": profile.get().name,
            "minutes": round(minutes, 1), "peak_level": summary["peak_level"],
            "peak_readings": summary.get("peaks", {}), "usual_readings": baseline,
            "started_by": "the person" if summary["trigger"] == "you" else "Timber's readings",
            "actions_taken": taken,
        }
        out = await spark.ask_json("guardian: estimate episode", ESTIMATE_SYSTEM, json.dumps(facts),
                                   fallback, timeout=90, reasoning_effort="low")

        mood = out.get("mood") if out.get("mood") in MOODS else fallback["mood"]
        helped = [a for a in out.get("helped") or [] if a in taken] or fallback["helped"]
        note = str(out.get("note") or "")[:240]
        if not note or not self.check_outgoing(note, CIRCLE)[0]:
            note = fallback["note"]
        source = "muse" if out["_status"] == "ok" else f"rules ({out['_status']})"

        db.execute(
            "UPDATE episodes SET mood = ?, helped = ?, note = ?, estimate_source = ? WHERE id = ?",
            (mood, json.dumps(helped), note, source, summary["id"]),
        )
        episode = {**summary, "mood": mood, "mood_estimated": True, "helped": helped,
                   "note": note, "estimate_source": source}
        self.feed.publish({"kind": "episode", "episode": _public(episode)})
        return episode

    # ---------- the care-circle view ----------
    def summary(self, days: int = 14) -> dict:
        today = date.today()
        since = datetime.combine(today - timedelta(days=days - 1), datetime.min.time()).timestamp()
        rows = db.query("SELECT * FROM episodes WHERE start >= ? ORDER BY start DESC", (since,))
        episodes = [_public(_row(r)) for r in rows]

        per_day = {today - timedelta(days=i): {"episodes": 0, "minutes": 0.0, "moods": {}} for i in range(days)}
        for ep in episodes:
            day = per_day.get(datetime.fromtimestamp(ep["start"]).date())
            if day is None:
                continue
            day["episodes"] += 1
            day["minutes"] += ep["minutes"]
            if ep["mood"]:
                day["moods"][ep["mood"]] = day["moods"].get(ep["mood"], 0) + 1

        this_week = sum(per_day[today - timedelta(days=i)]["episodes"] for i in range(min(7, days)))
        last_week = sum(per_day[today - timedelta(days=i)]["episodes"] for i in range(7, min(14, days)))
        alerts = db.query("SELECT * FROM alerts ORDER BY t DESC LIMIT 20")
        return {
            "name": profile.get().name,
            "now": {"level": (sense.current or {}).get("level", "calm"),
                    "episode": mind.episode.summary() if mind.episode else None},
            "days": [{"date": d.isoformat(), **v, "minutes": round(v["minutes"], 1)}
                     for d, v in sorted(per_day.items())],
            "trend": {"this_week": this_week, "last_week": last_week,
                      "direction": _direction(this_week, last_week)},
            "episodes": episodes[:20],
            "alerts": [{**a, "to": json.loads(a.pop("sent_to"))} for a in alerts],
        }

    # ---------- loop ----------
    async def run(self) -> None:
        queue = mind.feed.subscribe()
        closing: set[asyncio.Task] = set()
        try:
            while True:
                message = await queue.get()
                try:
                    if message["kind"] == "state":
                        self.feed.publish({"kind": "state", "level": (message["sense"] or {}).get("level", "calm")})
                    if message["kind"] != "decision":
                        continue
                    decision = message["decision"]
                    if decision["action"] == "notify_circle":
                        self.send_alert(decision)
                    elif decision["action"] == "remember" and "summary" in decision["data"]:
                        task = asyncio.create_task(self.close_episode(decision["data"]["summary"]))
                        closing.add(task)  # Muse is slow; never hold up the next alert
                        task.add_done_callback(_finished(closing))
                except Exception:
                    log.exception("guardian step failed")
        finally:
            mind.feed.unsubscribe(queue)


def _finished(tasks: set[asyncio.Task]):
    """Done-callback: forget the task, and log it if it failed (instead of losing the error)."""
    def done(task: asyncio.Task) -> None:
        tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("closing an episode failed", exc_info=task.exception())
    return done


def _row(r: dict) -> dict:
    return {**r, "actions": json.loads(r["actions"]), "helped": json.loads(r["helped"] or "[]"),
            "peaks": json.loads(r["peaks"] or "{}")}


def _public(ep: dict) -> dict:
    """What the care circle sees for one episode."""
    end = ep.get("end") or time.time()
    return {
        "id": ep["id"], "start": ep["start"], "end": ep.get("end"),
        "minutes": round((end - ep["start"]) / 60, 1), "peak_level": ep["peak_level"],
        "started_by": profile.get().name if ep["trigger"] == "you" else "Timber",
        "mood": ep.get("mood"), "mood_estimated": True, "helped": ep.get("helped") or [],
        "note": ep.get("note"), "estimate_source": ep.get("estimate_source"),
    }


def _direction(this_week: int, last_week: int) -> str:
    if this_week < last_week:
        return "fewer"
    if this_week > last_week:
        return "more"
    return "same"


guardian = Guardian()
