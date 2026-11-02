"""Play a rising-load scenario against a running Timber server.

    py scripts/scenario.py                # http://127.0.0.1:8000
    py scripts/scenario.py --url http://192.168.1.20:8000

Teaches a calm baseline first (if needed), then climbs: calm -> rising ->
overloaded (you stay silent, and a cashier asks a question) -> calm again.
Prints the Sense level after each phase and the Mind's decisions at the end.
"""

import argparse
import random
import time

import httpx

SPEECH_SILENCE_S = 10  # matches backend/agents/mind.py

PHASES = [
    # name, heart_rate, noise_db, movement, seconds
    ("calm", 72, 48, 0.1, 5),
    ("rising", 90, 74, 0.3, 6),
    ("overloaded", 116, 88, 0.6, SPEECH_SILENCE_S + 2),
    ("calm again", 74, 50, 0.1, 6),
]
CASHIER = {"speaker": "Cashier", "is_me": False, "text": "Do you need a bag?"}


def send(client: httpx.Client, hr: float, noise: float, move: float) -> None:
    jitter = random.uniform
    client.post("/api/devices/ingest", json=[
        {"device": "scenario watch", "metric": "heart_rate", "value": round(hr + jitter(-2, 2))},
        {"device": "scenario mic", "metric": "noise_db", "value": round(noise + jitter(-2, 2), 1)},
        {"device": "scenario phone", "metric": "movement", "value": move},
    ]).raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between readings")
    args = parser.parse_args()

    with httpx.Client(base_url=args.url, timeout=30) as client:
        hr = client.get("/api/state").json()["signals"].get("heart_rate")
        learned = hr["baseline"]["readings"] if hr else 0
        if learned < 30:
            print(f"Teaching a calm baseline ({30 - learned} readings)...")
            for _ in range(30 - learned):
                send(client, 72, 48, 0.1)
                time.sleep(0.05)
        started = time.time()

        for name, hr, noise, move, seconds in PHASES:
            print(f"\n== {name}: heart rate ~{hr}, noise ~{noise} dB")
            end = time.time() + seconds
            while time.time() < end:
                send(client, hr, noise, move)
                time.sleep(args.delay)
            if name == "overloaded":
                print(f'   Cashier: "{CASHIER["text"]}"')
                client.post("/api/events", json={
                    "type": "speech_complete", "source": "scenario", "data": CASHIER})
                time.sleep(0.5)
            current = client.get("/api/sense").json()["current"] or {}
            print(f"   level: {current.get('level')}  ({current.get('source')})")
            print(f"   reason: {current.get('reason')}")

        print("\n== The Mind's decisions")
        decisions = client.get("/api/decisions", params={"limit": 50}).json()["decisions"]
        for d in reversed(decisions):
            if d["t"] >= started:
                print(f"   {d['action']:<14} {d['agent']:<12} {d['level']:<11} {d['reason']}")


if __name__ == "__main__":
    main()
