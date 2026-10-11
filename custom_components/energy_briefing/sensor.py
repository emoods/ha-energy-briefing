"""Briefing sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .manager import BriefingManager

ATTRS = ("kind", "summary", "details", "lights", "outlook", "best_window", "source", "generated_at")


def device_info(entry_id: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="Energy Briefing",
        manufacturer="emoods",
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([BriefingSensor(entry.runtime_data, entry.entry_id)])


class BriefingSensor(RestoreEntity, SensorEntity):
    """Latest briefing; the state is the headline, everything else in attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "briefing"
    _attr_icon = "mdi:solar-power-variant"
    _unrecorded_attributes = frozenset({"details", "lights", "outlook", "summary"})

    def __init__(self, manager: BriefingManager, entry_id: str) -> None:
        self._manager = manager
        self._attr_unique_id = f"{entry_id}_briefing"
        self._attr_device_info = device_info(entry_id)
        self._restored: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and self._manager.latest is None:
            self._restored = {"title": last.state, **{k: last.attributes.get(k) for k in ATTRS}}
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))

    @property
    def _data(self) -> dict[str, Any]:
        return self._manager.latest or self._restored

    @property
    def native_value(self) -> str | None:
        title = self._data.get("title")
        return title[:255] if title else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {k: self._data.get(k) for k in ATTRS}
