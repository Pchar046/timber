"""Timber backend: FastAPI app (REST + websockets)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .meta import client as meta_client
from .meta.api import router as models_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await meta_client.aclose()


app = FastAPI(title="Timber", lifespan=lifespan)
app.include_router(models_router)


@app.get("/health")
def health():
    return {"status": "ok", "app": "timber", "meta_live": meta_client.live()}
