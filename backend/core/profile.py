"""The person: mode, needs, phrases, care circle and comfort settings.

Loaded from data/profile.json, which the person edits while calm. The mode can also
be switched from the apps, which saves it back to the file.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .db import DATA_DIR

PROFILE_PATH = DATA_DIR / "profile.json"


class CircleMember(BaseModel):
    name: str
    relation: str
    sees: list[str] = Field(default_factory=list)  # e.g. alerts, episodes, mood


Mode = Literal["autism", "dementia"]


class Comfort(BaseModel):
    quiet_hours: str | None = None     # e.g. "22:00-07:00"


class ModeSettings(BaseModel):
    """The person's own choices for one mode (unset = the mode's default)."""
    first_touch: Literal["vibrate", "whisper"] | None = None


class Profile(BaseModel):
    mode: Mode
    name: str
    needs: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)
    circle: list[CircleMember] = Field(default_factory=list)
    comfort: Comfort = Field(default_factory=Comfort)
    modes: dict[Mode, ModeSettings] = Field(default_factory=dict)


_profile: Profile | None = None


def load(path: Path = PROFILE_PATH) -> Profile:
    global _profile
    _profile = Profile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return _profile


def get() -> Profile:
    return _profile or load()


def set_mode(mode: Mode, path: Path = PROFILE_PATH) -> Profile:
    person = get()
    person.mode = mode
    path.write_text(person.model_dump_json(indent=2), encoding="utf-8")
    return person
