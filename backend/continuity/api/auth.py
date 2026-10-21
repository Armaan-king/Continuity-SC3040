"""Accounts and the session cookie.

## Why an opaque session and not a JWT

A JWT would have to be revoked by keeping a list of the ones that are no longer valid,
which is a session table with extra steps and a signature to get wrong. An opaque random
token *is* the reference to a row, so logging out is a `DELETE` and there is no window
where a stolen token outlives the logout that was supposed to end it.

Only the SHA-256 of the token is stored. The cookie value itself is never written down.

## The two settings that will break on deploy day

`SameSite=Lax` is right when the UI and the API share a registrable domain — ports are
not part of that comparison, so `localhost:5173 → localhost:8000` is same-site and works
unchanged in development. Split them across domains (`*.vercel.app` calling `*.fly.dev`)
and Lax stops sending the cookie at all: that deployment needs
`CONTINUITY_COOKIE_SAMESITE=none` together with `CONTINUITY_COOKIE_SECURE=1`, because
browsers refuse `SameSite=None` without `Secure`.

Both default to the development setting, so nothing has to be configured to run locally
and the production values are a deliberate act.
"""

from __future__ import annotations

import os

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from .store import SESSION_TTL_SECONDS, EmailTaken, Store, User

COOKIE = "continuity_session"

MIN_PASSWORD_LENGTH = 8
"""A floor, not a policy. Composition rules push people towards worse passwords."""

hasher = PasswordHasher()

_DUMMY_HASH = hasher.hash("a password that belongs to nobody")
"""Verified against when the email is unknown, so that "no such account" and "wrong
password" take the same time as well as returning the same body. Without it the login
endpoint answers a question it was never asked."""

INVALID_CREDENTIALS = "email or password is incorrect"
"""One message for both failures. Naming which half was wrong is an enumeration oracle."""

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class PublicUser(BaseModel):
    id: str
    email: str
    onboarded: bool


def _public(user: User) -> PublicUser:
    return PublicUser(id=user.id, email=user.email, onboarded=user.onboarded_at is not None)


def store_of(request: Request) -> Store:
    """The store, or a straight answer that this deployment has no accounts.

    Running without `DATABASE_URL` is a supported mode — it is what the offline suite
    uses — but it cannot serve these routes, and pretending otherwise would surface as
    an AttributeError inside a handler.
    """
    store = request.app.state.store
    if store is None:
        raise HTTPException(503, "this instance is running without a database")
    return store


async def current_user(request: Request) -> User:
    """Every authenticated route depends on this. A miss is 401, never an empty user.

    An instance with no store answers 401 here rather than the 503 `store_of` gives the
    other routes. The distinction is deliberate: `register` and `login` genuinely cannot
    function without a database, but "this instance has no accounts" is a complete and
    accurate answer to *who is signed in* — nobody is. Returning 503 reports a server
    fault where there is none, and a browser holding a cookie from some other instance
    then sees a scary error on every page load instead of simply being signed out.
    """
    store = request.app.state.store
    token = request.cookies.get(COOKIE)
    if store is None or not token:
        raise HTTPException(401, "not signed in")

    user = await store.user_for_token(token)
    if user is None:
        raise HTTPException(401, "not signed in")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite=os.environ.get("CONTINUITY_COOKIE_SAMESITE", "lax"),
        secure=os.environ.get("CONTINUITY_COOKIE_SECURE", "") == "1",
        path="/",
        # The *ceiling*, not the idle window. The browser keeps sending the cookie and the
        # server decides whether it is still alive — a cookie that expired client-side would
        # be indistinguishable from never having signed in, and the session table is the one
        # place that knows the difference.
        max_age=SESSION_TTL_SECONDS,
    )


@router.post("/register", status_code=201)
async def register(
    credentials: Credentials, request: Request, response: Response
) -> PublicUser:
    store = store_of(request)
    try:
        user = await store.create_user(credentials.email, hasher.hash(credentials.password))
    except EmailTaken:
        raise HTTPException(409, "an account with that email already exists")

    _set_session_cookie(response, await store.create_session(user.id))
    return _public(user)


@router.post("/login")
async def login(credentials: Credentials, request: Request, response: Response) -> PublicUser:
    store = store_of(request)
    user = await store.user_by_email(credentials.email)

    try:
        hasher.verify(user.password_hash if user else _DUMMY_HASH, credentials.password)
    except (VerifyMismatchError, VerificationError):
        raise HTTPException(401, INVALID_CREDENTIALS)

    if user is None:  # the dummy hash matched, which it cannot — belt and braces
        raise HTTPException(401, INVALID_CREDENTIALS)

    # Parameters change as hardware does; a login is the only moment the plaintext is
    # available to re-hash with the current ones.
    if hasher.check_needs_rehash(user.password_hash):
        await store.update_password_hash(user.id, hasher.hash(credentials.password))

    _set_session_cookie(response, await store.create_session(user.id))
    return _public(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    """Ends the session server-side, so a copied token stops working too."""
    token = request.cookies.get(COOKIE)
    if token and request.app.state.store is not None:
        await request.app.state.store.delete_session(token)
    response.delete_cookie(COOKIE, path="/")


@router.get("/me")
async def me(user: User = Depends(current_user)) -> PublicUser:
    return _public(user)
