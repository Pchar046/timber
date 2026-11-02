"""Sense agent: reads load against the person's own baseline: calm, rising or overloaded.

Muse Spark judges the whole picture (values, baseline, recent trend). A transparent
rule score runs alongside as a safety check: if the two disagree by two levels,
the rules win. Muse is asked at most every 15 s unless the rule level changes.
Reasons describe signals and surroundings only, never anything medical.
"""

import json
import re
import time
from collections import deque

from ..core.state import state
from ..meta import spark

LEVELS = ("calm", "rising", "overloaded")
MUSE_EVERY_S = 15
STALE_S = 30  # readings older than this don't count

# Rule score: heart rate and noise earn 0-3 points each, plus 1 for climbing (any
# signal) and 1 for restless movement. Total >= 2 is rising, >= 5 overloaded.
Z_POINTS = (2.0, 3.5, 5.0)                                 # std devs above usual
STD_FLOOR = {"heart_rate": 8.0, "noise_db": 6.0}           # keep z sane on a tight baseline
ABSOLUTE = {                                               # used while learning; noise always
    "heart_rate": (100, 120, 140),
    "noise_db": (75, 85, 95),
}
CLIMB = {"heart_rate": 8.0, "noise_db": 8.0}               # rise across the last few readings
RESTLESS = 0.6
RISING, OVERLOADED = 2, 5
LABEL = {"heart_rate": ("Heart rate", " bpm"), "noise_db": ("Noise", " dB")}

# Last line of defence for "no medical language" in anything Muse writes.
_MEDICAL = re.compile(
    r"\b(panic|anxiety|attack|symptom|diagnos|disorder|condition|syndrome|tachycardi|"
    r"arrhythmi|seizure|medic|treatment|therapy|meltdown|shutdown|cardiac|blood pressure)",
    re.I,
)

SYSTEM = """You are Sense, part of Timber, a daily-living support tool for an autistic adult.
Judge sensory load right now against THIS person's own baseline and recent trend.
Levels:
- calm: signals near their usual.
- rising: clearly above usual, or climbing fast.
- overloaded: far above usual on several signals, or a very loud room with a raised heart rate.
A rule score is included as a hint; use your judgement, but explain any disagreement.
Never use medical or diagnostic words (no conditions, symptoms, attacks, diagnoses or
treatments). Describe only the signals and surroundings, in plain words, to "you".
Keep the reason under 25 words.
JSON: {"level": "calm|rising|overloaded", "confidence": 0.0-1.0, "reason": "..."}"""


def _points(x: float, thresholds: tuple[float, ...]) -> int:
    return sum(x >= t for t in thresholds)


def rule_assess(snapshot: dict) -> dict:
    """Transparent score anyone can check. Returns {level, score, reason, reasons}."""
    score, reasons, any_climbing = 0, [], False
    signals = snapshot["signals"]
    for name in ("heart_rate", "noise_db"):
        sig = signals.get(name)
        if not sig or sig["age_s"] > STALE_S:
            continue
        value, base = sig["value"], sig["baseline"]
        pts = 0
        if base["ready"]:
            pts = _points((value - base["mean"]) / max(base["std"], STD_FLOOR[name]), Z_POINTS)
        if name == "noise_db" or not base["ready"]:
            pts = max(pts, _points(value, ABSOLUTE[name]))
        recent = sig["recent"]
        climbing = len(recent) >= 6 and (sum(recent[-3:]) - sum(recent[-6:-3])) / 3 >= CLIMB[name]
        if pts or climbing:
            label, unit = LABEL[name]
            usual = f" (your usual is about {base['mean']:.0f})" if base["ready"] else ""
            reasons.append(f"{label} {value:.0f}{unit}{usual}{', climbing' if climbing else ''}")
        score += pts
        any_climbing |= climbing
    score += any_climbing
    move = signals.get("movement")
    if move and move["age_s"] <= STALE_S and move["value"] >= RESTLESS:
        score += 1
        reasons.append("lots of movement")
    level = "overloaded" if score >= OVERLOADED else "rising" if score >= RISING else "calm"
    reason = "; ".join(reasons) if reasons else "Readings are near your usual."
    return {"level": level, "score": score, "reason": reason, "reasons": reasons}


def _muse_input(snapshot: dict, rule: dict) -> str:
    compact = {
        name: {
            "now": sig["value"],
            "usual": sig["baseline"]["mean"] if sig["baseline"]["ready"] else "still learning",
            "usual_spread": sig["baseline"]["std"] if sig["baseline"]["ready"] else None,
            "recent": sig["recent"],
            "seconds_ago": sig["age_s"],
        }
        for name, sig in snapshot["signals"].items()
        if sig["age_s"] <= STALE_S
    }
    return json.dumps({"signals": compact, "rule_hint": {"level": rule["level"], "why": rule["reason"]}})


async def ask_muse(snapshot: dict, rule: dict) -> dict:
    fallback = {"level": rule["level"], "confidence": 0.7, "reason": rule["reason"]}
    out = await spark.ask_json("sense: judge load", SYSTEM, _muse_input(snapshot, rule), fallback)
    if out.get("level") not in LEVELS:
        out = {**out, **fallback}
    try:
        confidence = min(1.0, max(0.0, float(out.get("confidence", 0.7))))
    except (TypeError, ValueError):
        confidence = 0.7
    reason = str(out.get("reason") or rule["reason"])[:200]
    if _MEDICAL.search(reason):
        reason = rule["reason"]
    return {"level": out["level"], "confidence": confidence, "reason": reason, "status": out["_status"]}


def combine(muse: dict, rule: dict) -> dict:
    """Muse decides, unless it is two levels away from the rules (then rules win).

    Without a real Muse answer (demo mode or an error) the live rules decide.
    """
    gap = abs(LEVELS.index(muse["level"]) - LEVELS.index(rule["level"]))
    if muse["status"] != "ok":
        level, confidence, reason = rule["level"], 0.7, rule["reason"]
        source = f"rules ({muse['status']})"
    elif gap >= 2:
        level, confidence, reason = rule["level"], 0.7, rule["reason"]
        source = "rules (safety override)"
    else:
        level, confidence, reason = muse["level"], muse["confidence"], muse["reason"]
        source = "muse"
    return {
        "level": level,
        "confidence": round(confidence, 2),
        "reason": reason,
        "source": source,
        "rule": {"level": rule["level"], "score": rule["score"], "reason": rule["reason"]},
        "t": time.time(),
    }


class Sense:
    """Called by the Mind after each burst of readings."""

    def __init__(self) -> None:
        self.current: dict | None = None
        self.changes: deque[dict] = deque(maxlen=200)  # level transitions only
        self._muse: dict | None = None
        self._muse_t = 0.0
        self._rule_level: str | None = None

    async def assess(self) -> dict:
        snapshot = state.snapshot()
        rule = rule_assess(snapshot)
        stale = time.time() - self._muse_t >= MUSE_EVERY_S
        if self._muse is None or rule["level"] != self._rule_level or stale:
            self._muse = await ask_muse(snapshot, rule)
            self._muse_t = time.time()
        self._rule_level = rule["level"]

        result = combine(self._muse, rule)
        changed = self.current is None or result["level"] != self.current["level"]
        self.current = state.sense = result
        if changed:
            self.changes.append(result)
        return result


sense = Sense()
