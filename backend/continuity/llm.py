"""Bounded JSON completions with explicit provider configuration and offline fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from . import env
from .settings import integer

log = logging.getLogger(__name__)
INTERNATIONAL_URL = "https://api.z.ai/api/paas/v4"
MAINLAND_URL = "https://open.bigmodel.cn/api/paas/v4"
API_KEY_ENV = "CONTINUITY_LLM_API_KEY"
TIMEOUT_S = 60.0
MAX_ATTEMPTS = 2
MINIMAL, LOW = "minimal", "low"
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

# provider -> (base URL, default model, native key variable)
PROVIDERS = {
    "zai": (INTERNATIONAL_URL, "glm-5.2", "ZAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "continuity-local", "OLLAMA_API_KEY"),
    "custom": ("", "", API_KEY_ENV),
    "disabled": ("", "", ""),
}


class LLMUnavailable(RuntimeError):
    """The model is unconfigured, unreachable, or returned an unusable response."""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str
    key: str | None
    max_tokens: int

    @property
    def enabled(self) -> bool:
        return self.provider != "disabled" and bool(self.key)


def configuration() -> LLMConfig:
    env.load()
    provider = os.environ.get("CONTINUITY_LLM_PROVIDER", "auto").strip().lower()
    if provider == "auto":
        provider = next((name for name in ("groq", "gemini", "zai")
                         if os.environ.get(PROVIDERS[name][2])), "disabled")
        if os.environ.get(API_KEY_ENV):
            provider = "zai"  # Preserve existing installations.
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown CONTINUITY_LLM_PROVIDER: {provider}")
    endpoint, default_model, key_variable = PROVIDERS[provider]
    endpoint = (os.environ.get("CONTINUITY_LLM_BASE_URL") or endpoint).rstrip("/")
    model_name = (os.environ.get("CONTINUITY_LLM_MODEL") or default_model).strip()
    key = os.environ.get(API_KEY_ENV) or (os.environ.get(key_variable) if key_variable else None)
    if provider == "ollama":
        key = key or "ollama"
    if provider != "disabled":
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("CONTINUITY_LLM_BASE_URL must be an HTTP(S) URL without credentials")
        if not model_name:
            raise ValueError("CONTINUITY_LLM_MODEL is required for a custom provider")
    return LLMConfig(provider, endpoint, model_name, key,
                     integer("CONTINUITY_LLM_MAX_TOKENS", 4096, maximum=32768))


def base_url() -> str:
    return configuration().base_url


def model() -> str:
    return configuration().model


def api_key() -> str | None:
    config = configuration()
    return config.key if config.enabled else None


def available() -> bool:
    """Configured, not a claim that a network health check has succeeded."""
    return configuration().enabled


def public_status() -> dict[str, Any]:
    config = configuration()
    return {"configured": config.enabled, "provider": config.provider,
            "model": config.model if config.enabled else None,
            "mode": "ai" if config.enabled else "rules"}


def parse_json(text: str) -> dict[str, Any]:
    value = json.loads(_FENCE.sub("", text).strip())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return value


async def complete_json(
    system: str, user: str, *, temperature: float = 0.0, effort: str = MINIMAL
) -> dict[str, Any]:
    from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

    config = configuration()
    if not config.enabled:
        raise LLMUnavailable("AI is not configured; using the rule-based fallback.")
    parameters: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "system", "content": system + "\nReturn only a JSON object."},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": config.max_tokens,
    }
    if config.provider == "zai":
        parameters["reasoning_effort"] = effort
    elif config.provider in {"groq", "gemini", "ollama"}:
        parameters["response_format"] = {"type": "json_object"}
    started = time.monotonic()
    failure = "unusable response"
    async with AsyncOpenAI(api_key=config.key, base_url=config.base_url,
                           timeout=TIMEOUT_S, max_retries=0) as client:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.chat.completions.create(**parameters)
                result = parse_json(response.choices[0].message.content or "")
                usage = response.usage
                log.info("llm provider=%s model=%s elapsed_ms=%d tokens=%s",
                         config.provider, config.model, (time.monotonic() - started) * 1000,
                         usage.total_tokens if usage else "unknown")
                return result
            except APIStatusError as exc:
                failure = f"provider returned HTTP {exc.status_code}"
                if exc.status_code not in {408, 409, 429} and exc.status_code < 500:
                    break
            except (APIConnectionError, APITimeoutError):
                failure = "provider connection failed or timed out"
            except (ValueError, IndexError):
                failure = "provider did not return a valid JSON object"
            if attempt + 1 < MAX_ATTEMPTS:
                await asyncio.sleep(1 + attempt)
    # Never echo provider response bodies, which can contain prompts or secrets.
    raise LLMUnavailable(f"{config.provider}: {failure}. Check the server's AI configuration.")
