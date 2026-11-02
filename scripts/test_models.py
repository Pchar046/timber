"""Call every Meta model once with the real key and print PASS/FAIL.

    py scripts/test_models.py

Needs MODEL_API_KEY in .env. Costs a few cents (one Muse Wonder call, one image).
Never prints the key.
"""

import asyncio
import base64
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.meta import client, image, log, sam, spark, transcribe, wonder  # noqa: E402

OUT = Path(tempfile.gettempdir()) / "timber_model_test"
results: list[tuple[str, bool, str]] = []


def report(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<16} {detail}", flush=True)


def failure(out: dict) -> str:
    return out.get("_error") or f"status {out.get('_status')}"


# ---------- test inputs we make ourselves ----------
def test_png() -> str:
    """320x240 white picture with a red square (left) and a blue circle (right)."""
    w, h = 320, 240
    rows = []
    for y in range(h):
        row = bytearray(b"\x00")
        for x in range(w):
            if 50 <= x < 150 and 70 <= y < 170:
                row += b"\xd0\x30\x30"
            elif (x - 235) ** 2 + (y - 120) ** 2 <= 45 ** 2:
                row += b"\x30\x50\xd0"
            else:
                row += b"\xff\xff\xff"
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


SPOKEN = "Do you need a bag for that?"


def test_speech() -> bytes:
    """~3 s of 24 kHz 16-bit mono speech from Windows' built-in voice (silence elsewhere)."""
    path = OUT / "speech.wav"
    if sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
            "24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
            " [System.Speech.AudioFormat.AudioChannel]::Mono);"
            f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak('{SPOKEN}'); $s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, timeout=30)
    if path.exists():
        with wave.open(str(path)) as wav:
            if (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (24000, 2, 1):
                pcm = wav.readframes(wav.getnframes())
                return pcm + bytes(transcribe.BYTES_PER_SECOND // 2)  # trailing silence
    return bytes(transcribe.BYTES_PER_SECOND * 3)


# ---------- the tests ----------
async def check_model_list() -> None:
    try:
        resp = await client.http().get("/models", headers={"Authorization": f"Bearer {client.api_key()}"})
        resp.raise_for_status()
        ids = sorted(m.get("id", "?") for m in resp.json().get("data", []))
    except Exception as exc:
        report("model list", False, f"{type(exc).__name__}: {str(exc)[:150]}")
        return
    report("model list", True, f"{len(ids)} models available to your key")
    for model_id in ids:
        print(f"        - {model_id}")
    for name, model_id in [("spark", spark.MODEL), ("wonder", wonder.MODEL), ("sam", sam.MODEL),
                           ("image", image.MODEL), ("transcribe", transcribe.MODEL)]:
        if ids and model_id not in ids:
            print(f"   !! {name}: '{model_id}' is not in the list above; set the right ID in .env")


async def check_spark(png: str) -> None:
    start = time.perf_counter()
    out = await spark.ask_json("model test", "You are a health check.", 'Reply {"ok": true}.', {})
    ms = (time.perf_counter() - start) * 1000
    ok = out["_status"] == "ok" and out.get("ok") is True
    report("spark json", ok, f"{ms:.0f} ms, reply {out}" if ok else failure(out) + f" | reply {out}")

    out = await spark.ask_json(
        "model test", "You describe pictures.",
        'What colour is the square, and what colour is the circle? JSON {"square": ..., "circle": ...}',
        {}, images=[png],
    )
    ok = out["_status"] == "ok" and "square" in out
    report("spark image", ok, f"saw {out.get('square')} square, {out.get('circle')} circle" if ok else failure(out))

    tools = [{
        "type": "function",
        "function": {
            "name": "dim_lights",
            "description": "Dim the lights in a room.",
            "parameters": {
                "type": "object",
                "properties": {"room": {"type": "string"}, "percent": {"type": "integer"}},
                "required": ["room", "percent"],
            },
        },
    }]
    out = await spark.chat(
        "model test",
        [{"role": "user", "content": "Dim the living room lights to 30 percent."}],
        {"content": "", "tool_calls": []}, tools=tools,
    )
    calls = out.get("tool_calls") or []
    ok = out["_status"] == "ok" and any(c["name"] == "dim_lights" for c in calls)
    report("spark tools", ok, f"called {calls}" if ok else failure(out) + f" | content: {out.get('content', '')[:80]}")


async def check_wonder() -> None:
    start = time.perf_counter()
    out = await wonder.reflect("model test", "You are a health check.", 'Reply {"ok": true}.', {})
    ms = (time.perf_counter() - start) * 1000
    ok = out["_status"] == "ok" and out.get("ok") is True
    report("wonder", ok, f"{ms:.0f} ms" if ok else failure(out))


async def check_sam(png: str) -> None:
    out = await sam.find("red square", png, task="model test")
    if out["_status"] != "ok":
        report("sam", False, failure(out))
        return
    box = out["boxes"][0] if out["boxes"] else None
    # The square is at x 50-150, y 70-170 in our test picture.
    where = f"best box {box}" if box else "no boxes returned"
    report("sam", bool(box), where)


async def check_image() -> None:
    start = time.perf_counter()
    out = await image.generate("A calm green leaf on a white background", task="model test", size="512x512")
    ms = (time.perf_counter() - start) * 1000
    if out["_status"] != "ok":
        report("image", False, failure(out))
        return
    picture = out["image"]
    if picture.startswith("data:"):
        path = OUT / "card.png"
        path.write_bytes(base64.b64decode(picture.split(",", 1)[1]))
        picture = str(path)
    report("image", True, f"{ms / 1000:.1f} s, saved {picture}")


async def check_transcribe() -> None:
    pcm = test_speech()
    out = await transcribe.transcribe_pcm(pcm, task="model test")
    kinds = sorted({e.get("type", "?") for e in out["events"]})
    ok = out["_status"] == "ok"
    detail = f'heard "{out["text"]}" (expected "{SPOKEN}"), events {kinds}'
    if not ok:
        errors = [e["error"] for e in log.recent(20) if e["model"] == transcribe.MODEL and e.get("error")]
        detail = errors[0] if errors else f"status {out['_status']}"
    report("transcribe", ok, detail)
    if ok and out["events"]:
        print(f"        first event: {out['events'][0]}")


async def main() -> int:
    if not client.live():
        print("No MODEL_API_KEY found. Copy .env.example to .env and paste your key after MODEL_API_KEY=")
        return 1
    if client.api_key() == "PASTE_YOUR_KEY_HERE":
        print(".env still has the placeholder text. Replace PASTE_YOUR_KEY_HERE with your real key and save.")
        return 1
    OUT.mkdir(exist_ok=True)
    print(f"Testing the Meta Model API at {client.BASE_URL}\n")
    png = test_png()
    try:
        await check_model_list()
        await check_spark(png)
        await check_sam(png)
        await check_transcribe()
        await check_wonder()
        await check_image()
    finally:
        await client.aclose()
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
