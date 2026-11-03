"""Environment: changes the room to lower the load, using whatever devices are here now.

On `adjust_home` it searches the devices present right now, hands only those to
Muse Spark as tools, and carries out the calls it makes. When the episode ends
(`remember`), it puts every device it touched back the way it was.
"""

import logging

from ..core import contract
from ..devices.home import CALM_QUERY, Device, home
from ..meta import spark
from .mind import mind

log = logging.getLogger(__name__)

MAX_CALLS = 6

SYSTEM = """You are Environment, part of Timber, a daily-living support tool for an autistic adult.
Their sensory load is {level} right now: {why}.
Use the tools to make the room calmer: gentler light and less sound. Choose the gentlest
changes that will help: dim lights to about 30% rather than turning them off, and pause or
lower music rather than anything drastic. Leave temperature alone unless it is clearly part
of the problem. The tools are the devices present right now; use only those, at most once each.
The person's own rules (follow them):
{rules}"""


def gentle_plan(devices: list[Device]) -> list[dict]:
    """The rule-based plan used without a model: dim lights to 30%, pause what's playing."""
    calls = []
    for d in devices:
        names = {a.name for a in d.actions}
        if "set_brightness" in names and d.state.get("brightness", 100) > 30:
            calls.append({"name": f"{d.id}__set_brightness", "arguments": {"percent": 30}})
        elif "pause" in names and d.state.get("playing"):
            calls.append({"name": f"{d.id}__pause", "arguments": {}})
    return calls


def undo_plan(device: Device, before: dict) -> list[dict]:
    """Calls that put a standard device back to `before`, for whatever actually changed."""
    actions = {a.name for a in device.actions}
    now = device.state
    changed = {k for k in before if before[k] != now.get(k)}
    calls = []
    if "set_brightness" in actions and changed & {"brightness", "on"}:
        calls.append(("set_brightness", {"percent": before["brightness"]}) if before.get("on", True)
                     else ("turn_off", {}))
    if "set_volume" in actions and "volume" in changed:
        calls.append(("set_volume", {"percent": before["volume"]}))
    if {"play", "pause"} <= actions and "playing" in changed:
        calls.append(("play", {}) if before["playing"] else ("pause", {}))
    if "set_temperature" in actions and "target" in changed:
        calls.append(("set_temperature", {"degrees": before["target"]}))
    return [{"name": f"{device.id}__{name}", "arguments": args} for name, args in calls]


class Environment:
    def __init__(self) -> None:
        self._before: dict[str, dict] = {}  # device id -> state before this episode's changes

    async def calm(self, level: str, why: str) -> dict:
        devices = home.search(CALM_QUERY)
        if not devices:
            result = {"status": "no devices", "results": []}
            mind.feed.publish({"kind": "home", **result})
            return result
        rules = "\n".join(f"- {s}" for s in contract.guidance("adjust_home")) or "- (none)"
        messages = [
            {"role": "system", "content": SYSTEM.format(level=level, why=why, rules=rules)},
            {"role": "user", "content": "Make the room calmer now."},
        ]
        out = await spark.chat(
            "environment: calm the room", messages,
            {"content": "", "tool_calls": gentle_plan(devices)},
            tools=home.tools(devices), reasoning_effort="minimal",  # same choices, ~2x faster
        )
        results = []
        for call in out["tool_calls"][:MAX_CALLS]:
            device = home.devices.get(call["name"].partition("__")[0])
            if device is not None:
                self._before.setdefault(device.id, dict(device.state))
            results.append(home.execute(call["name"], call.get("arguments") or {}))
        result = {"status": out["_status"], "results": results}
        mind.feed.publish({"kind": "home", **result})
        return result

    def restore(self) -> list[dict]:
        results = []
        for device_id, before in self._before.items():
            device = home.devices.get(device_id)
            if device is None:
                continue  # it left; nothing to put back
            for call in undo_plan(device, before):
                results.append(home.execute(call["name"], call["arguments"]))
        self._before.clear()
        if results:
            mind.feed.publish({"kind": "home", "status": "restored", "results": results})
        return results

    async def run(self) -> None:
        queue = mind.feed.subscribe()
        try:
            while True:
                message = await queue.get()
                if message["kind"] != "decision":
                    continue
                decision = message["decision"]
                try:
                    if decision["action"] == "adjust_home":
                        await self.calm(decision["level"], decision["data"].get("why", decision["reason"]))
                    elif decision["action"] == "remember":
                        self.restore()
                except Exception:
                    log.exception("environment step failed")
        finally:
            mind.feed.unsubscribe(queue)


environment = Environment()
