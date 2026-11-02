"""Timber backend: FastAPI app (REST + websockets)."""

from fastapi import FastAPI

app = FastAPI(title="Timber")


@app.get("/health")
def health():
    return {"status": "ok", "app": "timber"}
