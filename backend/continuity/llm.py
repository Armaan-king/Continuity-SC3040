"""GLM 5.2, via its OpenAI-compatible endpoint.

Z.ai serves an OpenAI-shaped API, so this is a thin shim over the `openai` SDK rather
than a new client. Two regions, two endpoints, and keys do **not** work across them:

    international   https://api.z.ai/api/paas/v4        sign up at z.ai/model-api
    mainland China  https://open.bigmodel.cn/api/paas/v4  sign up at open.bigmodel.cn

The default is international. Override with `CONTINUITY_LLM_BASE_URL`.

## Structured output is ours to enforce

The platform's guidance for JSON output is *prompt-driven* — ask for fields in the
prompt and parse what comes back. There is no server-side `json_schema` guarantee to
lean on. So every caller validates the response against its own declared fields and
rejects anything else; see `parts.normalize`. That is the same rule the rest of the
system runs on: the model fills declared fields, it never adds one.

## Missing key is a degraded mode, not a crash

Without `CONTINUITY_LLM_API_KEY` the system still runs. Normalisation falls back to the
fields readable straight from the distributor payload, and every electrical rule that
needs a parsed value reports *could not check* rather than passing quietly. Fewer
answers, no wrong ones.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from . import env

# Z.ai serves two regions with *non-interchangeable* keys. A key from one against the
# other's endpoint returns 401 with nothing to explain why, so the default is the
# international platform and the mainland one is an explicit override.
INTERNATIONAL_URL = "https://api.z.ai/api/paas/v4"
MAINLAND_URL = "https://open.bigmodel.cn/api/paas/v4"



def base_url() -> str:
    env.load()
    return os.environ.get("CONTINUITY_LLM_BASE_URL") or INTERNATIONAL_URL


def model() -> str:
    env.load()
    return os.environ.get("CONTINUITY_LLM_MODEL") or "glm-5.2"


API_KEY_ENV = "CONTINUITY_LLM_API_KEY"

TIMEOUT_S = 60.0
MAX_ATTEMPTS = 2

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class LLMUnavailable(RuntimeError):
    """No key configured, or the provider could not be reached."""


def api_key() -> str | None:
    env.load()
    return os.environ.get(API_KEY_ENV) or None


def available() -> bool:
    return api_key() is not None


def _client():
    from openai import AsyncOpenAI  # imported lazily so the engine never pulls it in

    key = api_key()
    if key is None:
        raise LLMUnavailable(
            f"{API_KEY_ENV} is not set. Continuity runs without it, but every field "
            f"that needs parsing will report as unchecked."
        )
    return AsyncOpenAI(
        api_key=key,
        base_url=base_url(),
        timeout=TIMEOUT_S,
        # Z.ai's own examples send this; it selects English error messages, which is
        # the difference between a debuggable 400 and an inscrutable one.
        default_headers={"Accept-Language": "en-US,en"},
    )


def parse_json(text: str) -> dict[str, Any]:
    """Parse a model's JSON reply, tolerating a markdown fence around it.

    Tolerating the fence is not the same as tolerating anything: the result still has
    to be a JSON object, and the caller still validates every key in it.
    """
    stripped = _FENCE.sub("", text).strip()
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return value


MINIMAL, LOW = "minimal", "low"
"""Reasoning budgets.

Measured on the planner prompt: default **41.2s**, `low` 13.2s, `minimal` **5.8s** —
and minimal returned *more* output, not less. Extraction tasks (planning a slot list,
typing a parameter block) are not reasoning tasks, and paying a seven-fold latency
penalty for deliberation they do not use is most of why a run took two minutes.

The reviewer is the exception: choosing between "swap this" and "this whole kind of
part is wrong" is the one judgement here worth thinking about.
"""


async def complete_json(
    system: str, user: str, *, temperature: float = 0.0, effort: str = MINIMAL
) -> dict[str, Any]:
    """One completion, expected back as a JSON object.

    `temperature=0` because a value parsed one way in rehearsal and another way on
    stage changes the board underneath you. Determinism here is worth more than
    variety.
    """
    client = _client()
    last: Exception | None = None

    for _ in range(MAX_ATTEMPTS):
        try:
            response = await client.chat.completions.create(
                model=model(),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                reasoning_effort=effort,
            )
            return parse_json(response.choices[0].message.content or "")
        except Exception as error:
            last = error

    raise LLMUnavailable(f"{model()}: {last}")
