"""Shared plumbing for the Meta Model API clients: config, HTTP, demo mode, logging.

The API key is read from the environment (.env) and only ever sent to Meta.
With no key, every client returns its demo fallback so the app still runs.
"""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable

import httpx
from dotenv import load_dotenv

from .log import record

load_dotenv()

BASE_URL = os.getenv("META_BASE_URL", "https://api.meta.ai/v1").rstrip("/")

_http: httpx.AsyncClient | None = None


class ModelError(Exception):
    pass


def api_key() -> str | None:
    return os.getenv("MODEL_API_KEY") or None


def live() -> bool:
    return api_key() is not None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(base_url=BASE_URL, timeout=30)
    return _http


async def aclose() -> None:
    if _http is not None:
        await _http.aclose()


RETRY_STATUS = (429, 502, 503)  # busy or rate-limited: worth another try
RETRY_WAITS = (0.8, 2.0)


async def post(path: str, payload: dict, timeout: float = 30) -> dict:
    """POST JSON to the Meta Model API and return the parsed reply.

    A busy or rate-limited answer is retried twice after a short pause; anything
    else fails straight away so the caller's fallback can take over.
    """
    for wait in (*RETRY_WAITS, None):
        resp = await http().post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {api_key()}"},
            timeout=timeout,
        )
        if resp.status_code not in RETRY_STATUS or wait is None:
            break
        await asyncio.sleep(wait)
    if resp.is_error:
        raise ModelError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


async def call(
    model: str,
    task: str,
    run: Callable[[], Awaitable[dict]],
    fallback: dict,
) -> dict:
    """Run one model call, or return `fallback` in demo mode. Always logs.

    The result carries `_status`: "ok", "demo" or "error". On error the
    fallback is returned too, so a flaky network never breaks the demo.
    """
    if not live():
        record(model, task, 0, "demo")
        return {**fallback, "_status": "demo"}

    start = time.perf_counter()
    try:
        result = await run()
    except Exception as exc:
        ms = (time.perf_counter() - start) * 1000
        error = f"{type(exc).__name__}: {exc}"[:300]
        record(model, task, ms, "error", error)
        return {**fallback, "_status": "error", "_error": error}
    record(model, task, (time.perf_counter() - start) * 1000, "ok")
    return {**result, "_status": "ok"}
