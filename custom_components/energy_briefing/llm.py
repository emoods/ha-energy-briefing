"""LLM provider clients: model discovery and structured text generation."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_LOCATION,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_SERVICE_ACCOUNT,
    DEFAULT_LOCATION,
    PROVIDER_AI_TASK,
    PROVIDER_ANTHROPIC,
    PROVIDER_GEMINI,
    PROVIDER_NONE,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_VERTEX,
)

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=90)

# Known Vertex models, used when the Model Garden listing is not permitted.
VERTEX_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]

_EXCLUDE = re.compile(
    r"(embed|image|imagen|veo|tts|audio|live|realtime|transcribe|whisper|dall|"
    r"moderation|search|computer|robotics|cyber|aqa|vision|omni|learnlm|gemma)",
    re.IGNORECASE,
)

RESPONSE_FIELDS = {
    "title": "Short headline, max 60 characters, starting with one fitting emoji.",
    "summary": "Push notification text, 2-3 short sentences, max 280 characters.",
    "details": "Markdown bullet list (max 8 bullets) with only the relevant points.",
}


class LLMError(Exception):
    """Raised when a provider call fails."""


def _version_key(name: str) -> tuple:
    nums = [int(n) for n in re.findall(r"\d+", name)]
    return tuple(nums) or (0,)


def rank_models(provider: str, models: list[str]) -> list[str]:
    """Sort models so the best cheap/fast choice comes first."""
    models = sorted({m for m in models if m and not _EXCLUDE.search(m)})

    def pref(name: str) -> int:
        lname = name.lower()
        unstable = any(t in lname for t in ("preview", "exp", "latest", "beta"))
        if provider in (PROVIDER_VERTEX, PROVIDER_GEMINI):
            score = 3 if "flash-lite" in lname else 2 if "flash" in lname else 1 if "gemini" in lname else 0
        elif provider == PROVIDER_OPENAI:
            score = 3 if ("mini" in lname and lname.startswith("gpt")) else 2 if "nano" in lname else 1 if lname.startswith("gpt") else 0
        elif provider == PROVIDER_ANTHROPIC:
            score = 3 if "haiku" in lname else 2 if "sonnet" in lname else 1
        else:
            score = 1
        return score * 2 - (1 if unstable else 0)

    return sorted(models, key=lambda m: (pref(m), _version_key(m)), reverse=True)


def _service_account(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get(CONF_SERVICE_ACCOUNT)
    if isinstance(raw, dict):
        return raw
    try:
        info = json.loads(raw or "")
    except ValueError as err:
        raise LLMError("invalid_service_account") from err
    if info.get("type") != "service_account" or not info.get("project_id"):
        raise LLMError("invalid_service_account")
    return info


class LLMClient:
    """Provider-agnostic client."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        self.hass = hass
        self.data = data
        self.provider = data.get(CONF_PROVIDER, PROVIDER_NONE)
        self._token: str | None = None
        self._token_expiry = 0.0

    @property
    def session(self) -> ClientSession:
        return async_get_clientsession(self.hass)

    # ------------------------------------------------------------------ auth
    async def _vertex_token(self) -> str:
        if self._token and self._token_expiry - time.time() > 120:
            return self._token
        info = _service_account(self.data)

        def _refresh() -> tuple[str, float]:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(Request())
            expiry = creds.expiry.timestamp() if creds.expiry else time.time() + 3000
            return creds.token, expiry

        try:
            self._token, self._token_expiry = await self.hass.async_add_executor_job(_refresh)
        except Exception as err:  # noqa: BLE001 - google-auth raises many types
            raise LLMError("auth_failed") from err
        return self._token

    def _vertex_host(self) -> tuple[str, str, str]:
        info = _service_account(self.data)
        location = self.data.get(CONF_LOCATION) or DEFAULT_LOCATION
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        return host, info["project_id"], location

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            async with self.session.request(method, url, timeout=_TIMEOUT, **kwargs) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    raise LLMError("auth_failed")
                if resp.status >= 400:
                    raise LLMError(f"HTTP {resp.status}: {text[:300]}")
                return json.loads(text) if text else {}
        except (ClientError, TimeoutError) as err:
            raise LLMError("cannot_connect") from err

    # -------------------------------------------------------------- listing
    async def async_list_models(self) -> list[str]:
        """Return available models, best suggestion first."""
        provider = self.provider
        if provider in (PROVIDER_NONE, PROVIDER_AI_TASK):
            return []
        models: list[str] = []
        if provider == PROVIDER_VERTEX:
            token = await self._vertex_token()
            host, project, _ = self._vertex_host()
            try:
                result = await self._request(
                    "GET",
                    f"https://{host}/v1beta1/publishers/google/models?pageSize=300",
                    headers={"Authorization": f"Bearer {token}", "x-goog-user-project": project},
                )
                for item in result.get("publisherModels", []):
                    name = item.get("name", "").split("/")[-1]
                    if name.startswith("gemini"):
                        models.append(name)
            except LLMError as err:
                _LOGGER.debug("Model Garden listing failed (%s), using known models", err)
            if not models:
                models = list(VERTEX_FALLBACK_MODELS)
        elif provider == PROVIDER_GEMINI:
            result = await self._request(
                "GET",
                "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                headers={"x-goog-api-key": self.data[CONF_API_KEY]},
            )
            for item in result.get("models", []):
                if "generateContent" in item.get("supportedGenerationMethods", []):
                    models.append(item["name"].removeprefix("models/"))
        elif provider in (PROVIDER_OPENAI, PROVIDER_OPENAI_COMPAT):
            base = (self.data.get(CONF_BASE_URL) or "https://api.openai.com/v1").rstrip("/")
            headers = {}
            if self.data.get(CONF_API_KEY):
                headers["Authorization"] = f"Bearer {self.data[CONF_API_KEY]}"
            result = await self._request("GET", f"{base}/models", headers=headers)
            models = [m.get("id", "") for m in result.get("data", [])]
            if provider == PROVIDER_OPENAI_COMPAT:
                return sorted(m for m in models if m)
        elif provider == PROVIDER_ANTHROPIC:
            result = await self._request(
                "GET",
                "https://api.anthropic.com/v1/models?limit=100",
                headers={"x-api-key": self.data[CONF_API_KEY], "anthropic-version": "2023-06-01"},
            )
            models = [m.get("id", "") for m in result.get("data", [])]
        return rank_models(provider, models)

    # ----------------------------------------------------------- generation
    async def async_generate(self, system: str, prompt: str) -> dict[str, str]:
        """Generate {title, summary, details}."""
        provider = self.provider
        model = self.data.get(CONF_MODEL)
        if provider == PROVIDER_NONE:
            raise LLMError("no_provider")
        if provider == PROVIDER_AI_TASK:
            return await async_generate_ai_task(self.hass, self.data.get(CONF_AI_TASK_ENTITY), system, prompt)

        if provider in (PROVIDER_VERTEX, PROVIDER_GEMINI):
            schema = {
                "type": "OBJECT",
                "properties": {k: {"type": "STRING", "description": v} for k, v in RESPONSE_FIELDS.items()},
                "required": list(RESPONSE_FIELDS),
            }
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.9,
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                },
            }
            if provider == PROVIDER_VERTEX:
                token = await self._vertex_token()
                host, project, location = self._vertex_host()
                url = f"https://{host}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"
                headers = {"Authorization": f"Bearer {token}"}
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {"x-goog-api-key": self.data[CONF_API_KEY]}
            result = await self._request("POST", url, json=body, headers=headers)
            try:
                parts = result["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            except (KeyError, IndexError) as err:
                raise LLMError("empty_response") from err
            return _parse(text)

        if provider in (PROVIDER_OPENAI, PROVIDER_OPENAI_COMPAT):
            base = (self.data.get(CONF_BASE_URL) or "https://api.openai.com/v1").rstrip("/")
            headers = {}
            if self.data.get(CONF_API_KEY):
                headers["Authorization"] = f"Bearer {self.data[CONF_API_KEY]}"
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system + "\n" + _json_hint()},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            }
            result = await self._request("POST", f"{base}/chat/completions", json=body, headers=headers)
            try:
                text = result["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as err:
                raise LLMError("empty_response") from err
            return _parse(text)

        if provider == PROVIDER_ANTHROPIC:
            body = {
                "model": model,
                "max_tokens": 2048,
                "system": system + "\n" + _json_hint(),
                "messages": [{"role": "user", "content": prompt}],
            }
            result = await self._request(
                "POST",
                "https://api.anthropic.com/v1/messages",
                json=body,
                headers={"x-api-key": self.data[CONF_API_KEY], "anthropic-version": "2023-06-01"},
            )
            text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
            return _parse(text)

        raise LLMError("unknown_provider")


def _json_hint() -> str:
    fields = ", ".join(f'"{k}": {v}' for k, v in RESPONSE_FIELDS.items())
    return f"Reply with a single JSON object only, with these string fields: {fields}"


def _parse(text: str) -> dict[str, str]:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise LLMError("invalid_json")
    try:
        data = json.loads(match.group(0))
    except ValueError as err:
        raise LLMError("invalid_json") from err
    out = {k: str(data.get(k) or "").strip() for k in RESPONSE_FIELDS}
    if not out["title"] or not out["summary"]:
        raise LLMError("invalid_json")
    return out


async def async_generate_ai_task(hass: HomeAssistant, entity_id: str | None, system: str, prompt: str) -> dict[str, str]:
    """Use a Home Assistant AI Task entity (e.g. Google Generative AI)."""
    structure = {
        key: {"selector": {"text": {"multiline": key == "details"}}, "description": desc, "required": True}
        for key, desc in RESPONSE_FIELDS.items()
    }
    data: dict[str, Any] = {
        "task_name": "energy briefing",
        "instructions": f"{system}\n\n{prompt}",
        "structure": structure,
    }
    if entity_id:
        data["entity_id"] = entity_id
    try:
        result = await hass.services.async_call(
            "ai_task", "generate_data", data, blocking=True, return_response=True
        )
    except Exception as err:  # noqa: BLE001 - surface any AI task failure
        raise LLMError(f"ai_task: {err}") from err
    payload = (result or {}).get("data") or {}
    if isinstance(payload, str):
        return _parse(payload)
    return _parse(json.dumps(payload))
