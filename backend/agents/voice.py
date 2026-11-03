"""Voice: speaks for the person when their speech has switched off.

Proof-carrying speech, in four steps:
  1. Draft   Muse Spark writes at most two sentences, each citing ONE source: one of the
             person's own phrases (P1..) or a "may say" rule from their contract (C..).
  2. Rules   deterministic checks drop any sentence with a never-say word or medical word.
  3. Verify  a second Muse Spark call checks each sentence against its cited source and the
             contract; anything not proven is dropped. (No verifier available: only
             sentences that are the source word for word survive.)
  4. Speak   "This is Timber, speaking for <name>." plus what survived, sent to the apps.
Timber never guesses the person's answer: with no source for it, it says they can't
answer right now and offers another way.
"""

import json
import logging
import re
import time

from ..core import contract, db, echo, profile
from ..meta import spark
from .guardian import guardian
from .mind import mind

log = logging.getLogger(__name__)

MAX_SENTENCES = 2

DRAFT_SYSTEM = """You are Voice, part of Timber, a daily-living support tool. {name} is overloaded
and can't speak right now. {speaker} just said to {name}: "{heard}"
Write what Timber should say back: at most 2 short sentences, about {name} in the third person.
Use ONLY these sources. Every sentence cites exactly one source id and says nothing beyond it:
{sources}
Never guess what {name} wants, thinks or feels. If answering needs {name}'s own choice and no
source gives it, say {name} can't answer right now and offer another way from the sources.
No introduction: Timber adds "This is Timber, speaking for {name}." itself.
JSON: {{"sentences": [{{"text": "...", "source": "P1"}}]}}"""

VERIFY_SYSTEM = """You check sentences before Timber says them out loud for {name}, who can't speak
right now. A sentence is ok ONLY if all of these hold:
1. Its meaning is fully supported by its cited source text. Rephrasing and third person are
   fine; adding answers, facts, preferences or feelings is not.
2. It breaks none of {name}'s never-say rules: {never}
3. It contains no medical information, diagnosis or advice.
JSON: {{"checks": [{{"i": 0, "ok": true, "why": "short reason"}}]}}"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def sources() -> dict[str, str]:
    """What Timber may draw on when speaking: the person's phrases and "may say" rules."""
    found = {}
    phrases = [p for p in profile.get().phrases if not p.lower().startswith("this is timber")]
    for i, phrase in enumerate(phrases, 1):
        found[f"P{i}"] = phrase
    for rule in contract.active():
        if rule.kind == "may_say" and "speak_for_you" not in rule.actions:
            found[f"C{rule.id}"] = rule.statement
    return found


def fallback_draft(srcs: dict[str, str]) -> list[dict]:
    """Without a model: 'can't talk right now' plus 'another way', word for word."""
    picks = []
    for want in ("can't", "write"):
        sid = next((k for k, v in srcs.items() if want in v.lower() and k not in picks), None)
        if sid:
            picks.append(sid)
    picks = picks or list(srcs)[:1]
    return [{"text": srcs[sid], "source": sid} for sid in picks]


class Voice:
    async def draft(self, heard: str, speaker: str, srcs: dict[str, str]) -> tuple[list[dict], str]:
        name = profile.get().name
        system = DRAFT_SYSTEM.format(
            name=name, speaker=speaker, heard=heard,
            sources="\n".join(f"{k}: {v}" for k, v in srcs.items()),
        )
        out = await spark.ask_json("voice: draft reply", system, f'Reply to: "{heard}"',
                                   {"sentences": fallback_draft(srcs)}, reasoning_effort="minimal")
        sentences = [s for s in out.get("sentences") or []
                     if isinstance(s, dict) and isinstance(s.get("text"), str) and s.get("source") in srcs]
        return sentences[:MAX_SENTENCES] or fallback_draft(srcs), out["_status"]

    async def verify(self, sentences: list[dict], srcs: dict[str, str]) -> tuple[list[dict], list[dict], str]:
        """Returns (kept, dropped, verifier status). Each dropped item says why."""
        kept, dropped = [], []
        for s in sentences:  # deterministic checks first: cheap and certain
            ok, why = guardian.check_outgoing(s["text"], audience=None)
            (kept if ok else dropped).append(s if ok else {**s, "why": why})

        # The person's own words, word for word, are proven by construction.
        verbatim = [s for s in kept if _norm(s["text"]) == _norm(srcs[s["source"]])]
        reworded = [s for s in kept if s not in verbatim]
        if not reworded:
            return verbatim, dropped, "verbatim"

        name = profile.get().name
        never = [r.statement for r in contract.active() if r.kind == "never_say"] or ["(none)"]
        items = [{"i": i, "text": s["text"], "source_id": s["source"], "source_text": srcs[s["source"]]}
                 for i, s in enumerate(reworded)]
        out = await spark.ask_json(
            "voice: verify reply", VERIFY_SYSTEM.format(name=name, never="; ".join(never)),
            json.dumps(items), {"checks": []}, reasoning_effort="minimal",
        )
        if out["_status"] != "ok":  # no verifier: rewordings can't be proven, so they go
            dropped += [{**s, "why": "unverified rewording"} for s in reworded]
            return verbatim, dropped, out["_status"]

        verdicts = {c.get("i"): c for c in out.get("checks") or [] if isinstance(c, dict)}
        proven = []
        for i, s in enumerate(reworded):
            check = verdicts.get(i)
            if check and check.get("ok") is True:
                proven.append(s)
            else:
                dropped.append({**s, "why": (check or {}).get("why", "not verified")})
        return [s for s in kept if s in verbatim or s in proven], dropped, "ok"

    async def speak_for(self, decision: dict) -> dict:
        name = profile.get().name
        heard = decision["data"].get("heard", "")
        speaker = decision["data"].get("speaker", "Someone")
        episode = decision["data"].get("episode")

        # The opener needs no model (it's the contract's own rule), so it goes out at once
        # and covers the few seconds the reply takes to draft and check.
        opener = f"This is Timber, speaking for {name}."
        echo.record(opener)
        mind.feed.publish({"kind": "say", "part": "opener", "text": opener, "heard": heard,
                           "speaker": speaker, "episode": episode, "t": time.time()})

        srcs = sources()
        draft, draft_status = await self.draft(heard, speaker, srcs)
        kept, dropped, verify_status = await self.verify(draft, srcs)
        if not kept:  # everything failed: fall back to a phrase that passes the rules
            kept = [s for s in fallback_draft(srcs) if guardian.check_outgoing(s["text"], None)[0]][:1]

        reply = " ".join(s["text"] for s in kept)
        said = f"{opener} {reply}".strip()
        echo.record(reply)
        result = {
            "kind": "say", "part": "reply", "text": reply, "said": said, "heard": heard, "speaker": speaker,
            "episode": episode,
            "sentences": [{"text": opener, "receipt": "contract: Timber says it is Timber speaking"}]
                         + [{"text": s["text"], "receipt": f'{s["source"]}: "{srcs[s["source"]]}"'} for s in kept],
            "dropped": dropped, "status": {"draft": draft_status, "verify": verify_status}, "t": time.time(),
        }
        db.execute(
            "INSERT INTO spoken (episode, heard, said, sentences, dropped, status, t) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (result["episode"], heard, said, json.dumps(result["sentences"]), json.dumps(dropped),
             json.dumps(result["status"]), result["t"]),
        )
        mind.feed.publish(result)
        log.info("spoke for %s: %s", name, said)
        return result

    async def run(self) -> None:
        queue = mind.feed.subscribe()
        try:
            while True:
                message = await queue.get()
                if message["kind"] == "decision" and message["decision"]["action"] == "speak_for_you":
                    try:
                        await self.speak_for(message["decision"])
                    except Exception:
                        log.exception("speaking failed")
        finally:
            mind.feed.unsubscribe(queue)


voice = Voice()
