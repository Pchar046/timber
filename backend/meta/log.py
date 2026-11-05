"""Shared model log: every Meta model call lands here so the console can show
"Meta models at work". In-memory ring buffer; newest entries win."""

import time
from collections import deque

from ..core.feed import Feed

_entries: deque[dict] = deque(maxlen=500)
feed = Feed("models")  # each call as it lands, for the console


def record(model: str, task: str, ms: float, status: str, error: str | None = None) -> dict:
    """status is "ok" (real call), "demo" (no key) or "error" (call failed)."""
    entry = {"model": model, "task": task, "ms": round(ms), "status": status, "t": time.time()}
    if error:
        entry["error"] = error
    _entries.append(entry)
    feed.publish({"kind": "model", "entry": entry})
    return entry


def recent(limit: int = 100) -> list[dict]:
    """Newest first."""
    return list(reversed(_entries))[:limit]


def summary() -> dict[str, dict[str, int]]:
    """Call counts per model, split by status."""
    out: dict[str, dict[str, int]] = {}
    for e in _entries:
        counts = out.setdefault(e["model"], {"ok": 0, "demo": 0, "error": 0})
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return out
