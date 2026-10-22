"""Bounded JSON completions with explicit provider configuration and offline fallback."""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from . import ai_activity, env
from .settings import integer

INTERNATIONAL_URL = "https://api.z.ai/api/paas/v4"
MAINLAND_URL = "https://open.bigmodel.cn/api/paas/v4"
API_KEY_ENV = "FUZE_LLM_API_KEY"
TIMEOUT_S = 60.0
MAX_ATTEMPTS = 2
MAX_RETRY_DELAY_S = 30.0
MINIMAL, LOW = "minimal", "low"
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

# provider -> (base URL, default model, native key variable)
PROVIDERS = {
    "zai": (INTERNATIONAL_URL, "glm-5.2", "ZAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "GEMINI_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "fuze-local", "OLLAMA_API_KEY"),
    "custom": ("", "", API_KEY_ENV),
    "disabled": ("", "", ""),
}


class LLMUnavailable(RuntimeError):
    """The model is unconfigured, unreachable, or returned an unusable response."""


def http_failure(status: int, body: Any) -> str:
    """Translate provider diagnostics without echoing prompts, keys or generated text."""
    error = body.get("error", body) if isinstance(body, dict) else {}
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    message = str(error.get("message", ""))[:8000].lower()
    hints = {
        "json_validate_failed": "the model could not produce the required JSON output",
        "context_length_exceeded": "the request exceeds the model's context window",
        "model_decommissioned": "the configured model has been retired; select a supported model",
        "model_not_found": "check the model ID and API base URL",
        "model_permission_blocked": "this API project does not permit the selected model",
        "organization_restricted": "the provider has restricted this API organization",
        "invalid_api_key": "check the selected provider's API key and model permissions",
    }
    hint = hints.get(code) if isinstance(code, str) else None
    if hint is None and status == 400:
        if "max_completion_tokens" in message or "max_tokens" in message:
            hint = "the provider rejected the output-token setting; check FUZE_LLM_MAX_TOKENS"
        elif "reasoning_effort" in message:
            hint = "the selected model rejected the reasoning-effort setting"
        elif any(term in message for term in ("json_schema", "response_format", "additionalproperties")):
            hint = "the provider rejected the structured-output format or schema"
        elif "model" in message and any(term in message for term in ("decommission", "not supported", "does not exist")):
            hint = "check that the configured model is supported by this provider"
        else:
            hint = "the provider rejected the request; check model, token limit and response format"
    elif hint is None and status == 429:
        hint = "rate or token quota exceeded; check the provider's usage limits"
    elif hint is None and status >= 500:
        hint = "provider temporarily unavailable"
    elif hint is None and status in {401, 403}:
        hint = "check the selected provider's API key and model permissions"
    elif hint is None and status == 404:
        hint = "check the model ID and API base URL"
    return f"provider returned HTTP {status}" + (f" ({hint})" if hint else "")


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
    provider = env.get("FUZE_LLM_PROVIDER", "auto").strip().lower()
    if provider == "auto":
        provider = next((name for name in ("groq", "gemini", "zai")
                         if env.get(PROVIDERS[name][2])), "disabled")
        if env.get(API_KEY_ENV):
            provider = "zai"  # Preserve existing installations.
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown FUZE_LLM_PROVIDER: {provider}")
    endpoint, default_model, key_variable = PROVIDERS[provider]
    # Older local installations created this Ollama alias. Their explicit legacy
    # provider setting should keep working until they migrate the setting/model.
    if provider == "ollama" and "FUZE_LLM_PROVIDER" not in os.environ and env.get("CONTINUITY_LLM_PROVIDER") == "ollama":
        default_model = "continuity-local"
    endpoint = (env.get("FUZE_LLM_BASE_URL") or endpoint).rstrip("/")
    model_name = (env.get("FUZE_LLM_MODEL") or default_model).strip()
    # A provider's own credential must win over a leftover generic key from a
    # previous deployment. Custom and legacy configurations still accept the generic key.
    key = (env.get(key_variable) if key_variable else None) or env.get(API_KEY_ENV)
    if provider == "ollama":
        key = key or "ollama"
    if provider != "disabled":
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("FUZE_LLM_BASE_URL must be an HTTP(S) URL without credentials")
        if not model_name:
            raise ValueError("FUZE_LLM_MODEL is required for a custom provider")
    return LLMConfig(provider, endpoint, model_name, key,
                     integer("FUZE_LLM_MAX_TOKENS", 4096, maximum=32768))


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
            "mode": "ai" if config.enabled else "rules",
            "activity": ai_activity.status(config)}


def parse_json(text: str) -> dict[str, Any]:
    value = json.loads(_FENCE.sub("", text).strip())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return value


async def complete_json(
    system: str, user: str, *, temperature: float = 0.0, effort: str = MINIMAL, schema: dict | None = None,
    max_tokens: int | None = None, max_attempts: int | None = None
) -> dict[str, Any]:
    from .designs.context import current
    from .designs.service import digest

    context = current.get()
    if context is None:
        return await _complete_json(system, user, temperature=temperature, effort=effort, schema=schema, max_tokens=max_tokens, max_attempts=max_attempts)
    config = configuration()
    key = digest([config.provider, config.base_url, config.model, config.max_tokens, max_tokens, max_attempts, system, user, temperature, effort, schema])
    # Serialize provider calls within a run and coalesce identical requests. The cache
    # stays execution-local; user briefs never leak through a process-wide cache.
    async with context.llm_lock:
        if key in context.llm_cache:
            ai_activity.notify({"status": "cached", "provider": config.provider, "model": config.model})
            return copy.deepcopy(context.llm_cache[key])
        if context.llm_unavailable:
            raise LLMUnavailable(context.llm_unavailable)
        result = await _complete_json(system, user, temperature=temperature, effort=effort, schema=schema, max_tokens=max_tokens, max_attempts=max_attempts)
        context.llm_cache[key] = copy.deepcopy(result)
        return result


async def _complete_json(system: str, user: str, *, temperature: float, effort: str, schema: dict | None = None,
                         max_tokens: int | None = None, max_attempts: int | None = None) -> dict[str, Any]:
    from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
    from .designs.context import current

    config = configuration()
    if max_tokens is not None and (isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1):
        raise ValueError("max_tokens must be a positive integer")
    output_limit = min(config.max_tokens, max_tokens) if max_tokens is not None else config.max_tokens
    if max_attempts is not None and (isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1):
        raise ValueError("max_attempts must be a positive integer")
    attempts = min(MAX_ATTEMPTS, max_attempts) if max_attempts is not None else MAX_ATTEMPTS
    if not config.enabled:
        raise LLMUnavailable("AI is not configured; using the rule-based fallback.")
    parameters: dict[str, Any] = {
        "model": config.model,
        "messages": [{"role": "system", "content": system + "\nReturn only a JSON object."},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": output_limit,
    }
    if config.provider == "zai":
        parameters["reasoning_effort"] = effort
    elif config.provider in {"groq", "gemini", "ollama"}:
        parameters["response_format"] = {"type": "json_object"}
    if config.provider == "groq":
        parameters["max_completion_tokens"] = parameters.pop("max_tokens")
        if config.model in {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}:
            # GPT-OSS accepts low/medium/high, not the legacy "minimal" setting.
            # Low reasoning leaves more of the completion budget for the JSON result.
            parameters["reasoning_effort"] = effort if effort in {"low", "medium", "high"} else "low"
            if schema:
                parameters["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "fuze_response", "strict": True, "schema": schema}}
    failure = "unusable response"
    context = current.get()
    async with AsyncOpenAI(api_key=config.key, base_url=config.base_url,
                           timeout=TIMEOUT_S, max_retries=0) as client:
        for attempt in range(attempts):
            reservation = (len(system) + len(user) + (len(json.dumps(schema)) if schema else 0)) // 3 + output_limit
            if context:
                if (context.llm_calls >= integer("FUZE_LLM_MAX_CALLS", 40, maximum=100)
                        or context.llm_tokens + reservation > integer("FUZE_LLM_TOKEN_BUDGET", 120000, maximum=1000000)):
                    raise LLMUnavailable("The run's AI request or token budget is exhausted; unresolved work remains visible.")
                context.llm_calls += 1
                context.llm_tokens += reservation
            delay = float(1 + attempt)
            receipt = ai_activity.started(config, context.thread_id if context else None)
            http_status = None
            try:
                response = await client.chat.completions.create(**parameters)
                http_status = 200
                result = parse_json(response.choices[0].message.content or "")
                usage = response.usage
                if context and usage and isinstance(usage.total_tokens, int):
                    context.llm_tokens += usage.total_tokens - reservation
                ai_activity.finished(receipt, "success", http_status=200,
                                     tokens=usage.total_tokens if usage else None)
                return result
            except APIStatusError as exc:
                failure = http_failure(exc.status_code, exc.body)
                ai_activity.finished(receipt, "error", http_status=exc.status_code, reason=failure)
                if exc.status_code not in {408, 409, 429} and exc.status_code < 500:
                    if context:
                        context.llm_unavailable = f"{config.provider}: {failure}."
                    break
                # Respect a short provider cooldown. A daily quota or long wait
                # should use the fallback, not immediately repeat the same request.
                try:
                    retry_after = float(exc.response.headers.get("retry-after", ""))
                except ValueError:
                    retry_after = None
                if retry_after is not None and math.isfinite(retry_after) and retry_after >= 0:
                    if retry_after > MAX_RETRY_DELAY_S:
                        if context:
                            context.llm_unavailable = f"{config.provider}: {failure}."
                        break
                    delay = max(delay, retry_after)
            except (APIConnectionError, APITimeoutError):
                failure = "provider connection failed or timed out"
                ai_activity.finished(receipt, "error", reason=failure)
            except (ValueError, IndexError, TypeError):
                failure = "provider did not return a valid JSON object"
                ai_activity.finished(receipt, "error", http_status=http_status, reason=failure)
            except asyncio.CancelledError:
                ai_activity.finished(receipt, "cancelled", http_status=http_status)
                raise
            except Exception:
                ai_activity.finished(receipt, "error", http_status=http_status,
                                     reason="unexpected provider response or client error")
                raise
            if attempt + 1 < attempts:
                await asyncio.sleep(delay)
    # Never echo provider response bodies, which can contain prompts or secrets.
    if context and "HTTP 429" in failure:
        context.llm_unavailable = f"{config.provider}: {failure}."
    raise LLMUnavailable(f"{config.provider}: {failure}.")
