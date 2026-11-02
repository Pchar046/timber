"""Muse Voice Transcribe: the Ears. Realtime relay to Meta's ASR websocket.

The browser streams PCM to our backend, and the backend relays it here, so the
key never leaves the server. Events from Meta are passed to `on_event` as-is:
`transcript` is a cumulative partial (replace it, don't append), plus `speaker`
and `speechComplete`.
"""

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect

from .client import ModelError, api_key, live
from .log import record

MODEL = os.getenv("MUSE_TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
WS_URL = os.getenv("META_ASR_URL", "wss://api.meta.ai/v1/asr/realtime")

SAMPLE_RATE = 24_000
BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit LE mono

OnEvent = Callable[[dict], Awaitable[None]]

# What demo mode "hears": another speaker asking a question.
_DEMO_SCRIPT = [
    {"type": "speaker", "speaker": "SPEAKER_1"},
    {"type": "transcript", "speaker": "SPEAKER_1", "text": "Do you"},
    {"type": "transcript", "speaker": "SPEAKER_1", "text": "Do you need a bag?"},
    {"type": "speechComplete", "speaker": "SPEAKER_1"},
]


class TranscribeSession:
    """One realtime session. Use as `async with TranscribeSession(on_event) as s:`
    then `await s.send_audio(pcm)` for each chunk.

    With no key, or if Meta can't be reached, it plays a short demo script once
    audio arrives, with every event marked `"demo": True`.
    """

    def __init__(self, on_event: OnEvent, task: str = "live listening", mode: str = "DIARIZATION"):
        self.on_event = on_event
        self.task = task
        self.mode = mode
        self.status = "demo"
        self.session_id: str | None = None
        self._error: str | None = None
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._demo: asyncio.Task | None = None
        self._t0 = 0.0

    async def __aenter__(self) -> "TranscribeSession":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        self._t0 = time.perf_counter()
        if not live():
            return
        try:
            self._ws = await connect(WS_URL, open_timeout=10, max_size=None)
            await self._ws.send(json.dumps({
                "authorization": {"accessToken": f"Bearer {api_key()}"},
                "audioEncoding": "PCM_24KHZ",
                "model": MODEL,
                "mode": self.mode,
            }))
            first = json.loads(await asyncio.wait_for(self._ws.recv(), 10))
            if "sessionId" not in first:
                raise ModelError(f"no sessionId in first reply: {str(first)[:200]}")
            self.session_id = first["sessionId"]
            self.status = "ok"
            self._reader = asyncio.create_task(self._read())
        except Exception as exc:
            self.status = "error"
            self._error = f"{type(exc).__name__}: {exc}"[:300]
            await self._close_ws()

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(pcm)
                return
            except Exception as exc:  # Meta closed the stream: log it, fall back to demo
                self.status = "error"
                self._error = self._error or f"{type(exc).__name__}: {exc}"[:300]
                await self._close_ws()
        if self._demo is None:
            self._demo = asyncio.create_task(self._play_demo())

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "endStream"}))
                if self._reader:
                    await asyncio.wait_for(self._reader, 10)  # let final events arrive
            except Exception:
                pass
            await self._close_ws()
        if self._demo is not None:
            await self._demo
        ms = (time.perf_counter() - self._t0) * 1000
        record(MODEL, self.task, ms, self.status, self._error)

    async def _read(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, str):
                    await self.on_event(json.loads(raw))
        except Exception as exc:
            if self.status == "ok":
                self.status = "error"
                self._error = f"{type(exc).__name__}: {exc}"[:300]

    async def _play_demo(self) -> None:
        for event in _DEMO_SCRIPT:
            await asyncio.sleep(0.3)
            await self.on_event({**event, "demo": True})

    async def _close_ws(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


async def transcribe_pcm(pcm: bytes, task: str = "transcribe clip", realtime: bool = True) -> dict:
    """Send a whole clip through a session and collect the events (for tests).

    Returns {"text": last transcript, "events": [...], "_status": ...}.
    """
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    chunk = BYTES_PER_SECOND // 10  # 100 ms
    async with TranscribeSession(collect, task=task) as session:
        for i in range(0, len(pcm), chunk):
            await session.send_audio(pcm[i:i + chunk])
            if realtime:
                await asyncio.sleep(0.1)

    texts = [(e.get("text") or e.get("transcript") or "").strip() for e in events if e.get("type") == "transcript"]
    return {"text": next((t for t in reversed(texts) if t), ""), "events": events, "_status": session.status}
