"""Timber backend: FastAPI app (REST + websockets)."""

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .agents.api import router as agents_router
from .agents.api import ws_router as agents_ws_router
from .agents.coach import coach
from .agents.environment import environment
from .agents.fill import fill
from .agents.guardian import guardian
from .agents.memory import memory
from .agents.mind import mind
from .agents.reflector import reflector
from .agents.voice import voice
from .core import db, profile
from .core.api import router as core_router
from .core.events import bus
from .core.state import state
from .demo import router as demo_router
from .devices.gateway import router as devices_router
from .devices.home import home
from .devices.listen import router as listen_router
from .meta import client as meta_client
from .meta.api import router as models_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    profile.load()
    state.load()
    home.seed()
    bus.on(db.save_event)
    bus.on(state.apply)
    agents = [
        asyncio.create_task(mind.run(), name="mind"),
        asyncio.create_task(guardian.run(), name="guardian"),
        asyncio.create_task(environment.run(), name="environment"),
        asyncio.create_task(voice.run(), name="voice"),
        asyncio.create_task(fill.run(), name="fill"),
        asyncio.create_task(reflector.run(), name="reflector"),
        asyncio.create_task(memory.run(), name="memory"),
        asyncio.create_task(coach.run(), name="coach"),
    ]
    yield
    for task in agents:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await meta_client.aclose()
    db.close()


app = FastAPI(title="Timber", lifespan=lifespan)
app.include_router(core_router)
app.include_router(devices_router)
app.include_router(listen_router)
app.include_router(agents_router)
app.include_router(agents_ws_router)
app.include_router(models_router)
app.include_router(demo_router)

APPS_DIR = Path(__file__).resolve().parents[1] / "apps"
app.mount("/person", StaticFiles(directory=APPS_DIR / "person", html=True), name="person")
app.mount("/contract", StaticFiles(directory=APPS_DIR / "contract", html=True), name="contract")
app.mount("/circle", StaticFiles(directory=APPS_DIR / "circle", html=True), name="circle")
app.mount("/listen", StaticFiles(directory=APPS_DIR / "listen", html=True), name="listen")
app.mount("/watch", StaticFiles(directory=APPS_DIR / "watch", html=True), name="watch")
app.mount("/console", StaticFiles(directory=APPS_DIR / "console", html=True), name="console")


@app.get("/health")
def health():
    return {"status": "ok", "app": "timber", "meta_live": meta_client.live()}
