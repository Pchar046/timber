"""Muse Image: the Illustrator. ~8-18 s per image, so pre-generate; never on the live path.

Model ID muse-image-1.0 confirmed against the live model list (Nov 2).
"""

import base64
import html
import os

from .client import call, post

MODEL = os.getenv("MUSE_IMAGE_MODEL", "muse-image-1.0")


def _placeholder(prompt: str) -> str:
    """A calm placeholder card (SVG data URI) for demo mode."""
    label = html.escape(prompt[:60])
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">'
        '<rect width="100%" height="100%" rx="32" fill="#e8f0ec"/>'
        '<text x="50%" y="46%" text-anchor="middle" font-family="sans-serif" '
        'font-size="22" fill="#4a6b5d">Picture card (demo)</text>'
        f'<text x="50%" y="56%" text-anchor="middle" font-family="sans-serif" '
        f'font-size="16" fill="#6b8a7c">{label}</text></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


async def generate(prompt: str, task: str = "picture card", size: str = "1024x1024") -> dict:
    """Returns {"prompt", "image"}; `image` is a URL or a data URI."""
    payload = {"model": MODEL, "prompt": prompt, "size": size, "n": 1}

    async def run() -> dict:
        item = (await post("/images/generations", payload, timeout=120))["data"][0]
        image = item.get("url") or f"data:image/png;base64,{item['b64_json']}"
        return {"prompt": prompt, "image": image}

    return await call(MODEL, task, run, {"prompt": prompt, "image": _placeholder(prompt)})
