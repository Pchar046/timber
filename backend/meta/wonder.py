"""Muse Wonder: the nightly Reflector. Bigger, slower, pricier, so never on the live path."""

import os

from . import spark

MODEL = os.getenv("MUSE_WONDER_MODEL", "muse-wonder-1.0")


async def reflect(task: str, system: str, user: str, fallback: dict) -> dict:
    """One deep JSON-producing pass (same API shape as Muse Spark, long timeout)."""
    return await spark.ask_json(
        task, system, user, fallback, model=MODEL, temperature=0.3, timeout=300
    )
