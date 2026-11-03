"""Reflector: the nightly review, on Muse Wonder.

Once a night (and on demand) it looks back over one day and:
  1. updates the personal baseline from the day's calm readings (plain arithmetic, small
     steps, so one odd day can't move it far),
  2. writes the care-circle daily summary (checked like every outgoing message),
  3. treats each of Timber's actions as an experiment and PROPOSES playbook changes.
Proposals change nothing on their own: they wait on /contract until the person's calm
self approves them, and only then become contract rules. Every proposal cites the
episodes it rests on; one that cites nothing real is dropped.
"""

import asyncio
import json
import logging
import os
import statistics
import time
from datetime import date, datetime, timedelta

from ..core import contract, db, profile
from ..core.state import Baseline, state
from ..meta import wonder
from .guardian import CIRCLE, guardian

log = logging.getLogger(__name__)

REFLECT_AT = os.getenv("TIMBER_REFLECT_AT", "02:00")  # local time; reviews the day before
BLEND = 0.2                # how far one day moves the baseline
MIN_CALM_READINGS = 30     # a signal needs this many calm readings in a day to count
EPISODE_MARGIN_S = (60, 120)  # readings this close before/after an episode aren't "calm"
MAX_PROPOSALS = 3
PLAYBOOK = ("helps", "avoid")           # the rule kinds the Reflector may propose
NEVER_AVOID = ("notify_circle",)        # the safety net stays, whatever the data says
CALMING = ("vibrate", "whisper", "notify_phone", "adjust_home", "speak_for_you")
HELPED_WORDS = {"vibrate": "a gentle vibration", "whisper": "a calm word", "notify_phone": "a phone alert",
                "adjust_home": "a calmer room", "speak_for_you": "Timber speaking for them"}

SYSTEM = """You are the Reflector in Timber, a daily-living support tool for {name} ({mode} mode).
Once a night you review one whole day. Treat each of Timber's actions as an experiment: which ones
came before episodes that ended sooner, and which ones {name} stopped, overrode or blocked
(gestures such as "break" or "speech_on", and actions the contract blocked).
Return JSON with:
- summary: for the care circle (family, partners). At most 60 words, about {name} by name, never
  "you". Kind and factual, only from the data. No medical words, no advice, no diagnosis, and no
  guessing at thoughts or feelings beyond the mood estimates given. Say lengths and times in
  plain words ("under a minute", "around 7 PM"), and don't repeat the date.
- patterns: up to 3 short observations, each citing the episode ids it rests on:
  [{{"text": "...", "evidence": ["<episode id>"]}}]
- proposals: up to 3 changes to {name}'s playbook, which {name} approves or declines in the
  morning. Either
  {{"change": "add", "kind": "helps" or "avoid", "statement": "<one first-person sentence {name}
  could have written, e.g. 'A gentle vibration helps me first.'>", "actions": [from {acting}],
  "why": "<one sentence from the evidence>", "evidence": ["<episode id>", ...]}}
  or {{"change": "remove", "rule_id": "<id of an existing helps/avoid rule>", "why": "...",
  "evidence": [...]}}.
  Only propose what the day's data supports, citing at least one episode. Don't repeat existing
  rules, pending proposals or proposals {name} declined. Never propose avoiding notify_circle.
  An empty list is a good answer when nothing stands out."""


# ---------- gathering the day ----------
def _bounds(day: date) -> tuple[float, float]:
    start = datetime.combine(day, datetime.min.time()).timestamp()
    return start, start + 86400


def _clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")


def gather(day: date) -> dict:
    """Everything the Reflector may look at for one day, already trimmed to what matters."""
    since, until = _bounds(day)
    now = time.time()
    episodes = []
    for r in db.query("SELECT * FROM episodes WHERE start >= ? AND start < ? ORDER BY start", (since, until)):
        end = r["end"] or now
        episodes.append({
            "id": r["id"], "start": _clock(r["start"]), "minutes": round((end - r["start"]) / 60, 1),
            "ongoing": r["end"] is None, "peak_level": r["peak_level"],
            "started_by": "the person" if r["trigger"] == "you" else "readings",
            "actions": json.loads(r["actions"]), "helped_estimate": json.loads(r["helped"] or "[]"),
            "mood_estimate": r["mood"], "_span": (r["start"], end),
        })

    decisions = db.query("SELECT action, data, t FROM decisions WHERE t >= ? AND t < ?", (since, until))
    counts: dict[str, int] = {}
    blocked = []
    for d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1
        data = json.loads(d["data"])
        if d["action"] == "none" and data.get("rules"):
            blocked.append({"at": _clock(d["t"]), "action": data.get("blocked"), "episode": data.get("episode")})

    events = db.query("SELECT type, data, t FROM events WHERE t >= ? AND t < ? AND type IN "
                      "('gesture', 'speech_paused', 'reading') ORDER BY t", (since, until))
    gestures = [{"at": _clock(e["t"]), "kind": json.loads(e["data"]).get("kind")}
                for e in events if e["type"] == "gesture"]
    spans = [(a - EPISODE_MARGIN_S[0], b + EPISODE_MARGIN_S[1]) for a, b in (ep["_span"] for ep in episodes)]
    calm: dict[str, list[float]] = {}
    for e in events:
        if e["type"] != "reading" or any(a <= e["t"] <= b for a, b in spans):
            continue
        for name, value in json.loads(e["data"]).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                calm.setdefault(name, []).append(float(value))

    spoken = db.query("SELECT heard, said FROM spoken WHERE t >= ? AND t < ? ORDER BY t DESC LIMIT 10",
                      (since, until))
    rules = [{"id": r.id, "kind": r.kind, "statement": r.statement, "actions": r.actions}
             for r in contract.active() if r.kind in PLAYBOOK]
    earlier = [{"status": p["status"], **json.loads(p["rule"])}
               for p in db.query("SELECT status, rule FROM proposals WHERE status IN ('pending', 'declined')")]
    return {
        "day": day.isoformat(),
        "episodes": episodes,
        "decision_counts": counts,
        "blocked_by_contract": blocked[:20],
        "gestures": gestures[:30],
        "spoken_for_person": spoken,
        "words_offered": sum(1 for e in events if e["type"] == "speech_paused"),
        "playbook_rules": rules,
        "earlier_proposals": earlier[-20:],
        "_calm": calm,
    }


# ---------- 1. baseline ----------
def update_baseline(calm: dict[str, list[float]]) -> dict:
    """Nudge each learned baseline toward the day's calm readings. Returns before/after."""
    changes = {}
    for name, values in calm.items():
        base = state.baselines.get(name)
        if not base or not base.ready or len(values) < MIN_CALM_READINGS:
            continue
        mean = base.mean * (1 - BLEND) + statistics.fmean(values) * BLEND
        std = base.std * (1 - BLEND) + statistics.stdev(values) * BLEND
        after = Baseline(base.n, mean, std ** 2 * (base.n - 1))
        state.baselines[name] = after
        db.execute("INSERT OR REPLACE INTO baseline (signal, n, mean, m2) VALUES (?, ?, ?, ?)",
                   (name, after.n, after.mean, after.m2))
        changes[name] = {"before": {"mean": round(base.mean, 1), "std": round(base.std, 1)},
                         "after": {"mean": round(after.mean, 1), "std": round(after.std, 1)},
                         "calm_readings": len(values)}
    return changes


# ---------- 2 + 3. without a model ----------
def fallback(day: dict) -> dict:
    name = profile.get().name
    eps = day["episodes"]
    if not eps:
        summary = f"{name} had a calm day: no episodes."
    else:
        total = sum(e["minutes"] for e in eps)
        summary = f"{name} had {len(eps)} episode{'s' if len(eps) > 1 else ''} today, {total:.0f} minutes in all."
    helped: dict[str, list[str]] = {}
    for e in eps:
        for action in e["helped_estimate"]:
            helped.setdefault(action, []).append(e["id"])
    if helped:
        top = max(helped, key=lambda a: len(helped[a]))
        summary += f" What seemed to help most: {HELPED_WORDS.get(top, top)}."
    proposals = [
        {"change": "add", "kind": "helps", "actions": [action],
         "statement": f"{HELPED_WORDS[action][0].upper()}{HELPED_WORDS[action][1:]} helps me.",
         "why": f"It seemed to help in {len(ids)} episodes today.", "evidence": ids}
        for action, ids in helped.items() if len(ids) >= 2 and action in HELPED_WORDS
    ]
    return {"summary": summary, "patterns": [], "proposals": proposals}


# ---------- keeping proposals honest ----------
def _same(a: dict, b: dict) -> bool:
    return a.get("kind") == b.get("kind") and sorted(a.get("actions") or []) == sorted(b.get("actions") or [])


def vet(raw: object, day: dict) -> list[dict]:
    """Keep only proposals that cite real episodes, fit the contract and aren't repeats."""
    ids = {e["id"] for e in day["episodes"]}
    rules = {r["id"]: r for r in day["playbook_rules"]}
    taken = [*day["playbook_rules"], *day["earlier_proposals"]]
    kept = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        evidence = [e for e in p.get("evidence") or [] if e in ids]
        why = str(p.get("why") or "")[:240]
        if not evidence or not why:
            continue
        if p.get("change") == "remove":
            rule = rules.get(p.get("rule_id"))
            if rule:
                kept.append({"change": "remove", "rule": rule, "why": why, "evidence": evidence})
            continue
        rule = {"kind": p.get("kind"), "statement": str(p.get("statement") or "").strip()[:200],
                "actions": [a for a in p.get("actions") or [] if a in contract.ACTING]}
        if (p.get("change") != "add" or rule["kind"] not in PLAYBOOK or not rule["statement"]
                or not rule["actions"] or len(rule["actions"]) != len(p.get("actions") or [])
                or (rule["kind"] == "avoid" and set(rule["actions"]) & set(NEVER_AVOID))
                or any(_same(rule, t) for t in taken + [k["rule"] for k in kept])
                or not guardian.check_outgoing(f"{rule['statement']} {why}", None)[0]):
            continue
        kept.append({"change": "add", "rule": rule, "why": why, "evidence": evidence})
    return kept[:MAX_PROPOSALS]


class Reflector:
    def __init__(self) -> None:
        self.running: asyncio.Task | None = None

    async def reflect(self, day: date | None = None) -> dict:
        day = day or date.today()
        started = time.time()
        facts = gather(day)
        baseline = update_baseline(facts.pop("_calm"))
        for e in facts["episodes"]:
            e.pop("_span")
        name = profile.get().name
        backup = fallback(facts)
        out = await wonder.reflect(
            "reflector: nightly review",
            SYSTEM.format(name=name, mode=profile.get().mode, acting=list(contract.ACTING)),
            json.dumps({**facts, "baseline_update": baseline}),
            backup,
        )
        status = out["_status"]

        summary = str(out.get("summary") or "").strip()[:600]
        if not summary or not guardian.check_outgoing(summary, CIRCLE)[0]:
            summary = backup["summary"]
        ids = {e["id"] for e in facts["episodes"]}
        patterns = [{"text": str(p.get("text"))[:240], "evidence": [e for e in p.get("evidence") or [] if e in ids]}
                    for p in out.get("patterns") or [] if isinstance(p, dict) and p.get("text")]
        patterns = [p for p in patterns if p["evidence"] and guardian.check_outgoing(p["text"], CIRCLE)[0]][:3]
        proposals = vet(out.get("proposals"), facts)
        if status != "ok" and not proposals:
            proposals = vet(backup["proposals"], facts)

        now = time.time()
        ms = round((now - started) * 1000)
        db.execute("INSERT INTO reflections (day, summary, patterns, baseline, status, ms, t) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (facts["day"], summary, json.dumps(patterns), json.dumps(baseline), status, ms, now))
        reflection_id = db.query("SELECT max(id) AS id FROM reflections")[0]["id"]
        for p in proposals:
            db.execute("INSERT INTO proposals (reflection, change, rule, why, evidence, status, t) "
                       "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                       (reflection_id, p["change"], json.dumps(p["rule"]), p["why"], json.dumps(p["evidence"]), now))
        result = latest()
        guardian.feed.publish({"kind": "reflection", "reflection": result})
        log.info("reflected on %s in %d ms (%s): %d proposals", facts["day"], ms, status, len(proposals))
        return {**result, "proposals": self.proposals("pending")}

    def start(self) -> bool:
        """Run a review of today in the background. False if one is already running."""
        if self.running and not self.running.done():
            return False
        self.running = asyncio.create_task(self.reflect())
        self.running.add_done_callback(_log_failure)
        return True

    # ---------- the person's approval ----------
    def proposals(self, status: str | None = None) -> list[dict]:
        rows = db.query("SELECT * FROM proposals" + (" WHERE status = ?" if status else "") + " ORDER BY t DESC",
                        (status,) if status else ())
        return [{**r, "rule": json.loads(r["rule"]), "evidence": json.loads(r["evidence"])} for r in rows]

    def decide(self, proposal_id: int, approve: bool) -> dict | None:
        found = [p for p in self.proposals("pending") if p["id"] == proposal_id]
        if not found:
            return None
        p = found[0]
        if approve:
            if p["change"] == "remove":
                contract.remove(p["rule"]["id"])
            else:
                when = datetime.now().strftime("%b %d").replace(" 0", " ")
                rule = contract.Rule(kind=p["rule"]["kind"], statement=p["rule"]["statement"],
                                     actions=p["rule"]["actions"], mode=profile.get().mode,
                                     source=f"{p['rule']['statement']} (approved from Timber's nightly review, {when})")
                contract.get().rules.append(rule)
                contract.save()
        status = "approved" if approve else "declined"
        db.execute("UPDATE proposals SET status = ?, decided = ? WHERE id = ?", (status, time.time(), proposal_id))
        return {**p, "status": status}

    # ---------- nightly ----------
    async def run(self) -> None:
        hour, minute = (int(x) for x in REFLECT_AT.split(":"))
        while True:
            now = datetime.now()
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due <= now:
                due += timedelta(days=1)
            await asyncio.sleep((due - now).total_seconds())
            try:
                await self.reflect(date.today() - timedelta(days=1))
            except Exception:
                log.exception("nightly review failed")


def latest() -> dict | None:
    rows = db.query("SELECT * FROM reflections ORDER BY t DESC LIMIT 1")
    if not rows:
        return None
    r = rows[0]
    return {**r, "patterns": json.loads(r["patterns"]), "baseline": json.loads(r["baseline"])}


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("review failed", exc_info=task.exception())


reflector = Reflector()
