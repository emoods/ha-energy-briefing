"""Constants for Energy Briefing."""

from __future__ import annotations

DOMAIN = "energy_briefing"

# Config entry data keys
CONF_LANGUAGE = "language"
CONF_PROVIDER = "provider"
CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_SERVICE_ACCOUNT = "service_account_json"
CONF_LOCATION = "location"
CONF_MODEL = "model"
CONF_AI_TASK_ENTITY = "ai_task_entity"

# Options keys
CONF_WEATHER = "weather_entity"
CONF_BATTERY_SOC = "battery_soc_entity"
CONF_BATTERY_CAPACITY = "battery_capacity_kwh"
CONF_BATTERY_EMPTY_SOC = "battery_empty_soc"
CONF_BASE_LOAD = "base_load_kw"
CONF_CAR_CONNECTED = "car_connected_entity"
CONF_CAR_SOC = "car_soc_entity"
CONF_WASHER = "washer_entity"
CONF_DRYER = "dryer_entity"
CONF_DISHWASHER = "dishwasher_entity"
CONF_AC_HEATING = "ac_heating"
CONF_PRICE_IMPORT = "price_import"
CONF_PRICE_EXPORT = "price_export"
CONF_PRICE_GAS = "price_gas"
CONF_EVCC_URL = "evcc_url"
CONF_NOTIFY = "notify_services"
CONF_TIMES_WEEKDAY = "times_weekday"
CONF_TIMES_WEEKEND = "times_weekend"
CONF_WEEKLY_DAY = "weekly_review_day"
CONF_CLICK_PATH = "click_path"
CONF_FALLBACK_AI_TASK = "fallback_ai_task_entity"

PROVIDER_VERTEX = "vertex"
PROVIDER_GEMINI = "gemini_api"
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI_COMPAT = "openai_compatible"
PROVIDER_AI_TASK = "ha_ai_task"
PROVIDER_NONE = "none"

PROVIDERS = [
    PROVIDER_VERTEX,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_AI_TASK,
    PROVIDER_NONE,
]

LANGUAGES = ["en", "de"]

DEFAULT_LOCATION = "global"
DEFAULT_BASE_LOAD = 0.55
DEFAULT_BATTERY_EMPTY_SOC = 7
DEFAULT_TIMES_WEEKDAY = "08:00, 12:00, 20:00"
DEFAULT_TIMES_WEEKEND = "09:00, 20:00"
DEFAULT_WEEKLY_DAY = "sun"
DEFAULT_CLICK_PATH = "/lovelace/0"

KIND_MORNING = "morning"
KIND_MIDDAY = "midday"
KIND_EVENING = "evening"
KINDS = [KIND_MORNING, KIND_MIDDAY, KIND_EVENING]

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

EVENT_BRIEFING = f"{DOMAIN}_created"
STORAGE_VERSION = 1

GOOD_CONDITIONS = {"sunny", "clear-night"}
FAIR_CONDITIONS = {"partlycloudy"}
