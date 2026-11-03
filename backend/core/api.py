"""HTTP routes for the core: post events, read state and profile."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import contract, profile
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


# ---------- Calm-Self Contract ----------
class RuleIn(BaseModel):
    text: str = Field(min_length=3, max_length=1000)


class CheckIn(BaseModel):
    action: str
    level: str | None = None
    text: str | None = None


@router.get("/contract")
def get_contract() -> contract.Contract:
    return contract.get()


@router.post("/contract/rules")
async def add_rules(body: RuleIn) -> dict:
    """Plain words in; structured rules out (saved). `status` says who structured them."""
    rules, status = await contract.add(body.text)
    if not rules:
        raise HTTPException(422, "Couldn't turn that into a rule. Try saying it another way.")
    return {"added": rules, "status": status}


@router.delete("/contract/rules/{rule_id}")
def delete_rule(rule_id: str) -> dict:
    if not contract.remove(rule_id):
        raise HTTPException(404, "No rule with that id.")
    return {"removed": rule_id}


@router.post("/contract/check")
def check_action(body: CheckIn) -> contract.Verdict:
    """Would the contract allow this action right now? (Agents call contract.check directly.)"""
    return contract.check(body.action, body.level, body.text)
