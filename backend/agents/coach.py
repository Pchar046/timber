"""Coach: progress (Autism mode) and gentle brain activities (Dementia mode).

Progress is counted straight from saved episodes (no model), so every number is a fact
the person can check. The words are gentle either way: a harder week is said plainly,
never as a failure.

Activities come from the person's OWN key memories. Muse Spark turns one into a short
question; its answer must be the record's own words (checked), so nothing is invented.
Muse Image paints a picture card ahead of time. The first time the answer is shown
with the question (errorless learning); after each "I remember" the gap grows, and
"Show me" simply goes back to showing the answer.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta

import httpx

from ..core import db, modes, profile
from ..core.db import DATA_DIR
from ..meta import image, spark
from .guardian import CALMING, guardian  # CALMING: the actions the episode estimate can credit
from .memory import when
from .mind import mind

log = logging.getLogger(__name__)

CARDS_DIR = DATA_DIR / "cards"


def _minutes(ep: dict) -> float:
    return (ep["end"] - ep["start"]) / 60


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _say_minutes(m: float) -> str:
    return "under a minute" if m < 1 else f"{m:.0f} min"


def progress(days: int = 14) -> dict:
    """Episodes per day, average length, this week vs last, and which helps worked best."""
    today = date.today()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(min(first, today - timedelta(days=13)), datetime.min.time()).timestamp()
    rows = db.query("SELECT start, end, actions, helped FROM episodes "
                    "WHERE end IS NOT NULL AND start >= ? ORDER BY start", (since,))
    episodes = [{**r, "actions": json.loads(r["actions"]), "helped": json.loads(r["helped"] or "[]"),
                 "day": datetime.fromtimestamp(r["start"]).date()} for r in rows]

    per_day = []
    for i in range(days):
        d = first + timedelta(days=i)
        lengths = [_minutes(e) for e in episodes if e["day"] == d]
        per_day.append({"date": d.isoformat(), "episodes": len(lengths), "avg_minutes": _avg(lengths)})

    def week(back: int) -> dict:
        start, end = today - timedelta(days=6 + 7 * back), today - timedelta(days=7 * back)
        lengths = [_minutes(e) for e in episodes if start <= e["day"] <= end]
        return {"episodes": len(lengths), "avg_minutes": _avg(lengths)}

    this, last = week(0), week(1)

    helps = []
    for action in CALMING:
        used = [e for e in episodes if action in e["actions"]]
        if not used:
            continue
        helped = [e for e in used if action in e["helped"]]
        helps.append({"action": action, "used": len(used), "helped": len(helped),
                      "avg_minutes_when_used": _avg([_minutes(e) for e in used])})
    helps.sort(key=lambda h: (-h["helped"], h["avg_minutes_when_used"] or 0))

    return {"days": per_day, "this_week": this, "last_week": last,
            "helps": helps, "story": story(this, last)}


def story(this: dict, last: dict) -> list[str]:
    """One or two plain sentences about the trend, only from the counts."""
    lines = []
    if this["episodes"] == 0 and last["episodes"] == 0:
        return ["No episodes in the last two weeks."]
    if last["episodes"] == 0:
        lines.append(f"{this['episodes']} episode{'s' if this['episodes'] != 1 else ''} this week. "
                     "Next week you'll see how it compares.")
    elif this["episodes"] < last["episodes"]:
        lines.append(f"Fewer episodes than last week: {this['episodes']}, down from {last['episodes']}.")
    elif this["episodes"] > last["episodes"]:
        lines.append(f"A busier week: {this['episodes']} episodes, up from {last['episodes']}. "
                     "That's useful to know, not a failure.")
    else:
        lines.append(f"The same number of episodes as last week ({this['episodes']}).")
    a, b = this["avg_minutes"], last["avg_minutes"]
    if a is not None and b is not None and abs(a - b) >= 0.5:
        lines.append(f"Episodes are {'shorter' if a < b else 'longer'} on average: "
                     f"{_say_minutes(a)}, {'down' if a < b else 'up'} from {_say_minutes(b)}.")
    return lines


# =====================================================================================
# Brain activities (Dementia mode): spaced retrieval from the person's own memories
# =====================================================================================
INTERVALS = [600, 3600, 86400, 3 * 86400, 7 * 86400]  # gaps after each "I remember"
RETRY_S = 600          # after "Show me": back to answer-first, ten minutes later
PER_DAY = 3            # two or three a day, never a drill
LOOKBACK_DAYS = 14
PRACTICE_AT = os.getenv("TIMBER_PRACTICE_AT", "10:30,15:00")  # Dementia mode nudges, local time

ACTIVITY_SYSTEM = """You make one gentle memory activity for {name} from one of {name}'s own records.
Tone: {tone}
Return JSON:
{{"ok": true or false (false for small talk, anything upsetting, private or about health),
"question": "a short, warm question to {name} as 'you', e.g. 'Where did you put your keys?'",
"answer": "the answer, COPIED WORD FOR WORD from the record (a few words)",
"card": "a description for a soft painted illustration of the scene: objects and places only,
no people, no faces, no text or letters"}}
The answer must be words that appear in the record, and must NOT appear in the question.
Ask about what, where or when, from what was said. Ask "who" only if a person's name is in
the record's words. If someone else said it (who is not "You"), never say "you said".
Never add facts."""

FITS_SYSTEM = """Does the answer actually answer the question, using the record? JSON {"fits": true or false}"""

CARD_STYLE = ("Soft, warm watercolour illustration, simple shapes, calm colours, plenty of space. "
              "No people, no faces, no text, no letters. Scene: ")


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _public_activity(a: dict) -> dict:
    record = db.query("SELECT * FROM memories WHERE id = ?", (a["record"],))
    r = record[0] if record else None
    return {
        "id": a["id"], "question": a["question"], "answer": a["answer"], "stage": a["stage"],
        "show_answer_first": a["stage"] == 0,
        "card": f"/api/cards/{a['card']}" if a["card"] else None,
        "receipt": f'{r["source"]} {r["who"]}: "{r["text"]}"' if r else None,
        "from": datetime.fromtimestamp(r["t"]).strftime("%A %d %B").replace(" 0", " ") if r else None,
    }


class Coach:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("coach task failed", exc_info=task.exception())

    # ---------- making activities ----------
    async def make(self, record: dict) -> dict | None:
        """One activity from one record. The answer is checked against the record's own words."""
        name = profile.get().name
        backup = {"ok": True, "question": f"Do you remember what was said {when(record['t'])}?",
                  "answer": record["text"], "card": record.get("summary") or record["text"]}
        out = await spark.ask_json("coach: make activity",
                                   ACTIVITY_SYSTEM.format(name=name, tone=modes.MODES["dementia"].tone),
                                   json.dumps({"who": record["who"], "when": when(record["t"]), "said": record["text"],
                                               "source": record["source"]}),
                                   backup, reasoning_effort="minimal", timeout=30)
        if out.get("ok") is not True:
            return None
        question, answer = str(out.get("question") or "").strip(), str(out.get("answer") or "").strip(" .\"'")
        if not question or not answer or _norm(answer) not in _norm(record["text"]):  # proof: the record says it
            log.info("activity dropped: answer %r isn't in record %s", answer, record["id"])
            return None
        if _norm(answer) in _norm(question):  # nothing to remember if the question gives it away
            log.info("activity dropped: question %r gives away %r", question, answer)
            return None
        if out["_status"] == "ok":  # Muse wrote it, so a second look: does the answer fit the question?
            fits = await spark.ask_json("coach: check activity", FITS_SYSTEM,
                                        json.dumps({"record": record["text"], "question": question, "answer": answer}),
                                        {"fits": False}, reasoning_effort="minimal", timeout=20)
            if fits.get("fits") is not True:
                log.info("activity dropped: %r doesn't answer %r", answer, question)
                return None
        if not all(guardian.check_outgoing(t, None)[0] for t in (question, answer)):
            return None
        activity = {"id": uuid.uuid4().hex[:8], "record": record["id"], "question": question[:200],
                    "answer": answer[:200], "card_prompt": str(out.get("card") or answer)[:300], "card": None,
                    "stage": 0, "due": time.time(), "created": time.time(), "last": None,
                    "last_result": None, "status": "active"}
        db.execute("INSERT INTO activities (id, record, question, answer, card_prompt, card, stage, due, created, "
                   "last, last_result, status) VALUES (:id, :record, :question, :answer, :card_prompt, :card, "
                   ":stage, :due, :created, :last, :last_result, :status)", activity)
        self._spawn(self.paint(activity))  # ahead of time: never on the live path
        return activity

    async def paint(self, activity: dict) -> None:
        """Muse Image makes the picture card (~15 s), saved next to the memories."""
        out = await image.generate(CARD_STYLE + activity["card_prompt"], task="coach: picture card", size="1024x1024")
        data = out["image"]
        if data.startswith("http"):
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.get(data)
                r.raise_for_status()
                data = f"data:{r.headers.get('content-type', 'image/png')};base64,{base64.b64encode(r.content).decode()}"
        match = re.match(r"data:image/([\w+]+);base64,(.+)", data, re.S)
        if not match:
            return
        ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(match.group(1), match.group(1))
        CARDS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{activity['id']}.{ext}"
        (CARDS_DIR / name).write_bytes(base64.b64decode(match.group(2)))
        db.execute("UPDATE activities SET card = ? WHERE id = ?", (name, activity["id"]))
        mind.feed.publish({"kind": "card_ready", "activity": activity["id"], "card": f"/api/cards/{name}",
                           "status": out["_status"]})

    async def build(self, want: int = PER_DAY) -> list[dict]:
        """Top up to `want` active activities from recent key memories not used yet."""
        active = db.query("SELECT count(*) AS n FROM activities WHERE status = 'active'")[0]["n"]
        used = {r["record"] for r in db.query("SELECT record FROM activities")}
        since = time.time() - LOOKBACK_DAYS * 86400
        candidates = [r for r in db.query("SELECT * FROM memories WHERE key = 1 AND kind IN ('said', 'seen') "
                                          "AND t >= ? ORDER BY t DESC", (since,)) if r["id"] not in used]
        seen_text, made = set(), []
        for record in candidates:
            if active + len(made) >= want:
                break
            if _norm(record["text"]) in seen_text:
                continue
            seen_text.add(_norm(record["text"]))
            activity = await self.make(record)
            if activity:
                made.append(activity)
        return made

    # ---------- today ----------
    def done_today(self) -> int:
        start = datetime.combine(date.today(), datetime.min.time()).timestamp()
        return db.query("SELECT count(*) AS n FROM activities WHERE last >= ?", (start,))[0]["n"]

    def today(self) -> dict:
        left = max(0, PER_DAY - self.done_today())
        due = db.query("SELECT * FROM activities WHERE status = 'active' AND due <= ? ORDER BY due LIMIT ?",
                       (time.time(), left))
        upcoming = db.query("SELECT min(due) AS t FROM activities WHERE status = 'active' AND due > ?",
                            (time.time(),))[0]["t"]
        return {"activities": [_public_activity(a) for a in due], "done_today": PER_DAY - left,
                "per_day": PER_DAY, "next_due": upcoming}

    def result(self, activity_id: str, remembered: bool) -> dict | None:
        """'I remember' lengthens the gap; 'Show me' goes back to answer-first, gently."""
        found = db.query("SELECT * FROM activities WHERE id = ?", (activity_id,))
        if not found:
            return None
        a = found[0]
        if a["stage"] == 0 or remembered:  # stage 0 is errorless: seeing the answer counts as a step
            stage = min(a["stage"] + 1, len(INTERVALS))
            gap = INTERVALS[stage - 1]
        else:
            stage, gap = 0, RETRY_S
        status = "learned" if stage == len(INTERVALS) and remembered else "active"
        now = time.time()
        db.execute("UPDATE activities SET stage = ?, due = ?, last = ?, last_result = ?, status = ? WHERE id = ?",
                   (stage, now + gap, now, "remembered" if remembered else "shown", status, activity_id))
        return {"id": activity_id, "stage": stage, "next_in_s": gap, "status": status}

    # ---------- practice times (Dementia mode) ----------
    async def practice(self) -> None:
        times = sorted(t.strip() for t in PRACTICE_AT.split(",") if t.strip())
        while True:
            now = datetime.now()
            slots = [now.replace(hour=int(t[:2]), minute=int(t[3:5]), second=0, microsecond=0) for t in times]
            due = min((s if s > now else s + timedelta(days=1)) for s in slots)
            await asyncio.sleep((due - now).total_seconds())
            if modes.current().id != "dementia" or mind.episode is not None:
                continue  # never during an episode
            await self.build()
            today = self.today()
            if today["activities"]:
                mind.decide("exercise", "calm", f"Practice time: {len(today['activities'])} gentle "
                            "activities from your own memories.", activities=[a["id"] for a in today["activities"]])

    async def run(self) -> None:
        self._spawn(self.practice())
        try:
            await asyncio.Event().wait()
        finally:
            for task in self._tasks:
                task.cancel()


coach = Coach()
