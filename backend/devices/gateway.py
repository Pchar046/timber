"""Device gateway: readings from the watch, phone and room sensors become events.

Accepts {device, metric, value, t?} or a list of them. Metric names are mapped to
Timber's signals, and impossible values (a strap that lost contact, a broken
sensor) are dropped with a reason instead of polluting the baseline.
"""

import math
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..core.events import Event, bus

router = APIRouter(prefix="/api/devices", tags=["devices"])

ALIASES = {
    "heart_rate": "heart_rate", "hr": "heart_rate", "pulse": "heart_rate",
    "bpm": "heart_rate", "heartrate": "heart_rate",
    "noise_db": "noise_db", "noise": "noise_db", "db": "noise_db",
    "sound": "noise_db", "noise_level": "noise_db",
    "movement": "movement", "motion": "movement", "move": "movement",
    "activity": "movement", "accel": "movement",
}

# Plausible ranges for a person and a room; anything outside is a sensor fault.
LIMITS = {
    "heart_rate": (30, 220),   # bpm; 0 means the strap lost contact
    "noise_db": (10, 130),     # dB SPL
    "movement": (0, 1),        # normalised activity
}


class Reading(BaseModel):
    device: str
    metric: str
    value: Any
    t: float | None = None


def normalise(reading: Reading) -> tuple[str, float] | str:
    """(signal, value) for a usable reading, or the reason it was ignored."""
    signal = ALIASES.get(reading.metric.strip().lower().replace("-", "_").replace(" ", "_"))
    if signal is None:
        return f"unknown metric '{reading.metric}'"
    try:
        value = float(reading.value)
    except (TypeError, ValueError):
        return f"not a number: {reading.value!r}"
    low, high = LIMITS[signal]
    if not math.isfinite(value) or not low <= value <= high:
        return f"{signal} {value} outside {low}-{high}"
    return signal, value


def ingest_one(reading: Reading) -> Event | str:
    result = normalise(reading)
    if isinstance(result, str):
        return result
    signal, value = result
    fields: dict[str, Any] = {"type": "reading", "source": reading.device, "data": {signal: value}}
    if reading.t is not None:
        fields["t"] = reading.t
    return bus.publish(Event(**fields))


@router.post("/ingest")
async def ingest(body: Reading | list[Reading]) -> dict:
    readings = body if isinstance(body, list) else [body]
    accepted, ignored = [], []
    for reading in readings:
        result = ingest_one(reading)
        if isinstance(result, str):
            ignored.append({**reading.model_dump(), "reason": result})
        else:
            accepted.append(result)
    return {"accepted": accepted, "ignored": ignored}
