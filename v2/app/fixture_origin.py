"""Test-only loopback origin for the Gate 1 fixture proof (docs/RECOVERY_CONTRACT.md).

Gate 1 proves the production dry-run path end to end against fixtures that
already live in the repo, served over loopback HTTP. The Greenhouse fetcher,
liveness probe, and browser verification are otherwise locked to Greenhouse
hosts, so the gate runner needs a narrowly scoped way to point them at
``http://127.0.0.1:<port>`` instead.

This allowance cannot be switched on by configuration:

* it is not a :class:`~app.config.Settings` field, so ``.env``, environment
  variables, and the phone's settings API never see it;
* it is process-local and only set by calling :func:`enable_for_gate1` from
  Python, with an explicit acknowledgement string. No module under ``app/``
  calls it; only the gate runner (``scripts/gate1.py``) and tests do;
* it only accepts a plain-HTTP loopback IP origin with an explicit port, and
  refuses outright when the settings could ever submit (live submission
  allowed or a mode other than ``dry_run``).
"""
from __future__ import annotations

import ipaddress
import threading
from typing import Any
from urllib.parse import urlsplit

GATE1_ACKNOWLEDGEMENT = "gate1: loopback fixtures only, dry-run only, never production"

_lock = threading.Lock()
_origin: str | None = None


class FixtureOriginRefused(ValueError):
    """The loopback fixture allowance was asked for outside its narrow scope."""


def validate_loopback_origin(origin: str) -> str:
    """Return the normalized ``http://<loopback-ip>:<port>`` origin or raise."""
    try:
        parts = urlsplit(origin or "")
        port = parts.port
    except ValueError as exc:
        raise FixtureOriginRefused(f"not a valid origin: {origin!r}") from exc
    if parts.scheme != "http":
        raise FixtureOriginRefused("fixture origin must be plain http on loopback")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise FixtureOriginRefused("fixture origin must be scheme://host:port with nothing else")
    try:
        address = ipaddress.ip_address(parts.hostname or "")
    except ValueError as exc:
        raise FixtureOriginRefused("fixture origin host must be a loopback IP literal (no hostnames)") from exc
    if not address.is_loopback:
        raise FixtureOriginRefused("fixture origin host must be a loopback address")
    if not port:
        raise FixtureOriginRefused("fixture origin needs an explicit port")
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{host}:{port}"


def enable_for_gate1(origin: str, settings: Any, *, acknowledgement: str) -> str:
    """Route Greenhouse fetches in this process to a loopback fixture server.

    Refused unless ``acknowledgement`` is :data:`GATE1_ACKNOWLEDGEMENT` and the
    settings are dry-run only with live submission disallowed.
    """
    if acknowledgement != GATE1_ACKNOWLEDGEMENT:
        raise FixtureOriginRefused("the loopback fixture origin needs the explicit Gate 1 acknowledgement")
    if getattr(settings, "allow_live_submission", True) or getattr(settings, "application_mode", "") != "dry_run":
        raise FixtureOriginRefused("the loopback fixture origin is only allowed with APPLICATION_MODE=dry_run "
                                   "and ALLOW_LIVE_SUBMISSION=false")
    normalized = validate_loopback_origin(origin)
    global _origin
    with _lock:
        _origin = normalized
    return normalized


def disable() -> None:
    global _origin
    with _lock:
        _origin = None


def active_origin() -> str | None:
    """The loopback fixture origin, or None (always None in production)."""
    return _origin
