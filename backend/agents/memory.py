"""Memory: what was really said, and where the person is in their day.

- Records. Every finished turn the Ears hear is saved: who, what, when, source. Muse
  Spark then marks the key moments and links the people, places and things in them
  (the memory graph). The camera adds records the same way (Step 20).
- Answers. "Timber, what did I say this morning?" is answered ONLY from records. Each
  sentence cites the records it rests on and is checked against them before it's said;
  with no record, Timber says so instead of guessing.
- Orientation. The same words every day, built from the clock, the devices nearby and
  the day's written plan, each sentence with its receipt: "Good morning, Rose. It's
  Monday, November 2nd. You're home. Maria visits at 6 this evening." No model, so it
  can't drift. In Dementia mode it's said each morning and whenever asked.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta

from ..core import db, echo, modes, profile
from ..core.db import DATA_DIR
from ..core.events import Event, bus
from ..devices.home import home
from ..meta import spark
from .mind import mind

log = logging.getLogger(__name__)

SCHEDULE_PATH = DATA_DIR / "schedule.json"
ORIENT_AT = os.getenv("TIMBER_ORIENT_AT", "08:00")  # Dementia mode: the morning orientation
LOOKBACK_DAYS = 3
MAX_RECORDS = 150
MIN_WORDS_TO_EXTRACT = 4
LABEL = re.compile(r"^(speaker\s*)?[a-z?]$|^(you|me|i|timber|someone)$", re.I)  # not real names

WAKE = re.compile(r"^\W*(?:hey |ok |okay )?timber\b[\s,.!?:-]*", re.I)
ORIENT_Q = re.compile(r"\b(what day|what's the date|what is the date|what date|where am i|what time|"
                      r"what's happening today|what am i doing today)\b", re.I)

EXTRACT_SYSTEM = """You keep a memory for {name}. Read one thing that was said and return JSON:
{{"key": true if it's worth remembering later (a plan, a name, a place, where something was put,
a decision, a request, something the speaker said they felt), else false,
"summary": "one short third-person sentence of what was said, adding nothing",
"people": [names], "places": [places], "objects": [things]}}
Use only the words given. Never guess what anyone thinks."""

ASK_SYSTEM = """You are Timber's memory for {name}. It is now {now}.
Answer {name}'s question using ONLY the records below. Talk to {name} as "you", warmly, in at
most 2 short sentences. Every sentence cites the record ids it rests on. Quote what was said
when you can. Never guess what anyone thought or meant. If no record answers the question,
return no sentences.
Records (oldest first) as [id] time, who (source): what was said
{records}
JSON: {{"sentences": [{{"text": "...", "records": ["<id>"]}}]}}"""

VERIFY_SYSTEM = """Check each sentence against the records it cites. ok ONLY if every fact in it
(who, what, when) is stated in those records. Rewording and "you" for {name} are fine;
adding anything is not.
JSON: {{"checks": [{{"i": 0, "ok": true, "why": "short reason"}}]}}"""


# ---------- little helpers ----------
def clock(t: float) -> str:
    """'9:14 AM'"""
    d = datetime.fromtimestamp(t)
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def when(t: float) -> str:
    """'this morning at 9:14 AM', 'yesterday at 6:02 PM', 'on Saturday at 10:00 AM'"""
    d = datetime.fromtimestamp(t)
    days = (date.today() - d.date()).days
    if days == 0:
        part = "this morning" if d.hour < 12 else "this afternoon" if d.hour < 17 else "this evening"
        return f"{part} at {clock(t)}"
    return f"{'yesterday' if days == 1 else 'on ' + d.strftime('%A')} at {clock(t)}"


def ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


COMMON = set("""the and you your my mine was were are did does what when where who why how say said tell told
about this that these those morning afternoon evening today yesterday tonight timber have has had
for with from into put get got going want there their they them would could should will can just""".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 2}


def _row(r: dict) -> dict:
    return {**r, "key": bool(r["key"]), "links": json.loads(r["links"]), "when": when(r["t"])}


def line(r: dict) -> str:
    """How a record reads to Muse: camera records are photos the person took."""
    who = "a photo you took" if r["source"] == "camera" else f'{r["who"]} ({r["source"]})'
    return f'[{r["id"]}] {r["when"]}, {who}: {r["text"]}'


def receipt(r: dict) -> str:
    return f'{r["source"]} {clock(r["t"])}, {r["who"]}: "{r["text"]}"'


# ---------- records ----------
def save(text: str, who: str, source: str, kind: str = "said", t: float | None = None) -> dict:
    record = {"id": uuid.uuid4().hex[:8], "t": t or time.time(), "who": who, "text": text.strip(),
              "source": source, "kind": kind, "key": 0, "summary": None, "links": "[]"}
    db.execute("INSERT INTO memories (id, t, who, text, source, kind, key, summary, links) "
               "VALUES (:id, :t, :who, :text, :source, :kind, :key, :summary, :links)", record)
    return _row(record)


def records(days: int = LOOKBACK_DAYS, limit: int = MAX_RECORDS, include_questions: bool = False) -> list[dict]:
    since = time.time() - days * 86400
    rows = db.query("SELECT * FROM memories WHERE t >= ? " + ("" if include_questions else "AND kind != 'question' ")
                    + "ORDER BY t DESC LIMIT ?", (since, limit))
    return [_row(r) for r in reversed(rows)]


def _node(kind: str, name: str) -> str:
    found = db.query("SELECT id FROM memory_nodes WHERE type = ? AND name = ?", (kind, name))
    if found:
        return found[0]["id"]
    node_id = f"{kind[:2]}{uuid.uuid4().hex[:6]}"
    db.execute("INSERT OR IGNORE INTO memory_nodes (id, type, name) VALUES (?, ?, ?)", (node_id, kind, name))
    return db.query("SELECT id FROM memory_nodes WHERE type = ? AND name = ?", (kind, name))[0]["id"]


async def extract(record: dict) -> None:
    """Mark key moments and link the people, places and things in them."""
    if len(record["text"].split()) < MIN_WORDS_TO_EXTRACT:
        return
    out = await spark.ask_json("memory: key moment", EXTRACT_SYSTEM.format(name=profile.get().name),
                               f'{record["who"]} said: "{record["text"]}"',
                               {"key": False, "summary": None, "people": [], "places": [], "objects": []},
                               reasoning_effort="minimal", timeout=20)
    if out["_status"] != "ok":
        return
    links = []
    for field, kind in (("people", "person"), ("places", "place"), ("objects", "object")):
        for name in out.get(field) or []:
            if isinstance(name, str) and 0 < len(name.strip()) <= 60 and not LABEL.match(name.strip()):
                links.append(_node(kind, name.strip()))
    summary = out.get("summary") if isinstance(out.get("summary"), str) else None
    db.execute("UPDATE memories SET key = ?, summary = ?, links = ? WHERE id = ?",
               (int(out.get("key") is True), summary, json.dumps(sorted(set(links))), record["id"]))


def graph() -> dict:
    nodes = db.query("SELECT * FROM memory_nodes")
    recs = records(days=30, limit=500)
    return {"nodes": nodes, "records": recs,
            "edges": [{"record": r["id"], "node": n} for r in recs for n in r["links"]]}


# ---------- answering from records ----------
def _window(question: str) -> tuple[float, float] | None:
    q = question.lower()
    today = datetime.combine(date.today(), datetime.min.time()).timestamp()
    for words, start, end in ((("this morning",), 0, 12), (("this afternoon",), 12, 17),
                              (("this evening", "tonight"), 17, 24), (("today",), 0, 24),
                              (("yesterday",), -24, 0)):
        if any(w in q for w in words):
            return today + start * 3600, today + end * 3600
    return None


def fallback_answer(question: str, recs: list[dict]) -> list[dict]:
    """Without a model: quote the best-matching records word for word."""
    window = _window(question)
    pool = [r for r in recs if not window or window[0] <= r["t"] < window[1]]
    if re.search(r"\b(did i|i said|i say|have i)\b", question.lower()):
        pool = [r for r in pool if r["who"] == "You" or r["source"] == "camera"] or pool
    asked = _words(question) - COMMON
    if asked:
        scored = [(len(asked & _words(r["text"])), r) for r in pool]
        matches = [r for n, r in sorted(scored, key=lambda x: (-x[0], -x[1]["t"])) if n]
        pool = matches or ([] if not window else pool)
    picks, seen = [], set()
    for r in sorted(pool, key=lambda r: -r["t"]):  # newest first, each thing said once
        if r["text"].lower() not in seen and len(picks) < 2:
            seen.add(r["text"].lower())
            picks.append(r)
    return [{"text": f'{r["when"][0].upper()}{r["when"][1:]}, '
                     + (f'a photo you took showed: "{r["text"]}"' if r["source"] == "camera" else
                        f'{"you" if r["who"] == "You" else r["who"]} said: "{r["text"]}"'), "records": [r["id"]]}
            for r in reversed(picks)]


async def ask(question: str) -> dict:
    name = profile.get().name
    recs = records()
    by_id = {r["id"]: r for r in recs}
    started = time.time()
    lines = "\n".join(line(r) for r in recs)
    backup = fallback_answer(question, recs)
    out = await spark.ask_json(
        "memory: answer from records",
        ASK_SYSTEM.format(name=name, now=datetime.now().strftime("%A %I:%M %p"), records=lines or "(none)"),
        question, {"sentences": backup}, reasoning_effort="minimal", timeout=30)
    drafted = [{"text": s["text"].strip(), "records": [i for i in s.get("records") or [] if i in by_id]}
               for s in out.get("sentences") or [] if isinstance(s, dict) and isinstance(s.get("text"), str)]
    drafted = [s for s in drafted if s["text"] and s["records"]][:2]  # no receipt, not said

    status = {"draft": out["_status"], "verify": "skipped"}
    if out["_status"] == "ok" and drafted:
        kept, status["verify"] = await verify(drafted, by_id)
    else:
        kept = backup  # quotes are proven by construction
    if out["_status"] == "ok" and not kept and drafted:
        kept = backup  # the model's version failed its check: fall back to quotes

    sentences, said = [], set()
    for s in kept:
        if s["text"].lower() in said:
            continue
        said.add(s["text"].lower())
        sentences.append({"text": s["text"], "receipts": list(dict.fromkeys(receipt(by_id[i]) for i in s["records"]))})
    answer = " ".join(s["text"] for s in sentences) or "I don't have a record of that."
    result = {"kind": "answer", "question": question, "answer": answer, "sentences": sentences,
              "status": status, "ms": round((time.time() - started) * 1000), "t": time.time()}
    echo.record(answer)
    mind.feed.publish(result)
    return result


async def verify(sentences: list[dict], by_id: dict) -> tuple[list[dict], str]:
    items = [{"i": i, "sentence": s["text"], "records": [line(by_id[r]) for r in s["records"]]}
             for i, s in enumerate(sentences)]
    out = await spark.ask_json("memory: verify answer", VERIFY_SYSTEM.format(name=profile.get().name),
                               json.dumps(items), {"checks": []}, reasoning_effort="minimal", timeout=20)
    if out["_status"] != "ok":
        return [], out["_status"]
    ok = {c.get("i") for c in out.get("checks") or [] if isinstance(c, dict) and c.get("ok") is True}
    return [s for i, s in enumerate(sentences) if i in ok], "ok"


# ---------- orientation ----------
def schedule_today() -> list[dict]:
    if not SCHEDULE_PATH.exists():
        return []
    today = datetime.now().strftime("%a").lower()
    items = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8")).get("items", [])
    return sorted((i for i in items if {"daily", today} & {d.lower()[:3] if d != "daily" else d
                                                          for d in i.get("days", ["daily"])}),
                  key=lambda i: i["time"])


def _at(hhmm: str) -> str:
    hour, minute = (int(x) for x in hhmm.split(":"))
    part = "this morning" if hour < 12 else "this afternoon" if hour < 17 else "this evening"
    return f"{hour % 12 or 12}{f':{minute:02d}' if minute else ''} {part}"


def orientation(now: datetime | None = None) -> dict:
    """The same words every day; each sentence carries where it came from."""
    now = now or datetime.now()
    name = profile.get().name
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    stamp = now.strftime("%Y-%m-%d %H:%M")
    sentences = [
        {"text": f"{greeting}, {name}.", "receipt": f"clock: {stamp}"},
        {"text": f"It's {now:%A}, {now:%B} {ordinal(now.day)}.", "receipt": f"clock: {stamp}"},
    ]
    present = home.present()
    visiting = [d for d in present if d.ttl_s]
    if visiting:  # devices that come and go mean we're somewhere else
        room = visiting[0].room
        sentences.append({"text": f"You're at the {room}.", "receipt": f"nearby: {visiting[0].name} ({room})"})
    elif present:
        sentences.append({"text": "You're home.",
                          "receipt": "home devices: " + ", ".join(d.name for d in present[:3])})
    upcoming = [i for i in schedule_today() if i["time"] >= now.strftime("%H:%M")][:2]
    for item in upcoming:
        sentences.append({"text": f"{item['text']} at {_at(item['time'])}.",
                          "receipt": f"today's plan: {item['time']} {item['text']}"})
    return {"kind": "orient", "text": " ".join(s["text"] for s in sentences), "sentences": sentences,
            "mode": profile.get().mode, "t": time.time()}


class Memory:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("memory task failed", exc_info=task.exception())

    def orient(self, why: str) -> dict:
        message = {**orientation(), "why": why}
        echo.record(message["text"])
        mind.feed.publish(message)
        save(message["text"], "Timber", "orientation", kind="timber")
        return message

    def on_event(self, event: Event) -> None:
        if event.type == "gesture" and event.data.get("kind") == "lost":
            self.orient("you asked where you are")
            return
        if event.type != "speech_complete":
            return
        text = (event.data.get("text") or "").strip()
        if not text or echo.is_echo(text):
            return
        mine = event.data.get("is_me")
        who = "You" if mine else event.data.get("speaker") or "Someone"
        if mine is not False and WAKE.match(text):  # "Timber, ..." is a question for Timber
            question = WAKE.sub("", text).strip() or text
            save(question, "You" if mine else who, "heard", kind="question", t=event.t)
            if ORIENT_Q.search(question):
                self.orient("you asked")
            else:
                self._spawn(ask(question))
            return
        record = save(text, who, "heard", t=event.t)
        self._spawn(extract(record))

    async def mornings(self) -> None:
        hour, minute = (int(x) for x in ORIENT_AT.split(":"))
        while True:
            now = datetime.now()
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due <= now:
                due += timedelta(days=1)
            await asyncio.sleep((due - now).total_seconds())
            if modes.current().id == "dementia":
                self.orient("good morning")

    async def run(self) -> None:
        queue = bus.subscribe()
        self._spawn(self.mornings())
        try:
            while True:
                event = await queue.get()
                try:
                    self.on_event(event)
                except Exception:
                    log.exception("memory step failed")
        finally:
            bus.unsubscribe(queue)
            for task in self._tasks:
                task.cancel()


memory = Memory()
