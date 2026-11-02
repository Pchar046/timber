"""SAM 3.1: the Eyes. Finds an object in a photo from a short noun phrase."""

import os
import re

from .client import call, post

MODEL = os.getenv("SAM_MODEL", "sam-3.1")

# <|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|>
_BOX = re.compile(r"<\|box;([^|]*)\|>")


def parse_boxes(text: str) -> list[dict]:
    """Box records from SAM's output text, most confident first."""
    boxes = []
    for match in _BOX.finditer(text or ""):
        box = {}
        for part in match.group(1).split(";"):
            key, _, value = part.partition("=")
            try:
                box[key.strip()] = float(value)
            except ValueError:
                continue
        if {"x1", "y1", "x2", "y2"} <= box.keys():
            boxes.append(box)
    return sorted(boxes, key=lambda b: b.get("c", 0), reverse=True)


def _output_text(reply: dict) -> str:
    """Text from a /responses reply, whichever shape it comes in."""
    if isinstance(reply.get("output_text"), str):
        return reply["output_text"]
    return "".join(
        part.get("text", "")
        for item in reply.get("output", [])
        for part in item.get("content", []) or []
    )


async def find(phrase: str, image: str, task: str = "find object") -> dict:
    """Locate `phrase` (e.g. "keys") in `image` (URL or data URI).

    Returns {"phrase", "found", "boxes": [{x1, y1, x2, y2, w, h, c}]}.
    """
    payload = {
        "model": MODEL,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": phrase},
                {"type": "input_image", "image_url": image},
            ],
        }],
        "metadata": {"include_confidence": "true"},
    }

    async def run() -> dict:
        boxes = parse_boxes(_output_text(await post("/responses", payload, 30)))
        return {"phrase": phrase, "found": bool(boxes), "boxes": boxes}

    demo_box = {"x1": 120, "y1": 340, "x2": 260, "y2": 420, "w": 640, "h": 480, "c": 0.9}
    fallback = {"phrase": phrase, "found": True, "boxes": [demo_box]}
    return await call(MODEL, task, run, fallback)
