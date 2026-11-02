"""HTTP routes for the Meta model layer: the model log and a quick self-test."""

import asyncio

from fastapi import APIRouter

from . import image, log, sam, spark, transcribe, wonder
from .client import live

router = APIRouter(prefix="/api/models", tags=["models"])

# 1x1 PNG: enough to prove SAM is reachable without shipping a sample photo.
_TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@router.get("/log")
def model_log(limit: int = 100) -> dict:
    return {"live": live(), "summary": log.summary(), "entries": log.recent(limit)}


@router.post("/test")
async def self_test() -> dict:
    """Call every Meta model once. In demo mode this is free; with a key it costs a few cents."""
    results = await asyncio.gather(
        spark.ask_json(
            "self-test", "You are a health check.", 'Reply {"ok": true}.', {"ok": True}
        ),
        wonder.reflect(
            "self-test", "You are a health check.", 'Reply {"ok": true}.', {"ok": True}
        ),
        sam.find("object", _TINY_PNG, task="self-test"),
        image.generate("a calm green leaf", task="self-test", size="512x512"),
        transcribe.transcribe_pcm(bytes(transcribe.BYTES_PER_SECOND), task="self-test"),
    )
    names = ["spark", "wonder", "sam", "image", "transcribe"]
    return {"live": live(), "results": {n: r["_status"] for n, r in zip(names, results)}}
