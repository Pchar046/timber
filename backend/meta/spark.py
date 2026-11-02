"""Muse Spark: the fast Mind. Chat completions with JSON output, images and tools."""

import json
import os
import re

from .client import call, post

MODEL = os.getenv("MUSE_SPARK_MODEL", "muse-spark-1.3")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_json(text: str | None) -> dict:
    """Pull one JSON object out of a model reply (tolerates code fences and chatter)."""
    text = _FENCE.sub("", (text or "").strip())
    candidates = [text]
    if match := _OBJECT.search(text):
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return value if isinstance(value, dict) else {"data": value}
    return {"text": text}


def user_content(text: str, images: list[str] | None = None) -> str | list[dict]:
    """Text alone, or text plus images (URLs or data URIs)."""
    if not images:
        return text
    return [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": url}} for url in images
    ]


def _message(reply: dict) -> dict:
    return reply["choices"][0]["message"]


async def ask_json(
    task: str,
    system: str,
    user: str,
    fallback: dict,
    images: list[str] | None = None,
    *,
    model: str = MODEL,
    temperature: float = 0.2,
    timeout: float = 30,
) -> dict:
    """Ask for one JSON object. Returns `fallback` (with `_status`) in demo mode or on error."""
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system + "\nReply with one JSON object only."},
            {"role": "user", "content": user_content(user, images)},
        ],
    }

    async def run() -> dict:
        reply = await post("/chat/completions", payload, timeout)
        return parse_json(_message(reply).get("content"))

    return await call(model, task, run, fallback)


async def chat(
    task: str,
    messages: list[dict],
    fallback: dict,
    tools: list[dict] | None = None,
    *,
    model: str = MODEL,
    temperature: float = 0.2,
    timeout: float = 30,
) -> dict:
    """Free-form chat, optionally with tool calling.

    Returns {"content": str, "tool_calls": [{"id", "name", "arguments": dict}]}.
    `fallback` should have the same shape.
    """
    payload: dict = {"model": model, "temperature": temperature, "messages": messages}
    if tools:
        payload["tools"] = tools

    async def run() -> dict:
        msg = _message(await post("/chat/completions", payload, timeout))
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments") or "{}"
            calls.append({
                "id": tc.get("id"),
                "name": fn.get("name"),
                "arguments": json.loads(args) if isinstance(args, str) else args,
            })
        return {"content": msg.get("content") or "", "tool_calls": calls}

    return await call(model, task, run, fallback)
