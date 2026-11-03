"""Shared safety checks. Timber supports daily living: no medical language, ever."""

import re

# Last line of defence for "no medical advice" in anything a model writes.
MEDICAL = re.compile(
    r"\b(panic|anxiety|attack|symptom|diagnos|disorder|condition|syndrome|tachycardi|"
    r"arrhythmi|seizure|medic|treatment|therapy|therapist|doctor|dose|prescri|"
    r"meltdown|shutdown|cardiac|blood pressure|clinical|patient)",
    re.I,
)


def medical_term(text: str) -> str | None:
    """The first medical-sounding word in `text`, or None if it reads clean."""
    match = MEDICAL.search(text or "")
    return match.group(0) if match else None
