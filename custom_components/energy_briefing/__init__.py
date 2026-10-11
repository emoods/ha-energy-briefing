"""Energy Briefing: LLM-written solar/battery/EV briefings for Home Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, KINDS
from .manager import BriefingManager

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type EnergyBriefingConfigEntry = ConfigEntry[BriefingManager]

SERVICE_GENERATE = "generate"
GENERATE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("kind"): vol.In(KINDS),
        vol.Optional("notify", default=False): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the generate service."""

    async def _generate(call: ServiceCall) -> ServiceResponse:
        entries = [
            e for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if not call.data.get("config_entry_id") or e.entry_id == call.data["config_entry_id"]
        ]
        if not entries:
            raise ServiceValidationError("No loaded Energy Briefing entry found")
        result = None
        for entry in entries:
            result = await entry.runtime_data.async_generate(call.data.get("kind"), call.data["notify"])
        if call.return_response:
            return {k: result[k] for k in ("title", "summary", "details", "kind", "source", "generated_at")}
        return None

    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE, _generate, schema=GENERATE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EnergyBriefingConfigEntry) -> bool:
    """Set up from a config entry."""
    manager = BriefingManager(hass, entry)
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.async_schedule()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: EnergyBriefingConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EnergyBriefingConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
