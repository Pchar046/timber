"""Stream a two-voice conversation into /ws/listen, the way the browser does.

    py scripts/listen_test.py                 # http://127.0.0.1:8000
    py scripts/listen_test.py --url http://127.0.0.1:8765

Uses Windows' built-in voices (a cashier and "you"), tags your voice as "me",
then shows the speech events the Mind received.
"""

import argparse
import asyncio
import json
import subprocess
import tempfile
import wave
from pathlib import Path

import httpx
import websockets

LINES = [  # (voice index, words); voice 1 plays "you"
    (0, "Hi there, do you need a bag for that?"),
    (1, "Yes please. Can I pay by card?"),
    (0, "Sure, just tap it on the reader."),
]
RATE, CHUNK = 24000, 4800  # 100 ms of 16-bit mono


def make_audio() -> bytes:
    out = Path(tempfile.gettempdir()) / "timber_listen_test"
    out.mkdir(exist_ok=True)
    script = [
        "Add-Type -AssemblyName System.Speech",
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
        "$v = @($s.GetInstalledVoices() | % { $_.VoiceInfo.Name })",
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000,"
        " [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)",
    ]
    for i, (voice, words) in enumerate(LINES):
        script += [f"$s.SelectVoice($v[{voice} % $v.Count])",
                   f"$s.SetOutputToWaveFile('{out / f'line{i}.wav'}', $f)", f"$s.Speak('{words}')"]
    script.append("$s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", "; ".join(script)], check=True, timeout=60)
    pcm = b""
    for i in range(len(LINES)):
        with wave.open(str(out / f"line{i}.wav")) as w:
            pcm += w.readframes(w.getnframes()) + bytes(RATE * 2)  # 1 s pause between speakers
    return pcm + bytes(RATE * 2 * 2)  # trailing silence so Meta closes the last turn


async def main(url: str) -> None:
    pcm = make_audio()
    ws_url = url.replace("http", "ws", 1) + "/ws/listen"
    me_label = None
    async with websockets.connect(ws_url) as ws:

        async def receive() -> None:
            nonlocal me_label
            async for raw in ws:
                msg = json.loads(raw)
                if msg["kind"] == "status":
                    print(f"session: {msg['status']}")
                elif msg["kind"] == "turn":
                    print(f"  {msg['speaker']:<10} (label {msg['label']}, is_me={msg['is_me']}): {msg['text']}")
                    if me_label is None and msg["text"].lower().startswith("yes"):
                        me_label = msg["label"]  # the second voice is "you"
                        await ws.send(json.dumps({"type": "me", "label": me_label}))
                elif msg["kind"] == "me":
                    print(f"  -> tagged speaker {msg['label']} as me")

        listener = asyncio.create_task(receive())
        for i in range(0, len(pcm), CHUNK):
            await ws.send(pcm[i:i + CHUNK])
            await asyncio.sleep(0.1)
        await asyncio.sleep(4)  # let the last turn finish
        listener.cancel()

    async with httpx.AsyncClient(base_url=url) as client:
        events = (await client.get("/api/events", params={"type": "speech_complete", "limit": 10})).json()
    print("\nspeech_complete events on the bus (newest first):")
    for e in events:
        print(f"  {e['data']['speaker']:<10} is_me={e['data']['is_me']!s:<5} {e['data']['text']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    asyncio.run(main(parser.parse_args().url.rstrip("/")))
