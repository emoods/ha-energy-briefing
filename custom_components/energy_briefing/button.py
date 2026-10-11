"""Button to create a briefing on demand."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .manager import BriefingManager
from .sensor import device_info


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    manager: BriefingManager = entry.runtime_data
    async_add_entities(
        [
            BriefingButton(manager, entry.entry_id, "generate", notify=False),
            BriefingButton(manager, entry.entry_id, "generate_and_notify", notify=True),
        ]
    )


class BriefingButton(ButtonEntity):
    """Creates a new briefing."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:refresh"

    def __init__(self, manager: BriefingManager, entry_id: str, key: str, notify: bool) -> None:
        self._manager = manager
        self._notify = notify
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        await self._manager.async_generate(notify=self._notify)
