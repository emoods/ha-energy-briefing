"""Setup wizard and options for Energy Briefing."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_AC_HEATING,
    CONF_AI_TASK_ENTITY,
    CONF_API_KEY,
    CONF_BASE_LOAD,
    CONF_BASE_URL,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_EMPTY_SOC,
    CONF_BATTERY_SOC,
    CONF_CAR_CONNECTED,
    CONF_CAR_SOC,
    CONF_CLICK_PATH,
    CONF_DISHWASHER,
    CONF_DRYER,
    CONF_EVCC_URL,
    CONF_FALLBACK_AI_TASK,
    CONF_LANGUAGE,
    CONF_LOCATION,
    CONF_MODEL,
    CONF_NOTIFY,
    CONF_PRICE_EXPORT,
    CONF_PRICE_GAS,
    CONF_PRICE_IMPORT,
    CONF_PROVIDER,
    CONF_SERVICE_ACCOUNT,
    CONF_TIMES_WEEKDAY,
    CONF_TIMES_WEEKEND,
    CONF_WASHER,
    CONF_WEATHER,
    CONF_WEEKLY_DAY,
    DEFAULT_BASE_LOAD,
    DEFAULT_BATTERY_EMPTY_SOC,
    DEFAULT_CLICK_PATH,
    DEFAULT_LOCATION,
    DEFAULT_TIMES_WEEKDAY,
    DEFAULT_TIMES_WEEKEND,
    DEFAULT_WEEKLY_DAY,
    DOMAIN,
    LANGUAGES,
    PROVIDER_AI_TASK,
    PROVIDER_NONE,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_VERTEX,
    PROVIDERS,
    WEEKDAYS,
)
from .detect import async_detect
from .llm import LLMClient, LLMError
from .manager import parse_times

ENTITY_FIELDS = {
    CONF_WEATHER: "weather",
    CONF_BATTERY_SOC: "sensor",
    CONF_CAR_CONNECTED: "binary_sensor",
    CONF_CAR_SOC: "sensor",
    CONF_WASHER: "sensor",
    CONF_DRYER: "sensor",
    CONF_DISHWASHER: "sensor",
}


def _select(options: list[str], key: str, multiple: bool = False, custom: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options, translation_key=key, multiple=multiple, custom_value=custom,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _num(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX))


def _opt(key: str, value: Any) -> vol.Optional:
    return vol.Optional(key, description={"suggested_value": value} if value not in (None, "", []) else None)


def _credentials_schema(provider: str, data: dict[str, Any]) -> vol.Schema:
    if provider == PROVIDER_VERTEX:
        return vol.Schema({
            vol.Required(CONF_SERVICE_ACCOUNT): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Required(CONF_LOCATION, default=data.get(CONF_LOCATION, DEFAULT_LOCATION)): str,
        })
    if provider == PROVIDER_OPENAI_COMPAT:
        return vol.Schema({
            vol.Required(CONF_BASE_URL, default=data.get(CONF_BASE_URL, "https://openrouter.ai/api/v1")): str,
            vol.Optional(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        })
    if provider == PROVIDER_AI_TASK:
        return vol.Schema({
            vol.Required(CONF_AI_TASK_ENTITY): EntitySelector(EntitySelectorConfig(domain="ai_task")),
        })
    return vol.Schema({vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))})


def _sources_schema(values: dict[str, Any]) -> vol.Schema:
    schema: dict[Any, Any] = {}
    for key, domain in ENTITY_FIELDS.items():
        schema[_opt(key, values.get(key))] = EntitySelector(EntitySelectorConfig(domain=domain))
    schema[vol.Optional(CONF_BATTERY_CAPACITY, default=values.get(CONF_BATTERY_CAPACITY, 10))] = _num(0, 200, 0.1, "kWh")
    schema[vol.Optional(CONF_BATTERY_EMPTY_SOC, default=values.get(CONF_BATTERY_EMPTY_SOC, DEFAULT_BATTERY_EMPTY_SOC))] = _num(0, 50, 1, "%")
    schema[vol.Optional(CONF_BASE_LOAD, default=values.get(CONF_BASE_LOAD, DEFAULT_BASE_LOAD))] = _num(0.05, 10, 0.05, "kW")
    schema[vol.Optional(CONF_AC_HEATING, default=values.get(CONF_AC_HEATING, False))] = BooleanSelector()
    return vol.Schema(schema)


def _delivery_schema(hass: HomeAssistant, values: dict[str, Any]) -> vol.Schema:
    notify = sorted(
        f"notify.{s}" for s in hass.services.async_services_for_domain("notify")
        if s not in ("notify", "send_message", "persistent_notification")
    )
    return vol.Schema({
        vol.Optional(CONF_NOTIFY, default=values.get(CONF_NOTIFY, [])): SelectSelector(
            SelectSelectorConfig(options=notify, multiple=True, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
        ),
        vol.Required(CONF_TIMES_WEEKDAY, default=values.get(CONF_TIMES_WEEKDAY, DEFAULT_TIMES_WEEKDAY)): str,
        vol.Required(CONF_TIMES_WEEKEND, default=values.get(CONF_TIMES_WEEKEND, DEFAULT_TIMES_WEEKEND)): str,
        vol.Required(CONF_WEEKLY_DAY, default=values.get(CONF_WEEKLY_DAY, DEFAULT_WEEKLY_DAY)): _select(WEEKDAYS + ["off"], "weekday"),
        _opt(CONF_PRICE_IMPORT, values.get(CONF_PRICE_IMPORT)): _num(0, 2, 0.001, "€/kWh"),
        _opt(CONF_PRICE_EXPORT, values.get(CONF_PRICE_EXPORT)): _num(0, 2, 0.001, "€/kWh"),
        _opt(CONF_PRICE_GAS, values.get(CONF_PRICE_GAS)): _num(0, 10, 0.001, "€"),
        _opt(CONF_EVCC_URL, values.get(CONF_EVCC_URL)): str,
        _opt(CONF_FALLBACK_AI_TASK, values.get(CONF_FALLBACK_AI_TASK)): EntitySelector(EntitySelectorConfig(domain="ai_task")),
        vol.Optional(CONF_CLICK_PATH, default=values.get(CONF_CLICK_PATH, DEFAULT_CLICK_PATH)): str,
    })


def _validate_times(user_input: dict[str, Any]) -> dict[str, str]:
    errors = {}
    for key in (CONF_TIMES_WEEKDAY, CONF_TIMES_WEEKEND):
        try:
            for hour, minute in parse_times(user_input.get(key)):
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError
        except ValueError:
            errors[key] = "invalid_times"
    return errors


async def _models(hass: HomeAssistant, data: dict[str, Any]) -> tuple[list[str], str | None]:
    try:
        return await LLMClient(hass, data).async_list_models(), None
    except LLMError as err:
        code = str(err)
        return [], code if code in ("auth_failed", "cannot_connect", "invalid_service_account") else "unknown"


class EnergyBriefingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}
        self._models: list[str] = []
        self._detected: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EnergyBriefingOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_PROVIDER] == PROVIDER_NONE:
                return await self.async_step_sources()
            return await self.async_step_credentials()
        default_lang = "de" if self.hass.config.language.startswith("de") else "en"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_LANGUAGE, default=default_lang): _select(LANGUAGES, "language"),
                vol.Required(CONF_PROVIDER, default=PROVIDER_VERTEX): _select(PROVIDERS, "provider"),
            }),
        )

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        provider = self._data[CONF_PROVIDER]
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            if provider == PROVIDER_AI_TASK:
                return await self.async_step_sources()
            self._models, error = await _models(self.hass, self._data)
            if error:
                errors["base"] = error
            elif not self._models and provider != PROVIDER_OPENAI_COMPAT:
                errors["base"] = "no_models"
            else:
                return await self.async_step_model()
        return self.async_show_form(
            step_id="credentials",
            data_schema=_credentials_schema(provider, self._data),
            errors=errors,
            description_placeholders={"provider": provider},
        )

    async def async_step_model(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_MODEL] = user_input[CONF_MODEL]
            return await self.async_step_sources()
        return self.async_show_form(
            step_id="model",
            data_schema=vol.Schema({
                vol.Required(CONF_MODEL, default=self._models[0] if self._models else ""): SelectSelector(
                    SelectSelectorConfig(options=self._models, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
                ),
            }),
            description_placeholders={"count": str(len(self._models)), "suggested": self._models[0] if self._models else "-"},
        )

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_delivery()
        self._detected = await async_detect(self.hass)
        dash = self._detected.get("energy_dashboard", {})
        yes, no = "✅", "❌"
        return self.async_show_form(
            step_id="sources",
            data_schema=_sources_schema(self._detected),
            description_placeholders={
                "solar": yes if dash.get("solar") else no,
                "grid": yes if dash.get("grid") else no,
                "forecast": yes if dash.get("forecast") else no,
            },
        )

    async def async_step_delivery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_times(user_input)
            if not errors:
                self._options.update(user_input)
                return self.async_create_entry(title="Energy Briefing", data=self._data, options=self._options)
        return self.async_show_form(
            step_id="delivery",
            data_schema=_delivery_schema(self.hass, user_input or self._detected),
            errors=errors,
        )


class EnergyBriefingOptionsFlow(OptionsFlow):
    """Change language, model, sources and delivery later."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._models: list[str] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_sources()
        schema: dict[Any, Any] = {
            vol.Required(CONF_LANGUAGE, default=current.get(CONF_LANGUAGE, "en")): _select(LANGUAGES, "language"),
        }
        provider = current.get(CONF_PROVIDER)
        if provider not in (PROVIDER_NONE, PROVIDER_AI_TASK):
            self._models, _ = await _models(self.hass, current)
            options = self._models or [current.get(CONF_MODEL, "")]
            schema[vol.Required(CONF_MODEL, default=current.get(CONF_MODEL, options[0]))] = SelectSelector(
                SelectSelectorConfig(options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._options.update(user_input)
            for key in ENTITY_FIELDS:
                if key not in user_input:
                    self._options[key] = None
            return await self.async_step_delivery()
        current = {**self.config_entry.options}
        return self.async_show_form(step_id="sources", data_schema=_sources_schema(current))

    async def async_step_delivery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_times(user_input)
            if not errors:
                self._options.update(user_input)
                return self.async_create_entry(data={**self.config_entry.options, **self._options})
        return self.async_show_form(
            step_id="delivery",
            data_schema=_delivery_schema(self.hass, user_input or {**self.config_entry.options}),
            errors=errors,
        )
