"""Try "Where are my keys?" against a running Timber server.

    py scripts/find_test.py desk.jpg                     # your own photo
    py scripts/find_test.py desk.jpg "where is my wallet"
    py scripts/find_test.py                              # Muse Image makes a test photo first

Prints Timber's answer, the SAM 3.1 box and the receipt, and writes the photo with the
box drawn on it to find_result.html (open it in a browser).
"""

import argparse
import asyncio
import base64
import mimetypes
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_PROMPT = ("A realistic phone photo looking down at a wooden kitchen counter: a set of house keys "
               "on a ring, a blue ceramic bowl, a coffee mug and a folded newspaper. Daylight.")


async def test_photo() -> str:
    from backend.meta import client, image  # only needed to make a test photo
    print("Asking Muse Image for a test photo (about 15 s)...")
    out = await image.generate(TEST_PROMPT, task="find test photo", size="1024x1024")
    await client.aclose()
    if out["_status"] != "ok":
        sys.exit(f"Muse Image unavailable ({out['_status']}); pass a photo path instead.")
    img = out["image"]
    if img.startswith("http"):
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.get(img)
            img = f"data:{r.headers.get('content-type', 'image/png')};base64,{base64.b64encode(r.content).decode()}"
    return img


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("photo", nargs="?")
    parser.add_argument("question", nargs="?", default="Where are my keys?")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    if args.photo:
        kind = mimetypes.guess_type(args.photo)[0] or "image/jpeg"
        image = f"data:{kind};base64,{base64.b64encode(Path(args.photo).read_bytes()).decode()}"
    else:
        image = await test_photo()

    async with httpx.AsyncClient(base_url=args.url, timeout=120) as http:
        found = (await http.post("/api/find", json={"question": args.question, "image": image})).json()
    print(f"\n{args.question}\n  -> {found['answer']}   ({found['ms'] / 1000:.1f}s, {found['status']})")
    print(f"     looked for: \"{found['phrase']}\"   receipt: {found['receipt']}")
    if found["box"]:
        print(f"     box: {found['box']}")

    b = found["box"]
    overlay = ""
    if b:
        w, h = b.get("w") or 1, b.get("h") or 1
        overlay = (f'<div style="position:absolute;left:{b["x1"] / w:.2%};top:{b["y1"] / h:.2%};'
                   f'width:{(b["x2"] - b["x1"]) / w:.2%};height:{(b["y2"] - b["y1"]) / h:.2%};'
                   'border:3px solid #f2c94c;border-radius:6px"></div>')
    Path("find_result.html").write_text(
        f'<p style="font:18px sans-serif">{found["answer"]}</p><div style="position:relative;display:inline-block">'
        f'<img src="{image}" style="max-width:640px;display:block">{overlay}</div>', encoding="utf-8")
    print("     wrote find_result.html")


asyncio.run(main())
