"""Timber backend: FastAPI app (REST + websockets)."""

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .agents.api import router as agents_router
from .agents.api import ws_router as agents_ws_router
from .agents.mind import mind
from .core import db, profile
from .core.api import router as core_router
from .core.events import bus
from .core.state import state
from .devices.gateway import router as devices_router
from .meta import client as meta_client
from .meta.api import router as models_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    profile.load()
    state.load()
    bus.on(db.save_event)
    bus.on(state.apply)
    agents = [asyncio.create_task(mind.run(), name="mind")]
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
app.include_router(agents_router)
app.include_router(agents_ws_router)
app.include_router(models_router)

APPS_DIR = Path(__file__).resolve().parents[1] / "apps"
app.mount("/person", StaticFiles(directory=APPS_DIR / "person", html=True), name="person")
app.mount("/watch", StaticFiles(directory=APPS_DIR / "watch", html=True), name="watch")


@app.get("/health")
def health():
    return {"status": "ok", "app": "timber", "meta_live": meta_client.live()}
