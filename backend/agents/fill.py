"""Fill: offers the word the person is reaching for.

When the person stops mid-sentence (the Listener publishes `speech_paused`), Muse Spark
guesses the missing word from the sentence so far and the last few turns of the
conversation. The phone shows it with a gentle tap. Only the person sees it (or hears
it, if they turn that on): it's a suggestion for them to use or ignore, never said to
anyone else, so it can't put words in their mouth.
"""

import asyncio
import logging
import re
import time
from collections import deque

from ..core import echo, profile
from ..core.events import Event, bus
from ..meta import spark
from .guardian import guardian
from .mind import mind

log = logging.getLogger(__name__)

MAX_WORDS = 3
CONTEXT_TURNS = 6

SYSTEM = """You help {name}, who sometimes loses a word in the middle of a sentence.
{name} said: "{text}" and stopped.
Recent conversation, oldest first:
{context}
Suggest the word or short phrase (1 to 3 words) {name} is most likely reaching for to carry
on the sentence. Up to 3 options, most likely first. Use the conversation for clues.
If the sentence is already complete or there's no sensible guess, return an empty list.
Never suggest anything medical.
JSON: {{"words": ["...", "..."]}}"""

# Without a model: a few everyday endings, so the demo still shows the idea.
DEMO = {
    "pass the": ["salt", "water", "remote"],
    "can i have": ["a minute", "some water", "a break"],
    "i need": ["a break", "a minute", "quiet"],
    "where is my": ["phone", "keys", "bag"],
    "turn off the": ["light", "music", "TV"],
}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z']+", text.lower()))


def demo_words(text: str) -> list[str]:
    said = _norm(text)
    return next((words for ending, words in DEMO.items() if said.endswith(ending)), [])


def clean(words: object, text: str) -> list[str]:
    """Short, distinct, safe suggestions that don't just repeat the last word."""
    last = (_norm(text).split() or [""])[-1]
    out: list[str] = []
    for word in words if isinstance(words, list) else []:
        if not isinstance(word, str):
            continue
        word = word.strip(" .,!?…\"'")
        key = _norm(word)
        if (not key or len(key.split()) > 4 or len(word) > 30 or key == last
                or key in (_norm(w) for w in out) or not guardian.check_outgoing(word, None)[0]):
            continue
        out.append(word)
    return out[:MAX_WORDS]


class Fill:
    def __init__(self) -> None:
        self.context: deque[str] = deque(maxlen=CONTEXT_TURNS)
        self._tasks: set[asyncio.Task] = set()

    async def suggest(self, data: dict) -> dict | None:
        text = data.get("text", "").strip()
        if not text or echo.is_echo(text):  # never fill in Timber's own words
            return None
        name = profile.get().name
        started = time.time()
        system = SYSTEM.format(name=name, text=text, context="\n".join(self.context) or "(nothing yet)")
        out = await spark.ask_json("fill: missing word", system, f'"{text}"',
                                   {"words": demo_words(text)}, reasoning_effort="minimal", timeout=10)
        words = clean(out.get("words"), text)
        if not words:
            log.info("no word to offer for %r (%s)", text, out["_status"])
            return None
        message = {"kind": "word", "heard": text, "words": words, "turn": data.get("turn"),
                   "status": out["_status"], "ms": round((time.time() - started) * 1000), "t": time.time()}
        mind.feed.publish(message)
        log.info("offered %s for %r", words, text)
        return message

    def on_event(self, event: Event) -> None:
        if event.type == "speech_complete":
            self.context.append(f'{event.data.get("speaker", "Someone")}: {event.data.get("text", "")}')
        elif event.type == "speech_paused":
            task = asyncio.create_task(self.suggest(event.data))  # don't hold up the next event
            self._tasks.add(task)
            task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("word fill failed", exc_info=task.exception())

    async def run(self) -> None:
        queue = bus.subscribe()
        try:
            while True:
                self.on_event(await queue.get())
        finally:
            bus.unsubscribe(queue)
            for task in self._tasks:
                task.cancel()


fill = Fill()
