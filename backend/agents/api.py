"""HTTP and websocket routes for the agents."""

import json
import re

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..core import db, modes, profile
from ..core.feed import stream
from .coach import CARDS_DIR, coach, progress
from . import memory as memory_store
from .find import PHOTOS_DIR, find
from .guardian import guardian
from .memory import memory
from .mind import mind
from .reflector import latest, reflector
from .sense import sense

router = APIRouter(prefix="/api", tags=["agents"])
ws_router = APIRouter(tags=["agents"])


@router.get("/sense")
def get_sense(limit: int = 50) -> dict:
    """Current load level, plus recent level changes (newest first)."""
    return {"current": sense.current, "changes": list(reversed(sense.changes))[:limit]}


@router.get("/decisions")
def get_decisions(limit: int = 50) -> dict:
    """The Mind's decisions, newest first, plus the episode in progress (if any)."""
    episode = mind.episode.summary() if mind.episode else None
    return {"episode": episode, "decisions": mind.recent(limit)}


@router.get("/spoken")
def get_spoken(limit: int = 20) -> list[dict]:
    """What Timber said for the person, newest first, with each sentence's receipt."""
    rows = db.query("SELECT * FROM spoken ORDER BY t DESC LIMIT ?", (limit,))
    return [{**r, **{k: json.loads(r[k]) for k in ("sentences", "dropped", "status")}} for r in rows]


@router.get("/circle")
def get_circle(days: int = 14) -> dict:
    """What the care circle sees: episodes per day, estimated mood, trend, recent alerts."""
    return {**guardian.summary(max(7, min(days, 60))), "reflection": latest()}


class ModeIn(BaseModel):
    mode: profile.Mode


@router.get("/mode")
def get_mode() -> dict:
    return {"mode": modes.current().public(), "modes": [m.label for m in modes.MODES.values()]}


@router.post("/mode")
async def set_mode(body: ModeIn) -> dict:
    """Switch Autism / Dementia mode. Saved to the profile; open apps update live."""
    profile.set_mode(body.mode)
    mind.feed.publish(mind.status())
    return {"mode": modes.current().public()}


# ---------- memory ----------
class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class RememberIn(BaseModel):
    text: str = Field(min_length=2, max_length=2000)
    who: str = "You"


@router.get("/memory")
def get_memory() -> dict:
    """The memory graph: records (who, what, when, source) and the people, places, things in them."""
    return memory_store.graph()


@router.post("/memory/ask")
async def ask_memory(body: AskIn) -> dict:
    """Answer only from records, each sentence with its receipt."""
    return await memory_store.ask(body.question)


@router.post("/memory/remember")
async def remember(body: RememberIn) -> dict:
    record = memory_store.save(body.text, body.who, "typed")
    await memory_store.extract(record)
    return record


class FindIn(BaseModel):
    question: str = Field(min_length=2, max_length=200)
    image: str = Field(min_length=20, max_length=12_000_000)  # a data URI from the camera, or a URL


@router.post("/find")
async def find_it(body: FindIn) -> dict:
    """"Where are my keys?" + a photo: SAM 3.1 finds it, Muse Spark says where, memory keeps it."""
    return await find(body.question, body.image)


@router.get("/photos/{name}")
def photo(name: str) -> FileResponse:
    path = PHOTOS_DIR / name
    if not re.fullmatch(r"[0-9a-f]{10}\.(jpg|png|webp)", name) or not path.exists():
        raise HTTPException(404, "No such photo.")
    return FileResponse(path)


# ---------- coach: brain activities ----------
class ResultIn(BaseModel):
    remembered: bool


@router.get("/coach/today")
def coach_today() -> dict:
    """Today's activities (at most three), each with its picture card and receipt."""
    return coach.today()


@router.post("/coach/build")
async def coach_build() -> dict:
    """Make activities from recent key memories now; picture cards follow in the background."""
    made = await coach.build()
    return {"made": len(made), **coach.today()}


@router.post("/coach/{activity_id}/result")
def coach_result(activity_id: str, body: ResultIn) -> dict:
    done = coach.result(activity_id, body.remembered)
    if done is None:
        raise HTTPException(404, "No such activity.")
    return done


@router.get("/cards/{name}")
def card(name: str) -> FileResponse:
    path = CARDS_DIR / name
    if not re.fullmatch(r"[0-9a-f]{8}\.(png|jpg|webp|svg)", name) or not path.exists():
        raise HTTPException(404, "No such card.")
    return FileResponse(path)


@router.get("/orientation")
async def preview_orientation() -> dict:
    return memory_store.orientation()


@router.post("/orientation")
async def say_orientation() -> dict:
    """Say the orientation now (the phone shows it, and speaks it in Dementia mode)."""
    return memory.orient("asked from the app")


@router.get("/progress")
def get_progress(days: int = 14) -> dict:
    """The person's own trend: episodes per day, average length, which helps worked best."""
    return progress(max(7, min(days, 60)))


# ---------- the nightly review ----------
@router.post("/reflect")
async def reflect_now() -> dict:
    """Start a review of today on Muse Wonder (it can take a minute or two)."""
    return {"started": reflector.start(), "running": True}


@router.get("/reflections/latest")
def latest_reflection() -> dict:
    running = bool(reflector.running and not reflector.running.done())
    return {"running": running, "reflection": latest(), "proposals": reflector.proposals("pending")}


@router.post("/proposals/{proposal_id}/{verdict}")
def decide_proposal(proposal_id: int, verdict: str) -> dict:
    """The calm self says yes or no. Only an approved proposal changes the contract."""
    if verdict not in ("approve", "decline"):
        raise HTTPException(404, "Use approve or decline.")
    decided = reflector.decide(proposal_id, verdict == "approve")
    if decided is None:
        raise HTTPException(404, "No pending proposal with that id.")
    return decided


@ws_router.websocket("/ws/person")
async def person_feed(ws: WebSocket) -> None:
    """Live feed for the person's phone: state changes and the Mind's decisions."""
    await stream(ws, mind.feed, mind.status)


@ws_router.websocket("/ws/circle")
async def circle_feed(ws: WebSocket) -> None:
    """Live feed for the care circle: alerts, finished episodes, current level."""
    await stream(ws, guardian.feed, lambda: {"kind": "state", "level": (sense.current or {}).get("level", "calm")})
