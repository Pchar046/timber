"""Echo guard: the microphone hears Timber's own voice. Don't mistake it for someone else."""

import re
import time
from collections import deque
from difflib import SequenceMatcher

WINDOW_S = 30
_said: deque[tuple[float, str]] = deque(maxlen=20)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def record(text: str) -> None:
    """Call with everything Timber says out loud."""
    _said.append((time.time(), _norm(text)))


def is_echo(heard: str) -> bool:
    """Was this probably Timber's own recent speech, picked up by the mic?"""
    heard = _norm(heard)
    if not heard:
        return False
    now = time.time()
    for t, said in _said:
        if now - t <= WINDOW_S and (heard in said or SequenceMatcher(None, heard, said).ratio() > 0.6):
            return True
    return False
