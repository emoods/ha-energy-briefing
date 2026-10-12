"""Energy Briefing: LLM-written solar/battery/EV briefings for Home Assistant."""

from __future__ import annotations

from pathlib import Path

import logging

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, KINDS
from .manager import BriefingManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type EnergyBriefingConfigEntry = ConfigEntry[BriefingManager]

CARD_URL = f"/{DOMAIN}/energy-briefing-card.js"
SERVICE_GENERATE = "generate"
GENERATE_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("kind"): vol.In(KINDS),
        vol.Optional("notify", default=False): cv.boolean,
        vol.Optional("notify_targets"): vol.All(cv.ensure_list, [cv.string]),
    }
)


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and load it in every dashboard."""
    path = Path(__file__).parent / "frontend" / "energy-briefing-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
    version = (await async_get_integration(hass, DOMAIN)).version
    url = f"{CARD_URL}?v={version}"
    add_extra_js_url(hass, url)
    try:
        await _async_register_resource(hass, url)
    except Exception:  # noqa: BLE001 - the card still loads via extra_js_url
        _LOGGER.debug("Could not register the dashboard resource", exc_info=True)


async def _async_register_resource(hass: HomeAssistant, url: str) -> None:
    """Add the card as a Lovelace resource so dashboards always load it.

    extra_js_url alone depends on the cached page; a resource is loaded by the
    dashboard itself. Only storage-mode resources are managed.
    """
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None or getattr(lovelace, "resource_mode", "storage") != "storage":
        return
    if not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True
    ours = [
        item for item in resources.async_items()
        if str(item.get("url", "")).split("?")[0] == CARD_URL
    ]
    if not ours:
        await resources.async_create_item({"res_type": "module", "url": url})
        return
    if ours[0].get("url") != url or ours[0].get("type") != "module":
        await resources.async_update_item(ours[0]["id"], {"res_type": "module", "url": url})
    for duplicate in ours[1:]:
        await resources.async_delete_item(duplicate["id"])


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the dashboard card and the generate service."""
    await _async_register_card(hass)

    async def _generate(call: ServiceCall) -> ServiceResponse:
        entries = [
            e for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if not call.data.get("config_entry_id") or e.entry_id == call.data["config_entry_id"]
        ]
        if not entries:
            raise ServiceValidationError("No loaded Energy Briefing entry found")
        result = None
        for entry in entries:
            result = await entry.runtime_data.async_generate(
                call.data.get("kind"), call.data["notify"], call.data.get("notify_targets")
            )
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
