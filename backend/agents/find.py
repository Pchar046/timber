"""Find it: "Where are my keys?" from a photo.

1. The question becomes a short noun phrase ("keys"), which is what SAM 3.1 wants.
2. SAM 3.1 finds it and returns a box with a confidence.
3. Muse Spark looks at the same photo and says where the box is in plain words
   ("on the kitchen counter, next to the blue bowl"). Without Muse, Timber says where it
   is in the picture ("lower left of the photo"), which the box alone proves.
4. The sighting is saved to memory with source "camera", so "Timber, where did I leave my
   keys?" can be answered later with this photo as the receipt.
If SAM doesn't see it, Timber says so. It never guesses where something might be.
"""

import base64
import logging
import re
import time
import uuid

from ..core import db, echo
from ..core.db import DATA_DIR
from ..meta import sam, spark
from . import memory
from .mind import mind

log = logging.getLogger(__name__)

PHOTOS_DIR = DATA_DIR / "photos"
MIN_CONFIDENCE = 0.3
NEAR = 0.05  # boxes this close (fraction of the photo) belong to one thing, e.g. keys on a ring
WHERE_SYSTEM = """You help someone find a thing in their own photo. The {phrase} is inside the box
x1={x1:.0%} y1={y1:.0%} x2={x2:.0%} y2={y2:.0%} (fractions of the photo's width and height).
Say where it is, using ONLY what you can see: the surface or place it's on or in, and at most one
thing right next to it. A short phrase that completes "The {phrase} is ...", e.g. "on the kitchen
counter, next to the blue bowl". No guessing about anything you can't see.
JSON: {{"where": "..."}}"""

ASKING = re.compile(r"^\W*(?:timber\W+)?(?:where(?:'s| is| are)?|find|can you find|have you seen|"
                    r"i can't find|i cannot find|look for|help me find)\s+", re.I)
OWNER = re.compile(r"^(?:my|the|our|your|a|an|some)\s+", re.I)


def phrase_of(question: str) -> str:
    """'Where are my keys?' -> 'keys'"""
    text = ASKING.sub("", question.strip())
    text = re.sub(r"\b(?:again|please|now|today)\b", "", text, flags=re.I)
    text = OWNER.sub("", text.strip(" ?.!,")).strip(" ?.!,")
    return " ".join(text.split()[:4]) or question.strip(" ?.!")


def place_in_photo(box: dict) -> str:
    """Where the box sits in the picture, from the numbers alone."""
    w, h = box.get("w") or 1, box.get("h") or 1
    cx, cy = (box["x1"] + box["x2"]) / 2 / w, (box["y1"] + box["y2"]) / 2 / h
    row = "top" if cy < 1 / 3 else "bottom" if cy > 2 / 3 else "middle"
    col = "left" if cx < 1 / 3 else "right" if cx > 2 / 3 else "centre"
    if row == "middle" and col == "centre":
        return "in the middle of the photo"
    if row == "middle":
        return f"on the {col} side of the photo"
    if col == "centre":
        return f"at the {row} of the photo"
    return f"in the {row} {col} of the photo"


def merge(boxes: list[dict]) -> dict:
    """SAM gives one box per piece (each key on a ring). Join the pieces near the best one."""
    best = dict(boxes[0])
    gap_x, gap_y = NEAR * (best.get("w") or 1), NEAR * (best.get("h") or 1)
    pieces, rest = 1, boxes[1:]
    grew = True
    while grew:
        grew = False
        for b in list(rest):
            if (b["x1"] <= best["x2"] + gap_x and b["x2"] >= best["x1"] - gap_x
                    and b["y1"] <= best["y2"] + gap_y and b["y2"] >= best["y1"] - gap_y):
                best.update(x1=min(best["x1"], b["x1"]), y1=min(best["y1"], b["y1"]),
                            x2=max(best["x2"], b["x2"]), y2=max(best["y2"], b["y2"]))
                rest.remove(b)
                pieces += 1
                grew = True
    best["pieces"] = pieces
    return best


def _is_are(phrase: str) -> str:
    last = phrase.split()[-1].lower()
    return "are" if last.endswith("s") and not last.endswith("ss") else "is"


def _keep_photo(image: str) -> str | None:
    """Save a data-URI photo next to the memories; returns its id."""
    match = re.match(r"data:image/(\w+);base64,(.+)", image, re.S)
    if not match:
        return None
    photo_id = uuid.uuid4().hex[:10]
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if match.group(1) in ("jpeg", "jpg") else match.group(1)
    (PHOTOS_DIR / f"{photo_id}.{ext}").write_bytes(base64.b64decode(match.group(2)))
    return f"{photo_id}.{ext}"


async def find(question: str, image: str) -> dict:
    started = time.time()
    phrase = phrase_of(question)
    seen = await sam.find(phrase, image, task="find: " + phrase)
    boxes = [b for b in seen.get("boxes") or [] if b.get("c", 1) >= MIN_CONFIDENCE]
    result = {"kind": "found", "question": question, "phrase": phrase, "found": bool(boxes),
              "box": merge(boxes) if boxes else None, "status": {"sam": seen["_status"]}}

    if not boxes:
        result["answer"] = f"I couldn't see your {phrase} in this photo."
        result["receipt"] = f"SAM 3.1 looked for \"{phrase}\" and found nothing ({seen['_status']})"
    else:
        box = result["box"]
        w, h = box.get("w") or 1, box.get("h") or 1
        frac = {"x1": box["x1"] / w, "y1": box["y1"] / h, "x2": box["x2"] / w, "y2": box["y2"] / h}
        out = await spark.ask_json("find: say where", WHERE_SYSTEM.format(phrase=phrase, **frac),
                                   f"Where is the {phrase}?", {"where": place_in_photo(box)},
                                   images=[image], reasoning_effort="minimal", timeout=30)
        where = str(out.get("where") or "").strip().rstrip(".")[:160] or place_in_photo(box)
        result["status"]["describe"] = out["_status"]
        result["answer"] = f"Your {phrase} {_is_are(phrase)} {where}."
        result["receipt"] = f"camera photo, SAM 3.1 box (confidence {box.get('c', 0):.2f})"
        photo = _keep_photo(image)
        record = memory.save(f"{phrase[0].upper()}{phrase[1:]} seen {where}.", "Timber", "camera", kind="seen")
        db.execute("UPDATE memories SET photo = ? WHERE id = ?", (photo, record["id"]))
        result["record"] = record["id"]
        result["photo"] = photo

    result["ms"] = round((time.time() - started) * 1000)
    result["t"] = time.time()
    echo.record(result["answer"])
    mind.feed.publish(result)
    log.info("find %r: %s", phrase, result["answer"])
    return result
