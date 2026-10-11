"""Collect energy facts, compute recommendations and build the briefing."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from statistics import median
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AC_HEATING,
    CONF_BASE_LOAD,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_EMPTY_SOC,
    CONF_BATTERY_SOC,
    CONF_CAR_CONNECTED,
    CONF_CAR_SOC,
    CONF_DISHWASHER,
    CONF_DRYER,
    CONF_EVCC_URL,
    CONF_LANGUAGE,
    CONF_PRICE_EXPORT,
    CONF_PRICE_GAS,
    CONF_PRICE_IMPORT,
    CONF_WASHER,
    CONF_WEATHER,
    CONF_WEEKLY_DAY,
    DEFAULT_BASE_LOAD,
    DEFAULT_BATTERY_EMPTY_SOC,
    DEFAULT_WEEKLY_DAY,
    DOMAIN,
    FAIR_CONDITIONS,
    GOOD_CONDITIONS,
    KIND_EVENING,
    KIND_MIDDAY,
    KIND_MORNING,
    STORAGE_VERSION,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

LIGHT_ICONS = {"laundry": "🧺", "dishwasher": "🍽️", "car": "🚗", "ac_heating": "❄️"}
LEVEL_ICONS = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
RATING_ICONS = {"great": "☀️", "good": "🌤️", "fair": "⛅", "poor": "☁️"}
WEATHER_ICONS = {
    "sunny": "☀️", "clear-night": "☀️", "partlycloudy": "⛅", "cloudy": "☁️", "fog": "🌫️",
    "rainy": "🌧️", "pouring": "🌧️", "lightning": "⛈️", "lightning-rainy": "⛈️",
    "snowy": "🌨️", "snowy-rainy": "🌨️", "hail": "🌨️", "windy": "💨", "windy-variant": "💨",
}

TEXT = {
    "en": {
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "laundry": "Laundry", "dishwasher": "Dishwasher", "car": "Car", "ac_heating": "AC heating",
        "today": "Today", "tomorrow": "Tomorrow",
    },
    "de": {
        "days": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
        "laundry": "Wäsche", "dishwasher": "Spüler", "car": "Auto", "ac_heating": "Klima statt Gas",
        "today": "Heute", "tomorrow": "Morgen",
    },
}

SYSTEM_PROMPT = {
    "en": (
        "You write a short, witty home energy briefing for a family with a PV system, a home battery "
        "and possibly an electric car. Use English. Be funny, casual and honest, never cringe. "
        "Rules: use ONLY the numbers and facts given in the JSON, never invent values; round sensibly. "
        "Leave out everything that does not matter for a decision today (or tomorrow for evening briefings). "
        "If several bad solar days are ahead, say plainly that it does not matter when to run appliances. "
        "If a much better day is coming within two days, suggest waiting for big loads. "
        "Evening briefing: focus on tomorrow and whether the battery lasts the night. "
        "Weekly review (if present): add a short, fun summary of the week with money saved. "
        "Mention system problems only if listed under 'problems'. Use € and kWh. "
        "The details must be a markdown bullet list."
    ),
    "de": (
        "Du schreibst einen kurzen, witzigen Energie-Bericht für eine Familie mit PV-Anlage, Hausbatterie "
        "und eventuell E-Auto. Schreibe auf Deutsch, locker, humorvoll und ehrlich, nie peinlich, duze die Leser (ihr). "
        "Regeln: verwende NUR die Zahlen und Fakten aus dem JSON, erfinde nie Werte, runde sinnvoll. "
        "Lass alles weg, was für eine Entscheidung heute (bzw. morgen beim Abendbericht) keine Rolle spielt. "
        "Wenn mehrere schlechte Solartage kommen, sag klar, dass es egal ist, wann man Geräte anstellt. "
        "Kommt in den nächsten zwei Tagen ein deutlich besserer Tag, empfiehl, große Verbraucher zu verschieben. "
        "Abendbericht: Fokus auf morgen und ob die Batterie die Nacht schafft. "
        "Wochenrückblick (falls vorhanden): kurze, lustige Zusammenfassung der Woche mit gesparten Euro. "
        "Systemprobleme nur erwähnen, wenn sie unter 'problems' stehen. Verwende € und kWh. "
        "Die Details sind eine Markdown-Aufzählung."
    ),
}


def _num(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _state(hass: HomeAssistant, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    return state.state


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


class BriefingBuilder:
    """Builds briefings for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], data: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self.data = data
        self.lang = data.get(CONF_LANGUAGE, "en")
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._memory: dict[str, Any] | None = None

    # ----------------------------------------------------------- memory
    async def _load_memory(self) -> dict[str, Any]:
        if self._memory is None:
            self._memory = await self._store.async_load() or {"forecasts": {}, "ratios": []}
        return self._memory

    async def _update_accuracy(self, today_forecast: float | None, solar_stat: str | None) -> float:
        """Remember today's forecast, score yesterday's against reality, return accuracy factor."""
        mem = await self._load_memory()
        today = dt_util.now().date()
        key = today.isoformat()
        if today_forecast and key not in mem["forecasts"]:
            mem["forecasts"][key] = today_forecast
        yesterday = (today - timedelta(days=1)).isoformat()
        if solar_stat and yesterday in mem["forecasts"] and mem.get("scored") != yesterday:
            actual = (await self._daily_changes([solar_stat], 1)).get(solar_stat)
            predicted = mem["forecasts"][yesterday]
            if actual is not None and predicted and predicted > 1:
                mem["ratios"] = (mem["ratios"] + [round(actual / predicted, 3)])[-14:]
                mem["scored"] = yesterday
        mem["forecasts"] = {k: v for k, v in mem["forecasts"].items() if k >= (today - timedelta(days=10)).isoformat()}
        await self._store.async_save(mem)
        recent = mem["ratios"][-7:]
        if len(recent) < 3:
            return 1.0
        return max(0.5, min(1.3, median(recent)))

    # ----------------------------------------------------------- recorder
    async def _daily_changes(self, stat_ids: list[str], days: int) -> dict[str, float | None]:
        """Sum of daily 'change' for the last `days` full days."""
        stat_ids = [s for s in stat_ids if s]
        if not stat_ids:
            return {}
        end = dt_util.start_of_local_day()
        start = end - timedelta(days=days)
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, set(stat_ids), "day", None, {"change"}
        )
        out: dict[str, float | None] = {}
        for sid in stat_ids:
            rows = stats.get(sid) or []
            vals = [r.get("change") for r in rows if r.get("change") is not None]
            out[sid] = sum(vals) if vals else None
        return out

    async def _today_change(self, stat_id: str | None) -> float | None:
        if not stat_id:
            return None
        start = dt_util.start_of_local_day()
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, dt_util.now(), {stat_id}, "hour", None, {"change"}
        )
        vals = [r.get("change") for r in stats.get(stat_id, []) if r.get("change") is not None]
        return sum(vals) if vals else None

    async def _battery_night(self, soc_entity: str | None, empty_soc: float) -> dict[str, Any] | None:
        if not soc_entity:
            return None
        now = dt_util.now()
        start = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, now, {soc_entity}, "hour", None, {"min"}
        )
        rows = stats.get(soc_entity) or []
        if not rows:
            return None
        lowest = min(r["min"] for r in rows if r.get("min") is not None)
        empty_at = None
        for row in rows:
            if row.get("min") is not None and row["min"] <= empty_soc:
                ts = row["start"]
                stamp = dt_util.utc_from_timestamp(ts) if isinstance(ts, (int, float)) else ts
                empty_at = dt_util.as_local(stamp).strftime("%H:%M")
                break
        return {"lowest_soc": round(lowest), "empty_at": empty_at}

    # ----------------------------------------------------------- energy prefs
    async def _energy_prefs(self) -> dict[str, Any]:
        result: dict[str, Any] = {"solar": [], "import": [], "export": [], "gas": [], "forecast_entries": []}
        try:
            from homeassistant.components.energy.data import async_get_manager

            manager = await async_get_manager(self.hass)
            prefs = manager.data or {}
        except Exception:  # noqa: BLE001 - energy may not be set up
            return result
        for src in prefs.get("energy_sources", []):
            if src.get("type") == "solar":
                result["solar"].append(src.get("stat_energy_from"))
                result["forecast_entries"].extend(src.get("config_entry_solar_forecast") or [])
            elif src.get("type") == "grid":
                if src.get("stat_energy_from"):
                    result["import"].append(src["stat_energy_from"])
                if src.get("stat_energy_to"):
                    result["export"].append(src["stat_energy_to"])
                for flow in src.get("flow_from", []):
                    result["import"].append(flow.get("stat_energy_from"))
                for flow in src.get("flow_to", []):
                    result["export"].append(flow.get("stat_energy_to"))
            elif src.get("type") == "gas":
                result["gas"].append(src.get("stat_energy_from"))
        for key in ("solar", "import", "export", "gas"):
            result[key] = [s for s in result[key] if s]
        return result

    async def _solar_forecast(self, entry_ids: list[str]) -> dict[datetime, float]:
        """Hourly Wh forecast summed over all forecast providers in the energy dashboard."""
        hours: dict[datetime, float] = {}
        for entry_id in entry_ids:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None:
                continue
            try:
                integration = await async_get_integration(self.hass, entry.domain)
                platform = await integration.async_get_platform("energy")
                forecast = await platform.async_get_solar_forecast(self.hass, entry_id)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("No solar forecast from %s: %s", entry.domain, err)
                continue
            for stamp, wh in ((forecast or {}).get("wh_hours") or {}).items():
                when = dt_util.parse_datetime(stamp) if isinstance(stamp, str) else stamp
                if when is None:
                    continue
                when = dt_util.as_local(when).replace(minute=0, second=0, microsecond=0)
                hours[when] = hours.get(when, 0.0) + float(wh)
        return hours

    async def _weather(self, entity_id: str | None) -> list[dict[str, Any]]:
        if not entity_id:
            return []
        try:
            result = await self.hass.services.async_call(
                "weather", "get_forecasts", {"type": "daily"},
                target={"entity_id": entity_id}, blocking=True, return_response=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Weather forecast failed: %s", err)
            return []
        return ((result or {}).get(entity_id) or {}).get("forecast") or []

    async def _evcc(self, url: str | None) -> dict[str, Any] | None:
        if not url:
            return None
        try:
            async with async_get_clientsession(self.hass).get(f"{url.rstrip('/')}/api/state", timeout=10) as resp:
                state = await resp.json()
        except Exception:  # noqa: BLE001
            return None
        stats = (state.get("statistics") or {}).get("30d") or {}
        return {
            "charged_kwh_30d": _r(stats.get("chargedKWh")),
            "solar_share_30d_percent": _r(stats.get("solarPercentage"), 0),
        }

    # ----------------------------------------------------------- main
    async def async_build(self, kind: str | None = None) -> dict[str, Any]:
        """Collect facts and compute deterministic recommendations."""
        hass = self.hass
        opt = self.options
        now = dt_util.now()
        if kind is None:
            kind = KIND_MORNING if now.hour < 11 else KIND_MIDDAY if now.hour < 16 else KIND_EVENING
        txt = TEXT.get(self.lang, TEXT["en"])

        base_load = float(opt.get(CONF_BASE_LOAD) or DEFAULT_BASE_LOAD)
        capacity = float(opt.get(CONF_BATTERY_CAPACITY) or 0) or None
        empty_soc = float(opt.get(CONF_BATTERY_EMPTY_SOC) or DEFAULT_BATTERY_EMPTY_SOC)
        price_in = opt.get(CONF_PRICE_IMPORT)
        price_out = opt.get(CONF_PRICE_EXPORT)
        price_gas = opt.get(CONF_PRICE_GAS)

        prefs = await self._energy_prefs()
        hourly = await self._solar_forecast(prefs["forecast_entries"])
        today = now.date()
        tomorrow = today + timedelta(days=1)
        fc_today = sum(v for k, v in hourly.items() if k.date() == today) / 1000 if hourly else None
        fc_rest = sum(v for k, v in hourly.items() if k.date() == today and k >= now.replace(minute=0, second=0, microsecond=0)) / 1000 if hourly else None
        fc_tomorrow = sum(v for k, v in hourly.items() if k.date() == tomorrow) / 1000 if hourly else None

        solar_stat = prefs["solar"][0] if prefs["solar"] else None
        accuracy = await self._update_accuracy(fc_today, solar_stat)

        weather = await self._weather(opt.get(CONF_WEATHER))
        soc = _num(hass, opt.get(CONF_BATTERY_SOC))
        sun = hass.states.get("sun.sun")
        sunset = dt_util.parse_datetime(sun.attributes.get("next_setting")) if sun and sun.attributes.get("next_setting") else None
        sunrise = dt_util.parse_datetime(sun.attributes.get("next_rising")) if sun and sun.attributes.get("next_rising") else None

        # --- day model -------------------------------------------------
        target_day = tomorrow if kind == KIND_EVENING else today
        day_hours = {k: v * accuracy for k, v in hourly.items() if k.date() == target_day}
        if kind != KIND_EVENING:
            day_hours = {k: v for k, v in day_hours.items() if k >= now.replace(minute=0, second=0, microsecond=0)}
        sun_hours = [k for k, v in day_hours.items() if v > 50]
        house_kwh = base_load * len(sun_hours)
        if capacity:
            if kind == KIND_EVENING:
                battery_need = capacity * (100 - empty_soc) / 100
            else:
                battery_need = capacity * max(0.0, 100 - (soc or 0)) / 100
        else:
            battery_need = 0.0
        pv_kwh = sum(day_hours.values()) / 1000
        spare = pv_kwh - house_kwh - battery_need if hourly else None
        peak_kw = max(day_hours.values()) / 1000 if day_hours else 0.0

        best_window = None
        if day_hours:
            run: list[datetime] = []
            best: list[datetime] = []
            for hour in sorted(day_hours):
                if day_hours[hour] / 1000 - base_load >= 2.0:
                    run.append(hour)
                    if len(run) > len(best):
                        best = list(run)
                else:
                    run = []
            if best:
                best_window = f"{best[0].strftime('%H:%M')}–{(best[-1] + timedelta(hours=1)).strftime('%H:%M')}"

        # --- traffic lights -------------------------------------------
        lights: list[dict[str, Any]] = []

        def add(key: str, level: str, hint: str | None = None) -> None:
            lights.append({"key": key, "icon": LIGHT_ICONS[key], "label": txt[key], "level": level,
                           "level_icon": LEVEL_ICONS[level], "hint": hint})

        if spare is not None:
            if opt.get(CONF_WASHER) or opt.get(CONF_DRYER):
                level = "green" if spare >= 4 and peak_kw >= 2.5 else "yellow" if spare >= 1.5 else "red"
                add("laundry", level, best_window if level != "red" else None)
            if opt.get(CONF_DISHWASHER):
                level = "green" if spare >= 2 else "yellow" if spare >= 0.8 else "red"
                add("dishwasher", level, best_window if level != "red" else None)
            car_connected = _state(hass, opt.get(CONF_CAR_CONNECTED)) == "on"
            car_soc = _num(hass, opt.get(CONF_CAR_SOC))
            if opt.get(CONF_CAR_CONNECTED) or opt.get(CONF_CAR_SOC):
                if car_connected or (car_soc is not None and car_soc < 50):
                    level = "green" if spare >= 10 else "yellow" if spare >= 4.5 else "red"
                    add("car", level, best_window if level != "red" else None)
            if opt.get(CONF_AC_HEATING) and weather:
                day_fc = next((d for d in weather if dt_util.as_local(dt_util.parse_datetime(d["datetime"])).date() == target_day), None)
                if day_fc and now.month in (9, 10, 11, 12, 1, 2, 3, 4, 5):
                    tmax = day_fc.get("temperature")
                    if tmax is not None and 6 <= tmax <= 20 and spare >= 3:
                        sunny = day_fc.get("condition") in GOOD_CONDITIONS | FAIR_CONDITIONS
                        add("ac_heating", "green" if spare >= 6 and sunny else "yellow", best_window)

        # --- outlook ----------------------------------------------------
        daily_need = base_load * 24
        outlook: list[dict[str, Any]] = []
        for day in weather[:6]:
            when = dt_util.parse_datetime(day.get("datetime", ""))
            if when is None:
                continue
            date = dt_util.as_local(when).date()
            if date < today:
                continue
            kwh = None
            if hourly and date in (today, tomorrow):
                kwh = sum(v for k, v in hourly.items() if k.date() == date) / 1000 * accuracy
            if kwh is not None:
                ratio = kwh / daily_need if daily_need else 0
                rating = "great" if ratio >= 2 else "good" if ratio >= 1.2 else "fair" if ratio >= 0.6 else "poor"
            else:
                cond = day.get("condition")
                rating = "good" if cond in GOOD_CONDITIONS else "fair" if cond in FAIR_CONDITIONS else "poor"
            outlook.append({
                "date": date.isoformat(),
                "day": txt["today"] if date == today else txt["tomorrow"] if date == tomorrow else txt["days"][date.weekday()],
                "condition": day.get("condition"),
                "icon": WEATHER_ICONS.get(day.get("condition"), "❔"),
                "rating": rating,
                "rating_icon": RATING_ICONS[rating],
                "pv_kwh": _r(kwh),
                "estimated": kwh is None,
                "temp_max": day.get("temperature"),
                "temp_min": day.get("templow"),
                "precipitation": day.get("precipitation"),
            })
        upcoming = [o for o in outlook if o["date"] > today.isoformat()][:5]
        week_bad = bool(upcoming) and all(o["rating"] == "poor" for o in upcoming)
        better_soon = None
        ref = next((o for o in outlook if o["date"] == target_day.isoformat()), None)
        order = ["poor", "fair", "good", "great"]
        if ref:
            for o in outlook:
                if target_day.isoformat() < o["date"] <= (target_day + timedelta(days=2)).isoformat():
                    if order.index(o["rating"]) >= order.index(ref["rating"]) + 2:
                        better_soon = o["day"]
                        break

        # --- yesterday / week ------------------------------------------
        ids = prefs["solar"] + prefs["import"] + prefs["export"] + prefs["gas"]
        yday = await self._daily_changes(ids, 1)

        def total(keys: list[str], source: dict[str, float | None]) -> float | None:
            vals = [source.get(k) for k in keys if source.get(k) is not None]
            return sum(vals) if vals else None

        def kwh(stat_ids: list[str], source: dict[str, float | None]) -> float | None:
            value = total(stat_ids, source)
            if value is None:
                return None
            # Energy dashboard stats can be Wh or kWh; Wh daily values are large.
            unit = None
            for sid in stat_ids:
                state = hass.states.get(sid)
                if state is not None:
                    unit = state.attributes.get("unit_of_measurement")
                    break
            return value / 1000 if unit == "Wh" else value

        y_pv = kwh(prefs["solar"], yday)
        y_import = kwh(prefs["import"], yday)
        y_export = kwh(prefs["export"], yday)
        y_gas = total(prefs["gas"], yday)
        yesterday = {
            "pv_kwh": _r(y_pv), "grid_import_kwh": _r(y_import), "grid_export_kwh": _r(y_export),
            "import_cost_eur": _r(y_import * price_in, 2) if (y_import is not None and price_in) else None,
            "export_revenue_eur": _r(y_export * price_out, 2) if (y_export is not None and price_out) else None,
            "gas": _r(y_gas, 2), "gas_cost_eur": _r(y_gas * price_gas, 2) if (y_gas is not None and price_gas) else None,
        }

        weekly = None
        weekly_day = opt.get(CONF_WEEKLY_DAY, DEFAULT_WEEKLY_DAY)
        if kind == KIND_EVENING and weekly_day in WEEKDAYS and now.weekday() == WEEKDAYS.index(weekly_day):
            week = await self._daily_changes(ids, 7)
            w_pv, w_imp, w_exp = kwh(prefs["solar"], week), kwh(prefs["import"], week), kwh(prefs["export"], week)
            weekly = {
                "pv_kwh": _r(w_pv), "grid_import_kwh": _r(w_imp), "grid_export_kwh": _r(w_exp),
                "self_consumption_percent": _r((w_pv - w_exp) / w_pv * 100, 0) if (w_pv and w_exp is not None) else None,
                "saved_eur": _r((w_pv - w_exp) * price_in, 2) if (w_pv and w_exp is not None and price_in) else None,
                "export_revenue_eur": _r(w_exp * price_out, 2) if (w_exp is not None and price_out) else None,
                "import_cost_eur": _r(w_imp * price_in, 2) if (w_imp is not None and price_in) else None,
                "ev": await self._evcc(opt.get(CONF_EVCC_URL)),
            }

        # --- battery ----------------------------------------------------
        battery: dict[str, Any] = {"soc": _r(soc, 0)}
        if kind != KIND_EVENING:
            night = await self._battery_night(opt.get(CONF_BATTERY_SOC), empty_soc)
            if night:
                battery["last_night"] = night
        elif soc is not None and capacity:
            usable = max(0.0, soc - empty_soc) / 100 * capacity
            hours = usable / base_load if base_load else 0
            empty_time = now + timedelta(hours=hours)
            battery["night_lasts"] = bool(sunrise and empty_time >= sunrise)
            battery["empty_at"] = None if battery["night_lasts"] else empty_time.strftime("%H:%M")

        pv_so_far = None
        if kind == KIND_MIDDAY and solar_stat:
            value = await self._today_change(solar_stat)
            if value is not None:
                pv_so_far = kwh([solar_stat], {solar_stat: value})

        # --- appliances / car ------------------------------------------
        appliances = {}
        for key, conf in (("washer", CONF_WASHER), ("dryer", CONF_DRYER), ("dishwasher", CONF_DISHWASHER)):
            if opt.get(conf):
                appliances[key] = _state(hass, opt[conf])
        car = None
        if opt.get(CONF_CAR_CONNECTED) or opt.get(CONF_CAR_SOC):
            car = {"connected": _state(hass, opt.get(CONF_CAR_CONNECTED)) == "on" if opt.get(CONF_CAR_CONNECTED) else None,
                   "soc": _r(_num(hass, opt.get(CONF_CAR_SOC)), 0)}

        problems = []
        for conf in (CONF_BATTERY_SOC, CONF_WEATHER):
            if opt.get(conf) and _state(hass, opt[conf]) is None:
                problems.append(f"{opt[conf]} unavailable")
        if not hourly:
            problems.append("no solar forecast in the energy dashboard")

        facts = {
            "kind": kind,
            "now": now.strftime("%A %Y-%m-%d %H:%M"),
            "sunset": dt_util.as_local(sunset).strftime("%H:%M") if sunset else None,
            "sunrise_next": dt_util.as_local(sunrise).strftime("%H:%M") if sunrise else None,
            "pv_forecast_today_kwh": _r(fc_today * accuracy) if fc_today is not None else None,
            "pv_forecast_rest_of_today_kwh": _r(fc_rest * accuracy) if fc_rest is not None else None,
            "pv_forecast_tomorrow_kwh": _r(fc_tomorrow * accuracy) if fc_tomorrow is not None else None,
            "pv_so_far_today_kwh": _r(pv_so_far),
            "forecast_accuracy_factor": round(accuracy, 2),
            "target_day": "tomorrow" if kind == KIND_EVENING else "today",
            "spare_kwh_after_house_and_battery": _r(spare),
            "peak_pv_kw": _r(peak_kw),
            "best_window": best_window,
            "battery": battery,
            "car": car,
            "appliances_running_state": appliances or None,
            "traffic_lights": [{"device": l["key"], "level": l["level"], "window": l["hint"]} for l in lights],
            "outlook": [{"day": o["day"], "condition": o["condition"], "rating": o["rating"], "pv_kwh": o["pv_kwh"],
                         "estimated_from_weather": o["estimated"], "temp_max": o["temp_max"], "temp_min": o["temp_min"]}
                        for o in outlook],
            "rest_of_week_bad": week_bad,
            "much_better_day_soon": better_soon,
            "frost_tomorrow": any(o["date"] == tomorrow.isoformat() and (o["temp_min"] is not None and o["temp_min"] <= 2) for o in outlook),
            "yesterday": yesterday if kind == KIND_MORNING else None,
            "weekly_review": weekly,
            "prices_eur_per_kwh": {"import": price_in, "export": price_out},
            "problems": problems,
        }
        return {"kind": kind, "facts": facts, "lights": lights, "outlook": outlook, "best_window": best_window}

    def prompt(self, built: dict[str, Any]) -> tuple[str, str]:
        system = SYSTEM_PROMPT.get(self.lang, SYSTEM_PROMPT["en"])
        names = {KIND_MORNING: "morning briefing", KIND_MIDDAY: "midday update", KIND_EVENING: "evening briefing with outlook for tomorrow"}
        prompt = (
            f"Write the {names[built['kind']]}. Facts (JSON, null = unknown, leave out):\n"
            + json.dumps(built["facts"], ensure_ascii=False, default=str)
        )
        return system, prompt

    def fallback(self, built: dict[str, Any]) -> dict[str, str]:
        """Plain template text when no LLM is available."""
        f = built["facts"]
        de = self.lang == "de"
        evening = built["kind"] == KIND_EVENING
        pv = f["pv_forecast_tomorrow_kwh"] if evening else f["pv_forecast_today_kwh"]
        day = ("Morgen" if de else "Tomorrow") if evening else ("Heute" if de else "Today")
        spare = f["spare_kwh_after_house_and_battery"]
        if spare is None:
            mood = "🔌"
        else:
            mood = "☀️" if spare >= 6 else "⛅" if spare >= 1.5 else "☁️"
        title = f"{mood} {day}: ~{pv} kWh PV" if pv is not None else f"{mood} {day}"
        parts = []
        if f["best_window"]:
            parts.append(("Bestes Fenster " if de else "Best window ") + f["best_window"] + ".")
        if f["rest_of_week_bad"]:
            parts.append("Die nächsten Tage bleiben trüb – egal, wann ihr wascht." if de else "The next days stay grey – run appliances whenever.")
        elif f["much_better_day_soon"]:
            parts.append((f"{f['much_better_day_soon']} wird deutlich besser." if de else f"{f['much_better_day_soon']} looks much better."))
        if evening and f["battery"].get("night_lasts") is False:
            parts.append((f"Batterie reicht bis ca. {f['battery']['empty_at']}." if de else f"Battery lasts until about {f['battery']['empty_at']}."))
        summary = " ".join(parts) or ("Nichts Besonderes heute." if de else "Nothing special today.")
        details = "\n".join(f"- {l['icon']} {l['label']}: {l['level_icon']}" + (f" {l['hint']}" if l["hint"] else "") for l in built["lights"])
        return {"title": title, "summary": summary, "details": details}
