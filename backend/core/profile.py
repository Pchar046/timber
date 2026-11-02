"""The person: mode, needs, phrases, care circle and comfort settings.

Loaded from data/profile.json, which the person edits while calm.
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


class Comfort(BaseModel):
    haptic_only: bool = True           # first touch is a vibration, never a voice
    quiet_hours: str | None = None     # e.g. "22:00-07:00"


class Profile(BaseModel):
    mode: Literal["autism", "dementia"]
    name: str
    needs: list[str] = Field(default_factory=list)
    phrases: list[str] = Field(default_factory=list)
    circle: list[CircleMember] = Field(default_factory=list)
    comfort: Comfort = Field(default_factory=Comfort)


_profile: Profile | None = None


def load(path: Path = PROFILE_PATH) -> Profile:
    global _profile
    _profile = Profile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return _profile


def get() -> Profile:
    return _profile or load()
