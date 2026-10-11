"""Best-effort auto-detection of entities for the setup wizard."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_BATTERY_SOC,
    CONF_CAR_CONNECTED,
    CONF_CAR_SOC,
    CONF_DISHWASHER,
    CONF_DRYER,
    CONF_NOTIFY,
    CONF_PRICE_EXPORT,
    CONF_PRICE_GAS,
    CONF_PRICE_IMPORT,
    CONF_WASHER,
    CONF_WEATHER,
)

INVERTER_PLATFORMS = {
    "huawei_solar", "solaredge", "solaredge_modbus", "solaredge_modbus_multi", "fronius", "sma", "goodwe",
    "solax_modbus", "solarman", "victron", "powerwall", "tesla_powerwall", "sungrow", "foxess_modbus",
    "growatt_server", "enphase_envoy", "kostal_plenticore", "e3dc_rscp", "sonnenbatterie", "senec",
    "givenergy_local", "anker_solix", "sessy", "deye", "alpha_ess", "rct_power",
}
CAR_PLATFORMS = {"tesla_custom", "tesla_fleet", "teslemetry", "tessie", "volkswagen_we_connect_id",
                 "bmw_connected_drive", "kia_uvo", "renault", "polestar_api", "audiconnect", "mercedesme"}
CHARGER_PLATFORMS = {"evcc_intg", "wallbox", "easee", "goecharger", "goecharger_api2", "keba", "zaptec",
                     "ohme", "openwbmqtt", "ocpp", "alfen_wallbox", "peblar", "v2c"}


def _first(candidates: list[tuple[int, str]]) -> str | None:
    return sorted(candidates, reverse=True)[0][1] if candidates else None


async def async_detect(hass: HomeAssistant) -> dict[str, Any]:
    """Return suggested option values."""
    reg = er.async_get(hass)
    found: dict[str, Any] = {}

    weather = sorted(hass.states.async_entity_ids("weather"))
    if weather:
        found[CONF_WEATHER] = "weather.home" if "weather.home" in weather else weather[0]

    soc, car_soc, car_conn, washer, dryer, dish = [], [], [], [], [], []
    for entry in reg.entities.values():
        if entry.disabled_by:
            continue
        eid = entry.entity_id
        name = f"{eid} {entry.original_name or ''}".lower()
        state = hass.states.get(eid)
        device_class = entry.device_class or entry.original_device_class
        unit = state.attributes.get("unit_of_measurement") if state else None
        if eid.startswith("sensor.") and device_class == "battery" and unit == "%":
            if entry.platform in INVERTER_PLATFORMS:
                soc.append((2 if "capacity" in name or "soc" in name else 1, eid))
            elif entry.platform in CAR_PLATFORMS and not any(x in name for x in ("model_y", "tpms")):
                car_soc.append((1, eid))
        if eid.startswith("binary_sensor.") and (
            (entry.platform in CHARGER_PLATFORMS and any(x in name for x in ("connected", "plug")))
            or eid == "binary_sensor.evcc_car_connected"
        ):
            car_conn.append((2 if "evcc" in eid else 1, eid))
        if entry.platform == "ha_washdata" and eid.endswith("_state"):
            if any(x in name for x in ("trock", "dryer")):
                dryer.append((1, eid))
            elif any(x in name for x in ("wasch", "washer", "washing")):
                washer.append((1, eid))
        if entry.platform == "home_connect" and "operation_state" in eid and any(x in name for x in ("geschirr", "dish")):
            dish.append((1, eid))

    for key, cands in ((CONF_BATTERY_SOC, soc), (CONF_CAR_SOC, car_soc), (CONF_CAR_CONNECTED, car_conn),
                       (CONF_WASHER, washer), (CONF_DRYER, dryer), (CONF_DISHWASHER, dish)):
        value = _first(cands)
        if value:
            found[key] = value

    found[CONF_NOTIFY] = [f"notify.{s}" for s in sorted(hass.services.async_services_for_domain("notify")) if s.startswith("mobile_app_")]

    try:
        from homeassistant.components.energy.data import async_get_manager

        prefs = (await async_get_manager(hass)).data or {}
    except Exception:  # noqa: BLE001
        prefs = {}
    found["energy_dashboard"] = {"solar": False, "grid": False, "forecast": False}
    for src in prefs.get("energy_sources", []):
        if src.get("type") == "solar":
            found["energy_dashboard"]["solar"] = True
            if src.get("config_entry_solar_forecast"):
                found["energy_dashboard"]["forecast"] = True
        elif src.get("type") == "grid":
            found["energy_dashboard"]["grid"] = True
            if src.get("number_energy_price") is not None:
                found[CONF_PRICE_IMPORT] = src["number_energy_price"]
            if src.get("number_energy_price_export") is not None:
                found[CONF_PRICE_EXPORT] = src["number_energy_price_export"]
        elif src.get("type") == "gas" and src.get("number_energy_price") is not None:
            found[CONF_PRICE_GAS] = src["number_energy_price"]
    return found
