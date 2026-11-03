"""The two modes: one Mind, two ways of helping.

The mode sets the tone, which agents lead, the first touch, and which actions the Mind
may take at all (see "Two modes, one Mind" in ARCHITECTURE.md). The person can still
change their own first touch in the profile; everything else follows the mode.
"""

from dataclasses import dataclass

from . import profile


@dataclass(frozen=True)
class Mode:
    id: str
    label: str
    leads: tuple[str, ...]      # the agents in front in this mode
    first_touch: str            # vibrate | whisper
    actions: frozenset[str]     # what the Mind may do in this mode
    tone: str                   # how Timber talks, for prompts and for people reading
    whisper: str                # the calm line in the ear: the same words every time
    words: dict[str, str]       # level -> what the phone says
    banner: str                 # the phone's message when load starts to rise
    break_label: str            # the big button

    def public(self) -> dict:
        """What the apps need (no internals)."""
        return {"id": self.id, "label": self.label, "leads": list(self.leads), "first_touch": first_touch(),
                "tone": self.tone, "words": self.words, "banner": self.banner, "break_label": self.break_label,
                "speaks_for_you": "speak_for_you" in self.actions}


MODES = {
    "autism": Mode(
        id="autism", label="Autism mode",
        leads=("sense", "voice", "environment", "guardian"),
        first_touch="vibrate",
        actions=frozenset({"vibrate", "whisper", "notify_phone", "notify_circle", "adjust_home",
                           "speak_for_you", "remember", "none"}),
        tone="Short, literal and calm. No small talk, no exclamation marks.",
        whisper="It's a lot right now. Timber is here.",
        words={"calm": "Calm", "rising": "Getting busy", "overloaded": "A lot right now"},
        banner="Things are getting a lot. Timber is with you.",
        break_label="I need a break",
    ),
    "dementia": Mode(
        id="dementia", label="Dementia mode",
        leads=("memory", "coach", "sense"),
        first_touch="whisper",
        actions=frozenset({"vibrate", "whisper", "notify_phone", "notify_circle", "adjust_home",
                           "remember", "exercise", "none"}),
        tone="Warm and slow. One idea per sentence, their name, familiar words. Never quiz or correct.",
        whisper="{name}, you're safe. Timber is here with you.",
        words={"calm": "All is well", "rising": "Let's slow down", "overloaded": "Timber is with you"},
        banner="Take a slow breath. You're safe, and help is on the way.",
        break_label="I'd like some help",
    ),
}


def current() -> Mode:
    return MODES[profile.get().mode]


def first_touch() -> str:
    """The person's own choice for this mode, else the mode's default."""
    chosen = profile.get().modes.get(profile.get().mode)
    return (chosen and chosen.first_touch) or current().first_touch


def whisper_line() -> str:
    return current().whisper.format(name=profile.get().name)
