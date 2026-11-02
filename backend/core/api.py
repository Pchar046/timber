"""HTTP routes for the core: post events, read state and profile."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import profile
from .events import Event, EventType, bus
from .state import state

router = APIRouter(prefix="/api", tags=["core"])


class EventIn(BaseModel):
    type: EventType
    source: str = "api"
    data: dict[str, Any] = Field(default_factory=dict)
    t: float | None = None  # defaults to now


@router.post("/events")
async def post_event(body: EventIn) -> Event:
    fields = body.model_dump(exclude_none=True)
    return bus.publish(Event(**fields))


@router.get("/events")
def recent_events(limit: int = 50, type: str | None = None) -> list[Event]:
    return bus.recent(limit, type)


@router.get("/state")
def get_state() -> dict:
    return state.snapshot()


@router.get("/profile")
def get_profile() -> profile.Profile:
    return profile.get()
