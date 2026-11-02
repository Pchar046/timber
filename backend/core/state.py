"""Live personal state: the latest signals, a short history, and a personal baseline.

The baseline is learned from the first readings of each signal (Welford's running
mean/variance), saved to SQLite so it survives restarts, then held steady. The
nightly Reflector will be the one to update it later.
"""

import math
import time
from collections import deque
from dataclasses import dataclass

from . import db
from .events import Event

BASELINE_READINGS = 30  # readings per signal before the baseline counts as learned
HISTORY = 120           # recent values kept per signal


@dataclass
class Baseline:
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0

    @property
    def ready(self) -> bool:
        return self.n >= BASELINE_READINGS

    def deviation(self, x: float) -> float | None:
        """How many standard deviations x is from normal (None until learned)."""
        if not self.ready:
            return None
        return (x - self.mean) / max(self.std, 1e-6)


class State:
    def __init__(self) -> None:
        self.signals: dict[str, dict] = {}  # name -> {value, t, source}
        self.history: dict[str, deque[tuple[float, float]]] = {}
        self.baselines: dict[str, Baseline] = {}
        self.last_event: dict[str, dict] = {}  # event type -> last event
        self.events_seen = 0
        self.sense: dict | None = None  # latest {level, confidence, reason}, set by the Sense agent

    def load(self) -> None:
        for row in db.query("SELECT signal, n, mean, m2 FROM baseline"):
            self.baselines[row["signal"]] = Baseline(row["n"], row["mean"], row["m2"])

    def apply(self, event: Event) -> None:
        """Bus listener: fold one event into the live state."""
        self.events_seen += 1
        self.last_event[event.type] = event.model_dump()
        if event.type == "reading":
            for name, value in event.data.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._reading(name, float(value), event.t, event.source)

    def _reading(self, name: str, value: float, t: float, source: str) -> None:
        self.signals[name] = {"value": value, "t": t, "source": source}
        self.history.setdefault(name, deque(maxlen=HISTORY)).append((t, value))
        base = self.baselines.setdefault(name, Baseline())
        if not base.ready:
            base.add(value)
            db.execute(
                "INSERT OR REPLACE INTO baseline (signal, n, mean, m2) VALUES (?, ?, ?, ?)",
                (name, base.n, base.mean, base.m2),
            )

    def snapshot(self) -> dict:
        now = time.time()
        signals = {}
        for name, sig in self.signals.items():
            base = self.baselines.get(name, Baseline())
            dev = base.deviation(sig["value"])
            signals[name] = {
                **sig,
                "age_s": round(now - sig["t"], 1),
                "recent": [v for _, v in list(self.history[name])[-10:]],
                "baseline": {
                    "mean": round(base.mean, 2),
                    "std": round(base.std, 2),
                    "readings": base.n,
                    "ready": base.ready,
                },
                "deviation": None if dev is None else round(dev, 2),
            }
        return {
            "t": now,
            "sense": self.sense,
            "signals": signals,
            "events_seen": self.events_seen,
            "last_event": self.last_event,
        }


state = State()
