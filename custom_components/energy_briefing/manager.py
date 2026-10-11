"""Runtime manager: generates, stores and delivers briefings."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .briefing import BriefingBuilder
from .const import (
    CONF_CLICK_PATH,
    CONF_FALLBACK_AI_TASK,
    CONF_MODEL,
    CONF_NOTIFY,
    CONF_PROVIDER,
    CONF_TIMES_WEEKDAY,
    CONF_TIMES_WEEKEND,
    DEFAULT_CLICK_PATH,
    DEFAULT_TIMES_WEEKDAY,
    DEFAULT_TIMES_WEEKEND,
    EVENT_BRIEFING,
    PROVIDER_NONE,
)
from .llm import LLMClient, LLMError, async_generate_ai_task

_LOGGER = logging.getLogger(__name__)


def parse_times(value: str | None) -> list[tuple[int, int]]:
    """Parse '08:00, 12:00' into [(8, 0), (12, 0)]."""
    out: list[tuple[int, int]] = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        hour, _, minute = part.partition(":")
        out.append((int(hour), int(minute or 0)))
    return out


class BriefingManager:
    """Owns the latest briefing for a config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.options = {**entry.options}
        self.config = {**entry.data, **entry.options}
        self.builder = BriefingBuilder(hass, entry.entry_id, self.options, self.config)
        self.client = LLMClient(hass, self.config)
        self.latest: dict[str, Any] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def async_schedule(self) -> None:
        weekday = parse_times(self.options.get(CONF_TIMES_WEEKDAY, DEFAULT_TIMES_WEEKDAY))
        weekend = parse_times(self.options.get(CONF_TIMES_WEEKEND, DEFAULT_TIMES_WEEKEND))

        for hour, minute in sorted(set(weekday + weekend)):
            on_weekday = (hour, minute) in weekday
            on_weekend = (hour, minute) in weekend

            async def _run(now, wd=on_weekday, we=on_weekend) -> None:
                is_weekend = dt_util.as_local(now).weekday() >= 5
                if (is_weekend and we) or (not is_weekend and wd):
                    await self.async_generate(notify=True)

            self.entry.async_on_unload(
                async_track_time_change(self.hass, _run, hour=hour, minute=minute, second=0)
            )

    async def async_generate(self, kind: str | None = None, notify: bool = False, targets: list[str] | None = None) -> dict[str, Any]:
        async with self._lock:
            built = await self.builder.async_build(kind)
            system, prompt = self.builder.prompt(built)
            source = "template"
            text = None
            provider = self.config.get(CONF_PROVIDER, PROVIDER_NONE)
            if provider != PROVIDER_NONE:
                try:
                    text = await self.client.async_generate(system, prompt)
                    model = self.config.get(CONF_MODEL)
                    source = f"{provider}/{model}" if model else provider
                except LLMError as err:
                    _LOGGER.warning("Briefing LLM (%s) failed: %s", provider, err)
            fallback_entity = self.options.get(CONF_FALLBACK_AI_TASK)
            if text is None and fallback_entity:
                try:
                    text = await async_generate_ai_task(self.hass, fallback_entity, system, prompt)
                    source = fallback_entity
                except LLMError as err:
                    _LOGGER.warning("Briefing fallback AI task failed: %s", err)
            if text is None:
                text = self.builder.fallback(built)

            briefing = {
                **text,
                "kind": built["kind"],
                "lights": built["lights"],
                "outlook": built["outlook"],
                "best_window": built["best_window"],
                "source": source,
                "generated_at": dt_util.now().isoformat(timespec="seconds"),
                "facts": built["facts"],
            }
            self.latest = briefing
            for update in list(self._listeners):
                update()
            self.hass.bus.async_fire(EVENT_BRIEFING, {k: briefing[k] for k in ("title", "summary", "kind", "source")})
            if notify:
                await self.async_notify(briefing, targets)
            return briefing

    async def async_notify(self, briefing: dict[str, Any], targets: list[str] | None = None) -> None:
        lights = " ".join(f"{l['icon']}{l['level_icon']}" for l in briefing["lights"])
        message = briefing["summary"] + (f"\n{lights}" if lights else "")
        data = {
            "tag": "energy_briefing",
            "group": "energy_briefing",
            "channel": "Energy Briefing",
            "notification_icon": "mdi:solar-power-variant",
            "clickAction": self.options.get(CONF_CLICK_PATH, DEFAULT_CLICK_PATH),
            "url": self.options.get(CONF_CLICK_PATH, DEFAULT_CLICK_PATH),
        }
        for service in targets or self.options.get(CONF_NOTIFY, []) or []:
            name = service.removeprefix("notify.")
            try:
                await self.hass.services.async_call(
                    "notify", name, {"title": briefing["title"], "message": message, "data": data}, blocking=True
                )
            except Exception as err:  # noqa: BLE001 - one broken phone must not stop the others
                _LOGGER.warning("Notify %s failed: %s", name, err)
