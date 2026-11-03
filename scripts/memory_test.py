"""Try Memory without a microphone, against a running Timber server.

    py scripts/memory_test.py

Plays a short conversation as if the Ears heard it, then asks Timber about it (by
"voice", starting with "Timber, ...") and prints each answer with its receipts. Also
prints today's orientation. Open /person or /listen to see the same answers there.
"""

import argparse
import asyncio
import json
import time

import httpx
import websockets

CONVERSATION = [
    ("You", True, "I put my keys in the blue bowl by the front door."),
    ("Speaker B", False, "Do you want to get lunch at Rosa's on Thursday?"),
    ("You", True, "Yes, Thursday works, let's meet at noon."),
    ("Speaker B", False, "Great, I'll bring the photos from the beach trip."),
]
QUESTIONS = [
    "Timber, what did I say about my keys?",
    "Timber, when am I meeting for lunch?",
    "Timber, what did I say about my sister's birthday?",  # nothing on record: Timber must say so
]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    async with websockets.connect(args.url.replace("http", "ws", 1) + "/ws/person") as feed, \
            httpx.AsyncClient(base_url=args.url, timeout=60) as client:

        async def say(who: str, is_me: bool, text: str) -> None:
            await client.post("/api/events", json={"type": "speech_complete", "source": "memory_test", "data": {
                "text": text, "speaker": who, "is_me": is_me, "label": "A" if is_me else "B"}})

        print("Conversation:")
        for who, is_me, text in CONVERSATION:
            await say(who, is_me, text)
            print(f"  {who}: {text}")
        await asyncio.sleep(1)

        for question in QUESTIONS:
            started = time.time()
            await say("You", True, question)
            answer = None
            while time.time() - started < 60 and not answer:
                msg = json.loads(await asyncio.wait_for(feed.recv(), 60))
                if msg.get("kind") == "answer":
                    answer = msg
            print(f"\n{question}\n  -> {answer['answer']}   ({time.time() - started:.1f}s, {answer['status']})")
            for s in answer["sentences"]:
                for r in s["receipts"]:
                    print(f"     receipt: {r}")

        orient = (await client.get("/api/orientation")).json()
        print(f"\nOrientation: {orient['text']}")
        for s in orient["sentences"]:
            print(f"     receipt: {s['receipt']}")


asyncio.run(main())
