"""Renaming the product must preserve configuration, sessions and saved runs."""

import asyncio
import importlib
import os
from unittest.mock import AsyncMock

import httpx
import ormsgpack
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from fuze import env, llm
from fuze.api import auth
from fuze.engine.models import Requirements
from fuze.settings import Settings


@pytest.fixture
def clean_brand_env(monkeypatch):
    monkeypatch.setattr(env, "_loaded", True)
    for name in tuple(os.environ):
        if name.startswith(("FUZE_", "CONTINUITY_")) or name == "DATABASE_URL":
            monkeypatch.delenv(name)


def test_old_render_configuration_still_selects_same_provider(clean_brand_env, monkeypatch):
    monkeypatch.setenv("CONTINUITY_LLM_PROVIDER", "groq")
    monkeypatch.setenv("CONTINUITY_LLM_MODEL", "existing-model")
    monkeypatch.setenv("CONTINUITY_LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("CONTINUITY_LLM_MAX_TOKENS", "2048")
    monkeypatch.setenv("CONTINUITY_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test.invalid/fuze")
    monkeypatch.setenv("GROQ_API_KEY", "local-test-only")
    config = llm.configuration()
    assert (config.provider, config.model, config.max_tokens) == ("groq", "existing-model", 2048)
    assert config.key == "local-test-only"
    assert Settings.read().cookie_secure
    monkeypatch.setenv("FUZE_LLM_PROVIDER", "disabled")
    assert not llm.configuration().enabled


def test_new_prefix_wins_even_when_empty(clean_brand_env, monkeypatch):
    monkeypatch.setenv("CONTINUITY_ORIGINS", "https://old.example")
    monkeypatch.setenv("FUZE_ORIGINS", "")
    assert Settings.read().origins == ()
    monkeypatch.delenv("FUZE_ORIGINS")
    assert Settings.read().origins == ("https://old.example",)
    monkeypatch.setenv("CONTINUITY_CACHE_DIR", "/legacy-cache")
    assert env.cache_dir("normalized").as_posix() == "/legacy-cache/normalized"


def test_old_local_model_alias_survives_until_configuration_is_migrated(clean_brand_env, monkeypatch):
    monkeypatch.setenv("CONTINUITY_LLM_PROVIDER", "ollama")
    assert llm.configuration().model == "continuity-local"
    monkeypatch.setenv("FUZE_LLM_PROVIDER", "ollama")
    assert llm.configuration().model == "fuze-local"
    monkeypatch.setenv("FUZE_LLM_MODEL", "my-local-model")
    assert llm.configuration().model == "my-local-model"


@pytest.mark.parametrize("module,names", [
    ("engine.models", ["Requirements", "Slot", "Rail", "PartSpec", "Evidence", "Verdict", "Board"]),
    ("planner.plan", ["Plan"]), ("parts.search", ["Candidate"]),
])
def test_saved_class_names_resolve_to_canonical_classes(module, names):
    old = importlib.import_module(f"continuity.{module}")
    new = importlib.import_module(f"fuze.{module}")
    for name in names:
        assert getattr(old, name) is getattr(new, name)


def test_checkpoint_from_previous_package_loads_without_changing_its_values():
    # This is the dataclass extension format persisted by JsonPlusSerializer.
    payload = ormsgpack.packb(ormsgpack.Ext(2, ormsgpack.packb((
        "continuity.engine.models", "Requirements", {"input_voltage": 5.0},
    ))))
    serializer = JsonPlusSerializer(allowed_msgpack_modules=[("continuity.engine.models", "Requirements")])
    result = serializer.loads_typed(("msgpack", payload))
    assert isinstance(result, Requirements)
    assert result.input_voltage == 5.0
    assert b"continuity" not in serializer.dumps_typed(result)[1]


def test_old_session_is_promoted_and_logout_revokes_both_names(clean_brand_env):
    async def check():
        app = FastAPI()
        app.include_router(auth.router)
        store = AsyncMock()
        # Only fields required by /me are needed; no external database or provider.
        from types import SimpleNamespace
        store.user_for_token.return_value = SimpleNamespace(id="u", email="u@example.com", onboarded_at=None)
        app.state.store = store
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            client.cookies.set(auth.LEGACY_COOKIE, "old-token", domain="test.local", path="/")
            response = await client.get("/auth/me")
            assert response.status_code == 200
            assert client.cookies.get(auth.COOKIE) == "old-token"
            assert client.cookies.get(auth.LEGACY_COOKIE) is None
            client.cookies.set(auth.LEGACY_COOKIE, "other-token", domain="test.local", path="/")
            assert (await client.post("/auth/logout")).status_code == 204
            assert {call.args[0] for call in store.delete_session.await_args_list} == {"old-token", "other-token"}
            assert not client.cookies

    asyncio.run(check())
