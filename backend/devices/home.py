"""The world as tools: nearby devices register themselves, and Timber searches them.

Every device (a lamp, a speaker, a thermostat, a store's quiet-zone light) says
who it is, which room it's in, and which actions it offers. Each action becomes a
Muse Spark tool. Devices can come and go: a registration with `ttl_s` expires
unless it is renewed, so walking out of a room makes its tools disappear.

Actions go to Home Assistant when HA_URL and HA_TOKEN are set and the device has
an entity_id; otherwise a simulated home takes the action, so the demo still
shows the room changing. (HA wiring adapted from the old project's app/home.py.)
"""

import asyncio
import logging
import os
import re
import time
from collections import deque
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

HA_URL = os.getenv("HA_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")


class Param(BaseModel):
    name: str
    type: Literal["integer", "number", "string", "boolean"] = "integer"
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None


class ToolAction(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    description: str
    params: list[Param] = Field(default_factory=list)


class Device(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,30}$")
    name: str
    kind: str                         # light | speaker | thermostat | anything a device announces
    room: str
    actions: list[ToolAction]
    state: dict[str, Any] = Field(default_factory=dict)
    source: str = "simulated"         # simulated | home_assistant | announced
    entity_id: str | None = None      # Home Assistant entity, when there is one
    ttl_s: float | None = None        # None: stays until removed
    last_seen: float = Field(default_factory=time.time)

    def expired(self, now: float) -> bool:
        return self.ttl_s is not None and now - self.last_seen > self.ttl_s


def _pct(description: str) -> Param:
    return Param(name="percent", description=description, minimum=0, maximum=100)


# Standard actions for well-known kinds; announced devices may bring their own.
KIND_ACTIONS: dict[str, list[ToolAction]] = {
    "light": [
        ToolAction(name="set_brightness", description="Set brightness. Lower is calmer and dimmer.",
                   params=[_pct("Brightness, 0-100")]),
        ToolAction(name="turn_off", description="Turn the light fully off."),
    ],
    "speaker": [
        ToolAction(name="pause", description="Pause music or sound that is playing. Makes the room quieter."),
        ToolAction(name="play", description="Resume music or sound."),
        ToolAction(name="set_volume", description="Set the volume. Lower is quieter.",
                   params=[_pct("Volume, 0-100")]),
    ],
    "thermostat": [
        ToolAction(name="set_temperature", description="Set the target temperature in °F.",
                   params=[Param(name="degrees", description="Target °F", minimum=60, maximum=80)]),
    ],
}

# Words a search should connect to each kind (so "sound" finds the speaker).
SYNONYMS = {
    "light": "light lamp bright brightness dim dark glare visual",
    "speaker": "speaker sound music noise volume quiet loud audio tv media",
    "thermostat": "thermostat temperature warm cool heat cold air",
}

# Home Assistant service for each standard action: (domain, service, data from args).
HA_SERVICES = {
    "set_brightness": ("light", "turn_on", lambda a: {"brightness_pct": max(1, int(a["percent"]))}),
    "turn_off": ("light", "turn_off", lambda a: {}),
    "pause": ("media_player", "media_pause", lambda a: {}),
    "play": ("media_player", "media_play", lambda a: {}),
    "set_volume": ("media_player", "volume_set", lambda a: {"volume_level": a["percent"] / 100}),
    "set_temperature": ("climate", "set_temperature", lambda a: {"temperature": a["degrees"]}),
}


# What the Environment agent searches for when the room needs to get calmer.
CALM_QUERY = "dim light brightness lamp lower sound music volume quiet noise speaker"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "device"


class Home:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.log: deque[dict] = deque(maxlen=100)

    # ---------- coming and going ----------
    def register(self, device: Device) -> Device:
        device.last_seen = time.time()
        known = self.devices.get(device.id)
        if known and not device.state:
            device.state = known.state  # a renewal keeps the device's current state
        self.devices[device.id] = device
        if not known:
            self._log(device, "joined", f"{device.name} ({device.room}) is now available")
        return device

    def remove(self, device_id: str) -> bool:
        device = self.devices.pop(device_id, None)
        if device:
            self._log(device, "left", f"{device.name} is no longer available")
        return device is not None

    def present(self) -> list[Device]:
        now = time.time()
        for device in [d for d in self.devices.values() if d.expired(now)]:
            self.devices.pop(device.id)
            self._log(device, "left", f"{device.name} went out of range")
        return list(self.devices.values())

    # ---------- tool search ----------
    def search(self, query: str, k: int = 6) -> list[Device]:
        """Devices present now whose name, kind, room or actions match the query."""
        words = set(re.findall(r"[a-z]+", query.lower()))
        scored = []
        for device in self.present():
            text = " ".join([device.name, device.kind, device.room, SYNONYMS.get(device.kind, ""),
                             *(f"{a.name} {a.description}" for a in device.actions)]).lower()
            score = len(words & set(re.findall(r"[a-z]+", text)))
            if score:
                scored.append((score, device))
        return [d for _, d in sorted(scored, key=lambda s: -s[0])[:k]]

    @staticmethod
    def tools(devices: list[Device]) -> list[dict]:
        """OpenAI-style tool definitions, one per device action."""
        tools = []
        for device in devices:
            for action in device.actions:
                props = {
                    p.name: {"type": p.type, "description": p.description,
                             **({"minimum": p.minimum} if p.minimum is not None else {}),
                             **({"maximum": p.maximum} if p.maximum is not None else {})}
                    for p in action.params
                }
                tools.append({"type": "function", "function": {
                    "name": f"{device.id}__{action.name}",
                    "description": f"{device.name} ({device.kind}, {device.room}): {action.description} "
                                   f"Now: {device.state}",
                    "parameters": {"type": "object", "properties": props,
                                   "required": [p.name for p in action.params]},
                }})
        return tools

    # ---------- acting ----------
    def execute(self, tool: str, args: dict) -> dict:
        """Run one tool call. Refuses tools for devices that aren't here any more."""
        device_id, _, action_name = tool.partition("__")
        device = next((d for d in self.present() if d.id == device_id), None)
        if device is None:
            return {"ok": False, "tool": tool, "error": "device not available"}
        action = next((a for a in device.actions if a.name == action_name), None)
        if action is None:
            return {"ok": False, "tool": tool, "error": "no such action"}
        clean = {}
        for p in action.params:
            if p.name not in args:
                return {"ok": False, "tool": tool, "error": f"missing {p.name}"}
            value = args[p.name]
            if p.type in ("integer", "number"):
                value = float(value)
                value = max(p.minimum if p.minimum is not None else value,
                            min(p.maximum if p.maximum is not None else value, value))
                value = int(value) if p.type == "integer" else value
            clean[p.name] = value

        before = dict(device.state)
        _apply(device, action.name, clean)
        detail = f"{device.name}: {action.name.replace('_', ' ')} {clean or ''}".strip()
        self._log(device, "action", detail)
        if device.entity_id and HA_URL and HA_TOKEN and action.name in HA_SERVICES:
            asyncio.get_running_loop().create_task(self._send_ha(device, action.name, clean))
        return {"ok": True, "tool": tool, "device": device.name, "action": action.name,
                "args": clean, "before": before, "after": dict(device.state)}

    async def _send_ha(self, device: Device, action: str, args: dict) -> None:
        domain, service, data = HA_SERVICES[action]
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{HA_URL}/api/services/{domain}/{service}",
                    json={"entity_id": device.entity_id, **data(args)},
                    headers={"Authorization": f"Bearer {HA_TOKEN}"},
                )
                resp.raise_for_status()
            self._log(device, "sent", f"Home Assistant: {domain}.{service} ok")
        except Exception as exc:
            self._log(device, "error", f"Home Assistant failed: {str(exc)[:120]}")

    def _log(self, device: Device, kind: str, detail: str) -> None:
        self.log.append({"t": time.time(), "device": device.id, "kind": kind, "detail": detail})

    def snapshot(self) -> dict:
        return {
            "home_assistant": bool(HA_URL and HA_TOKEN),
            "devices": [d.model_dump() for d in self.present()],
            "log": list(reversed(self.log))[:30],
        }

    # ---------- the simulated home ----------
    def seed(self) -> None:
        """The demo home. With Home Assistant set up, these drive real entities."""
        ha = bool(HA_URL and HA_TOKEN)
        for name, kind, room, state, entity in [
            ("Living room lamp", "light", "living room", {"on": True, "brightness": 100},
             os.getenv("HA_LIGHT", "light.living_room")),
            ("Living room speaker", "speaker", "living room", {"playing": True, "volume": 60},
             os.getenv("HA_SPEAKER", "media_player.living_room")),
            ("Hallway thermostat", "thermostat", "hallway", {"target": 72},
             os.getenv("HA_THERMOSTAT", "climate.home")),
        ]:
            self.register(Device(id=slug(name), name=name, kind=kind, room=room, state=state,
                                 actions=KIND_ACTIONS[kind], source="home_assistant" if ha else "simulated",
                                 entity_id=entity if ha else None))


def _apply(device: Device, action: str, args: dict) -> None:
    """Update the device's own record of its state."""
    s = device.state
    if action == "set_brightness":
        s.update(brightness=args["percent"], on=args["percent"] > 0)
    elif action == "turn_off":
        s.update(brightness=0, on=False)
    elif action == "pause":
        s["playing"] = False
    elif action == "play":
        s["playing"] = True
    elif action == "set_volume":
        s["volume"] = args["percent"]
    elif action == "set_temperature":
        s["target"] = args["degrees"]
    else:  # an announced device's own action
        s[action] = args or True


home = Home()
