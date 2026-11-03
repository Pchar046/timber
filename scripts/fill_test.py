"""Try word-fill without a microphone, against a running Timber server.

    py scripts/fill_test.py                         # a few unfinished sentences
    py scripts/fill_test.py "can you pass the"      # your own

Sends each sentence as if you had stopped mid-way, then prints the word Timber offers
on the phone (open /person to see it there too).
"""

import argparse
import asyncio
import json
import time

import httpx
import websockets

SENTENCES = ["Can you pass the", "Where is my", "I'd like a cup of", "It's okay, I just need"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sentences", nargs="*", default=SENTENCES)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    async with websockets.connect(args.url.replace("http", "ws", 1) + "/ws/person") as feed, \
            httpx.AsyncClient(base_url=args.url, timeout=10) as client:
        for turn, text in enumerate(args.sentences, 1000):
            started = time.time()
            await client.post("/api/events", json={
                "type": "speech_paused", "source": "fill_test",
                "data": {"text": text, "turn": turn, "is_me": True},
            })
            word = None
            while time.time() - started < 15 and not word:
                try:
                    msg = json.loads(await asyncio.wait_for(feed.recv(), 15 - (time.time() - started)))
                except TimeoutError:
                    break
                if msg.get("kind") == "word" and msg.get("turn") == turn:
                    word = msg
            if word:
                print(f'"{text}..."  ->  {" · ".join(word["words"])}   '
                      f'({word["status"]}, {time.time() - started:.1f}s)')
            else:
                print(f'"{text}..."  ->  (no word offered)')


asyncio.run(main())
