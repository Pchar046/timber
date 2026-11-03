"""The Calm-Self Contract: rules the person writes while calm.

When the person can't confirm anything (speech gone, overloaded), the contract
stands in for that confirmation. Every rule keeps the person's own words as its
receipt. Muse Spark turns plain words into structured rules; agents call
`check()` before acting.
"""

import json
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..meta import spark
from . import profile
from .db import DATA_DIR
from .events import Action

CONTRACT_PATH = DATA_DIR / "contract.json"

RuleKind = Literal["may_say", "never_say", "contact", "helps", "avoid"]
Level = Literal["rising", "overloaded", "any"]
ACTING = ("vibrate", "whisper", "notify_phone", "notify_circle", "adjust_home", "speak_for_you")


class Contact(BaseModel):
    name: str
    relation: str = ""
    how: str = "app"            # app | text | call
    when: Level = "rising"      # the first level at which to tell them


class Rule(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: RuleKind
    statement: str                                  # the rule, clearly, in the first person
    audience: str = "anyone"                        # anyone | strangers | care circle | a name
    actions: list[Action] = Field(default_factory=list)  # avoid/never_say: forbids; others: supports
    words: list[str] = Field(default_factory=list)  # never_say: words that must not be spoken
    contact: Contact | None = None
    mode: Literal["any", "autism", "dementia"] = "any"  # the mode this rule belongs to
    source: str                                     # the person's own words (the receipt)
    t: float = Field(default_factory=time.time)


class Contract(BaseModel):
    updated: float = Field(default_factory=time.time)
    rules: list[Rule] = Field(default_factory=list)


class Verdict(BaseModel):
    allowed: bool
    reason: str
    rules: list[str] = Field(default_factory=list)  # ids of the rules that decided it


# ---------- storage ----------
_contract: Contract | None = None


def active() -> list[Rule]:
    """The rules for the mode the person is in now."""
    mode = profile.get().mode
    return [r for r in get().rules if r.mode in ("any", mode)]


def get() -> Contract:
    global _contract
    if _contract is None:
        if CONTRACT_PATH.exists():
            _contract = Contract.model_validate_json(CONTRACT_PATH.read_text(encoding="utf-8"))
        else:
            _contract = Contract()
    return _contract


def save() -> None:
    contract = get()
    contract.updated = time.time()
    CONTRACT_PATH.write_text(contract.model_dump_json(indent=2), encoding="utf-8")


def remove(rule_id: str) -> bool:
    contract = get()
    before = len(contract.rules)
    contract.rules = [r for r in contract.rules if r.id != rule_id]
    if len(contract.rules) == before:
        return False
    save()
    return True


# ---------- plain words -> structured rules ----------
SYSTEM = f"""You turn a person's own words into rules for Timber, a daily-living support tool
that acts for them when they are overloaded and can't speak. They wrote these rules while calm;
Timber must follow them exactly when they can't confirm.
Return JSON {{"rules": [...]}}. One sentence may hold several rules: split them. Each rule:
- kind: "may_say" (Timber may say this for them), "never_say" (Timber must never say or reveal
  this), "contact" (someone to contact), "helps" (something that helps them), "avoid" (something
  Timber must not do).
- statement: the rule in one clear first-person sentence ("Never tell strangers I'm autistic.").
- audience: who it applies to: "anyone", "strangers", "care circle", or a person's name.
- actions: Timber actions it FORBIDS (avoid, never_say) or SUPPORTS (helps, may_say, contact),
  from {list(ACTING)}. For "avoid", list an action only if they want Timber not to do it at
  all; partial preferences ("not too dark") stay in the statement only.
- words: never_say only. Words or short phrases that must never be spoken, with obvious
  variants (e.g. "autistic", "autism").
- contact: contact only. {{"name", "relation", "how": "app|text|call",
  "when": "rising|overloaded|any"}}.
Use only what the person said. Do not invent rules. Never add medical advice."""


def _demo_rule(text: str) -> dict:
    """Keyword guess for demo mode; marked so nobody mistakes it for Muse's work."""
    lower = text.lower()
    if any(w in lower for w in ("never say", "never tell", "don't tell", "do not tell", "don't say")):
        kind = "never_say"
    elif any(w in lower for w in ("call ", "text ", "contact", "tell my", "message")):
        kind = "contact"
    elif "help" in lower:
        kind = "helps"
    elif any(w in lower for w in ("may say", "can say", "you can tell")):
        kind = "may_say"
    else:
        kind = "avoid"
    return {"kind": kind, "statement": text.strip()}


async def structure(text: str) -> tuple[list[Rule], str]:
    """Turn the person's words into rules (not yet saved). Returns (rules, model status)."""
    out = await spark.ask_json(
        "contract: structure rule", SYSTEM, text, {"rules": [_demo_rule(text)]}
    )
    rules = []
    for raw in out.get("rules") or []:
        if not isinstance(raw, dict):
            continue
        try:
            rules.append(Rule.model_validate({**raw, "source": text.strip()}))
        except ValidationError:
            continue  # drop anything that doesn't fit the contract
    return rules, out["_status"]


async def add(text: str) -> tuple[list[Rule], str]:
    rules, status = await structure(text)
    if rules:
        get().rules.extend(rules)
        save()
    return rules, status


# ---------- checks agents run before acting ----------
LEVELS = {"rising": 1, "overloaded": 2}


def _applies(rule: Rule, audience: str | None) -> bool:
    """Unknown audience: assume the rule applies (the safe side)."""
    return audience is None or rule.audience.lower() in ("anyone", audience.lower())


def check(
    action: str,
    level: str | None = None,
    text: str | None = None,
    to: list[str] | None = None,
    audience: str | None = None,
) -> Verdict:
    """May Timber take `action` (optionally saying `text` to `audience`, or telling `to`) now?"""
    for rule in active():
        if rule.kind == "avoid" and action in rule.actions:
            return Verdict(allowed=False, reason=f'you said: "{rule.source}"', rules=[rule.id])
        if rule.kind == "never_say" and text and _applies(rule, audience):
            hit = next((w for w in rule.words if w.lower() in text.lower()), None)
            if hit:
                return Verdict(allowed=False, reason=f'it would say "{hit}", and you said: "{rule.source}"',
                               rules=[rule.id])
    support = [r for r in active() if r.kind in ("helps", "may_say", "contact") and action in r.actions]
    if to:  # cite the rules for the people actually being told
        support = [r for r in support if r.contact and r.contact.name in to] or support
    if support:
        return Verdict(allowed=True, reason=f'you said: "{support[0].source}"', rules=[r.id for r in support])
    return Verdict(allowed=True, reason="no rule against it")


def contacts(level: str) -> list[Rule]:
    """Contact rules due at this level (a rule for "rising" also applies when overloaded)."""
    now = LEVELS.get(level, 0)
    return [
        r for r in active()
        if r.kind == "contact" and r.contact
        and (r.contact.when == "any" or LEVELS.get(r.contact.when, 1) <= now)
    ]


def guidance(action: str) -> list[str]:
    """Statements an agent should keep in mind for this action (for its prompt)."""
    return [r.statement for r in active() if action in r.actions or r.kind in ("helps", "avoid")]
