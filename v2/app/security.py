"""API-key authentication for the v2 API.

Every endpoint except ``GET /api/health`` and the static dashboard page needs
``X-API-Key: <API_KEY>`` (``Authorization: Bearer <API_KEY>`` is also
accepted). With no key configured the API refuses requests (503), unless
``LOCAL_DEV_MODE=true`` *and* ``HOST`` is a loopback address, in which case
only direct loopback requests without proxy headers are allowed.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings

API_KEY_HEADER = "X-API-Key"
MIN_API_KEY_LENGTH = 32
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# A reverse proxy or tunnel on the same machine makes every request look like
# it comes from 127.0.0.1; these headers reveal that it was forwarded.
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "cf-connecting-ip", "true-client-ip")

AuthMode = Literal["api_key", "local_dev", "misconfigured"]


@dataclass(frozen=True, slots=True)
class AuthPosture:
    mode: AuthMode
    detail: str


def auth_posture(settings: Settings) -> AuthPosture:
    key = settings.api_key.strip()
    if key:
        if len(key) < MIN_API_KEY_LENGTH:
            return AuthPosture("misconfigured", f"API_KEY must be at least {MIN_API_KEY_LENGTH} characters")
        return AuthPosture("api_key", "X-API-Key required")
    if settings.local_dev_mode:
        if settings.host.strip().lower() in LOOPBACK_HOSTS:
            return AuthPosture("local_dev", "no API key; direct loopback requests only (LOCAL_DEV_MODE)")
        return AuthPosture("misconfigured", "LOCAL_DEV_MODE=true requires HOST=127.0.0.1 (or ::1)")
    return AuthPosture("misconfigured", "API_KEY is not set")


def _supplied_key(request: Request) -> str:
    header = request.headers.get(API_KEY_HEADER, "").strip()
    if header:
        return header
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _is_direct_loopback(request: Request) -> bool:
    client = request.client.host if request.client else ""
    return client in LOOPBACK_HOSTS and not any(name in request.headers for name in PROXY_HEADERS)


def is_authorized(request: Request, settings: Settings) -> bool:
    posture = auth_posture(settings)
    if posture.mode == "api_key":
        supplied = _supplied_key(request)
        return bool(supplied) and hmac.compare_digest(supplied.encode(), settings.api_key.strip().encode())
    if posture.mode == "local_dev":
        return _is_direct_loopback(request)
    return False


def require_api_key(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    posture = auth_posture(settings)
    if posture.mode == "misconfigured":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"API authentication is not configured: {posture.detail}")
    if not is_authorized(request, settings):
        detail = "missing or invalid X-API-Key header" if posture.mode == "api_key" else "local dev mode accepts direct loopback requests only"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})
