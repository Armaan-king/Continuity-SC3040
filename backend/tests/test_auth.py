"""Registration, login, and the session cookie — over real HTTP.

Skipped unless `CONTINUITY_TEST_DB` is set, same as `test_store.py`.

The properties worth pinning are the ones that are silent when wrong. **A wrong password
and an unknown email must answer identically**, or the login form becomes an account
enumerator. **The cookie must be `HttpOnly`**, or any injected script can read a session.
And **`/auth/me` without a cookie must be 401 rather than an empty user**, because a
route that answers "nobody" is a route the frontend will happily treat as signed in.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import pytest

from continuity.api.app import app
from continuity.api.store import Store

DB_URL = os.environ.get("CONTINUITY_TEST_DB")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="set CONTINUITY_TEST_DB to run the auth tests"
)


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def signed_out():
    """An app with a store attached and empty tables, plus a cookie-keeping client.

    `httpx.ASGITransport` does not run lifespan events, so the store is attached here
    the way the lifespan would attach it.
    """
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(DB_URL, min_size=1, max_size=3, open=False) as pool:
        await pool.open()
        store = Store(pool)
        await store.setup()
        async with pool.connection() as conn:
            await conn.execute("TRUNCATE users, sessions, projects, threads CASCADE")

        previous = app.state.store
        app.state.store = store
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30.0
            ) as http:
                yield http
        finally:
            app.state.store = previous


CREDENTIALS = {"email": "sparsh@example.com", "password": "correct horse battery"}


# ── registration ──────────────────────────────────────────────────────────────


def test_registering_signs_you_in():
    async def go():
        async with signed_out() as http:
            created = await http.post("/auth/register", json=CREDENTIALS)
            return created, await http.get("/auth/me")

    created, me = run(go())
    assert created.status_code == 201
    assert created.json()["email"] == "sparsh@example.com"
    assert me.status_code == 200
    assert me.json()["email"] == "sparsh@example.com"


def test_a_new_account_has_not_been_onboarded():
    async def go():
        async with signed_out() as http:
            response = await http.post("/auth/register", json=CREDENTIALS)
            return response.json()

    assert run(go())["onboarded"] is False


def test_the_session_cookie_is_not_readable_by_script():
    async def go():
        async with signed_out() as http:
            return await http.post("/auth/register", json=CREDENTIALS)

    header = run(go()).headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header


def test_a_malformed_email_is_refused():
    async def go():
        async with signed_out() as http:
            return await http.post(
                "/auth/register", json={"email": "not-an-email", "password": "a-good-password"}
            )

    assert run(go()).status_code == 422


def test_a_short_password_is_refused():
    async def go():
        async with signed_out() as http:
            return await http.post(
                "/auth/register", json={"email": "a@example.com", "password": "short"}
            )

    assert run(go()).status_code == 422


def test_a_duplicate_email_is_refused():
    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            return await http.post("/auth/register", json=CREDENTIALS)

    assert run(go()).status_code == 409


def test_the_password_is_never_stored_in_the_clear():
    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            user = await app.state.store.user_by_email(CREDENTIALS["email"])
            return user.password_hash

    stored = run(go())
    assert CREDENTIALS["password"] not in stored
    assert stored.startswith("$argon2")


# ── login ─────────────────────────────────────────────────────────────────────


def test_login_with_the_right_password_signs_you_in():
    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            await http.post("/auth/logout")
            signed_in = await http.post("/auth/login", json=CREDENTIALS)
            return signed_in, await http.get("/auth/me")

    signed_in, me = run(go())
    assert signed_in.status_code == 200
    assert me.status_code == 200


def test_login_is_case_insensitive_on_the_email():
    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            await http.post("/auth/logout")
            return await http.post(
                "/auth/login",
                json={"email": "SPARSH@EXAMPLE.COM", "password": CREDENTIALS["password"]},
            )

    assert run(go()).status_code == 200


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable():
    """Otherwise the login form tells an attacker which addresses have accounts."""

    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            await http.post("/auth/logout")
            wrong = await http.post(
                "/auth/login", json={"email": CREDENTIALS["email"], "password": "wrong-password"}
            )
            unknown = await http.post(
                "/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}
            )
            return wrong, unknown

    wrong, unknown = run(go())
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_a_failed_login_does_not_sign_you_in():
    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            await http.post("/auth/logout")
            await http.post(
                "/auth/login", json={"email": CREDENTIALS["email"], "password": "wrong-password"}
            )
            return await http.get("/auth/me")

    assert run(go()).status_code == 401


# ── the session ───────────────────────────────────────────────────────────────


def test_me_without_a_cookie_is_a_401():
    async def go():
        async with signed_out() as http:
            return await http.get("/auth/me")

    assert run(go()).status_code == 401


def test_a_forged_cookie_is_a_401():
    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            http.cookies.set("continuity_session", "made-up-token")
            return await http.get("/auth/me")

    assert run(go()).status_code == 401


def test_logout_ends_the_session_on_the_server_not_only_the_browser():
    """Clearing the cookie is not enough — a copied token must stop working."""

    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            stolen = http.cookies.get("continuity_session")
            await http.post("/auth/logout")

            http.cookies.set("continuity_session", stolen)
            return await http.get("/auth/me")

    assert run(go()).status_code == 401


def test_a_stale_cookie_on_an_instance_with_no_accounts_reads_as_signed_out():
    """Not 503. A browser holding a cookie from another instance is simply signed out."""

    async def go():
        async with signed_out() as http:
            await http.post("/auth/register", json=CREDENTIALS)
            previous = app.state.store
            app.state.store = None  # single-user local mode
            try:
                return await http.get("/auth/me")
            finally:
                app.state.store = previous

    assert run(go()).status_code == 401
