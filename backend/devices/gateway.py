"""Device gateway: readings from the watch, phone and room sensors become events.

Accepts {device, metric, value, t?} or a list of them. Metric names are mapped to
Timber's signals, and impossible values (a strap that lost contact, a broken
sensor) are dropped with a reason instead of polluting the baseline.
"""

import math
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.events import Event, bus
from .home import KIND_ACTIONS, Device, ToolAction, home, slug

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


# ---------- devices as tools ----------
class DeviceIn(BaseModel):
    """A device announcing itself. Known kinds get standard actions if none are given."""

    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(min_length=1, max_length=30)
    room: str = Field(min_length=1, max_length=40)
    actions: list[ToolAction] | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    ttl_s: float | None = Field(default=None, gt=0, description="Expire unless renewed within this many seconds")


@router.get("/")
async def list_devices() -> dict:
    """Devices present now, their state, and what they've been doing."""
    return home.snapshot()


@router.post("/register")
async def register_device(body: DeviceIn) -> Device:
    actions = body.actions or KIND_ACTIONS.get(body.kind.lower())
    if not actions:
        raise HTTPException(422, f"Unknown kind '{body.kind}': say which actions it offers.")
    device = Device(id=slug(f"{body.room} {body.name}" if body.room.lower() not in body.name.lower() else body.name),
                    name=body.name, kind=body.kind.lower(), room=body.room, actions=actions,
                    state=body.state, source="announced", ttl_s=body.ttl_s)
    return home.register(device)


@router.delete("/{device_id}")
async def remove_device(device_id: str) -> dict:
    if not home.remove(device_id):
        raise HTTPException(404, "No device with that id.")
    return {"removed": device_id}


@router.get("/search")
async def search_devices(q: str) -> list[str]:
    """Tool search, as the agents see it: device ids matching `q`."""
    return [d.id for d in home.search(q)]
