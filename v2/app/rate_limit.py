"""Per-client request guardrails for the remote and materials API routers.

Direct loopback requests (the dashboard on the same machine) are never
throttled. Every other client, such as the phone over the LAN or a tunnel, gets:

* a sliding-window request ceiling (``HUNTERX_API_RATE_LIMIT_PER_MINUTE``,
  default 600 requests per 60 s), and
* a failed-authentication lockout: after ``HUNTERX_API_AUTH_FAILURE_LIMIT``
  (default 20) unauthorised requests inside ``HUNTERX_API_AUTH_FAILURE_WINDOW``
  seconds (default 300), the client gets 429 until the window drains, even if
  it later presents the right key. That caps API-key guessing.

State is in-process memory only: nothing is written to the database and no
schema changes. Counters are scoped per client address and active settings
object. ``0`` disables a limit; invalid values fall back to defaults.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.security import _is_direct_loopback, is_authorized

DEFAULT_REQUESTS_PER_MINUTE = 600
DEFAULT_AUTH_FAILURE_LIMIT = 20
DEFAULT_AUTH_FAILURE_WINDOW = 300.0
REQUEST_WINDOW = 60.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value >= 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


class ClientGuard:
    """Thread-safe sliding-window counters keyed by client address."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, deque[float]] = {}
        self._failures: dict[str, deque[float]] = {}

    @staticmethod
    def _trim(bucket: deque[float], cutoff: float) -> None:
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._failures.clear()

    def locked_out(self, client: str, limit: int, window: float) -> float:
        """Seconds until ``client`` may retry after too many auth failures (0 = not locked)."""
        if limit <= 0:
            return 0.0
        now = self._clock()
        with self._lock:
            bucket = self._failures.get(client)
            if not bucket:
                return 0.0
            self._trim(bucket, now - window)
            if len(bucket) < limit:
                return 0.0
            return max(bucket[0] + window - now, 1.0)

    def record_failure(self, client: str, window: float) -> None:
        now = self._clock()
        with self._lock:
            bucket = self._failures.setdefault(client, deque())
            self._trim(bucket, now - window)
            bucket.append(now)

    def hit(self, client: str, limit: int, window: float = REQUEST_WINDOW) -> float:
        """Count one request; return seconds to wait if over ``limit`` (0 = allowed)."""
        if limit <= 0:
            return 0.0
        now = self._clock()
        with self._lock:
            bucket = self._requests.setdefault(client, deque())
            self._trim(bucket, now - window)
            if len(bucket) >= limit:
                return max(bucket[0] + window - now, 1.0)
            bucket.append(now)
            return 0.0


guard = ClientGuard()


def _too_many(detail: str, retry_after: float) -> HTTPException:
    return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail, headers={"Retry-After": str(int(retry_after + 0.999))})


def enforce_client_limits(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Router dependency; runs before ``require_api_key``, which still decides 401/503."""
    if _is_direct_loopback(request):
        return
    # Scope counters to the active settings object: a settings reload (or a
    # fresh Settings in tests) starts clean, a running server keeps one scope.
    client = f"{request.client.host if request.client else 'unknown'}|{id(settings)}"
    failure_limit = _env_int("HUNTERX_API_AUTH_FAILURE_LIMIT", DEFAULT_AUTH_FAILURE_LIMIT)
    failure_window = _env_float("HUNTERX_API_AUTH_FAILURE_WINDOW", DEFAULT_AUTH_FAILURE_WINDOW)
    wait = guard.locked_out(client, failure_limit, failure_window)
    if wait:
        raise _too_many("too many failed authentication attempts from this client", wait)
    if not is_authorized(request, settings):
        if failure_limit > 0:
            guard.record_failure(client, failure_window)
        return  # require_api_key raises the 401/503
    wait = guard.hit(client, _env_int("HUNTERX_API_RATE_LIMIT_PER_MINUTE", DEFAULT_REQUESTS_PER_MINUTE))
    if wait:
        raise _too_many("request rate limit exceeded for this client", wait)
