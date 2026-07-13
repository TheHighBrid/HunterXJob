"""Shared Playwright browser runtime for Android and desktop automation.

The Android deployment keeps Chromium in native Termux and connects from the
Ubuntu PRoot backend over Chrome DevTools Protocol (CDP). Desktop deployments
can instead use a persistent or managed Playwright context. Application
adapters receive pages from this runtime and never launch browser processes.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings, get_settings

logger = logging.getLogger("hunterxjob.browser")

_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_runtime_local = threading.local()


class BrowserRuntimeError(RuntimeError):
    """Raised when the configured browser runtime cannot be initialized."""


class BrowserRuntime:
    """Own one reusable Playwright browser session per worker thread."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.mode = self.settings.BROWSER_MODE.strip().lower()
        if self.mode not in {"cdp", "persistent", "managed"}:
            raise BrowserRuntimeError(
                "BROWSER_MODE must be one of: cdp, persistent, managed"
            )

        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._owner_thread_id: int | None = None
        self._session_started_monotonic: float | None = None
        self._applications_in_session = 0
        self._artifact_index: dict[str, dict[str, str]] = {}

        Path(self.settings.BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        Path(self.settings.BROWSER_ARTIFACT_DIR).mkdir(parents=True, exist_ok=True)

    @property
    def connected(self) -> bool:
        return self._context is not None and self._playwright is not None

    def connect(self) -> None:
        """Connect to or launch the configured browser runtime."""
        if self.connected:
            self._assert_owner_thread()
            return

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency failure path
            raise BrowserRuntimeError("Playwright is not installed") from exc

        self._owner_thread_id = threading.get_ident()
        try:
            self._playwright = sync_playwright().start()
            chromium = self._playwright.chromium

            if self.mode == "cdp":
                self._browser = chromium.connect_over_cdp(
                    self.settings.BROWSER_CDP_URL,
                    timeout=self.settings.BROWSER_CONNECT_TIMEOUT_MS,
                )
                contexts = list(self._browser.contexts)
                self._context = contexts[0] if contexts else self._browser.new_context()
            elif self.mode == "persistent":
                launch_kwargs: dict[str, Any] = {
                    "user_data_dir": self.settings.BROWSER_PROFILE_DIR,
                    "headless": self.settings.BROWSER_HEADLESS,
                    "args": self._launch_args(),
                }
                if self.settings.BROWSER_EXECUTABLE_PATH:
                    launch_kwargs["executable_path"] = self.settings.BROWSER_EXECUTABLE_PATH
                self._context = chromium.launch_persistent_context(**launch_kwargs)
                self._browser = self._context.browser
            else:
                launch_kwargs = {
                    "headless": self.settings.BROWSER_HEADLESS,
                    "args": self._launch_args(),
                }
                if self.settings.BROWSER_EXECUTABLE_PATH:
                    launch_kwargs["executable_path"] = self.settings.BROWSER_EXECUTABLE_PATH
                self._browser = chromium.launch(**launch_kwargs)
                self._context = self._browser.new_context()

            self._session_started_monotonic = time.monotonic()
            self._applications_in_session = 0
            logger.info("browser runtime connected in %s mode", self.mode)
        except Exception as exc:  # noqa: BLE001
            self._reset_handles(stop_driver=True)
            raise BrowserRuntimeError(
                f"could not initialize browser runtime in {self.mode} mode: {exc}"
            ) from exc

    @contextmanager
    def application_page(self, application_key: str) -> Iterator[Any]:
        """Yield a fresh page while preserving the shared browser context.

        A screenshot, HTML snapshot, trace, and metadata file are captured on
        exit whenever Playwright supports the requested artifact.
        """
        self._recycle_if_needed()
        self.connect()
        self._assert_owner_thread()
        assert self._context is not None

        page = self._context.new_page()
        page.set_default_timeout(self.settings.BROWSER_DEFAULT_TIMEOUT_MS)
        page.set_default_navigation_timeout(
            self.settings.BROWSER_NAVIGATION_TIMEOUT_MS
        )

        artifact_dir = self._artifact_dir(application_key)
        trace_started = False
        artifacts: dict[str, str] = {}
        capture_errors: list[str] = []

        try:
            try:
                self._context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=False,
                )
                trace_started = True
            except Exception as exc:  # noqa: BLE001
                capture_errors.append(f"trace start failed: {exc}")

            yield page
        finally:
            screenshot_path = artifact_dir / "page.png"
            html_path = artifact_dir / "page.html"
            trace_path = artifact_dir / "trace.zip"
            metadata_path = artifact_dir / "metadata.json"

            try:
                page.screenshot(path=str(screenshot_path), full_page=True)
                artifacts["screenshot"] = str(screenshot_path)
            except Exception as exc:  # noqa: BLE001
                capture_errors.append(f"screenshot failed: {exc}")

            try:
                html_path.write_text(page.content(), encoding="utf-8")
                artifacts["html_snapshot"] = str(html_path)
            except Exception as exc:  # noqa: BLE001
                capture_errors.append(f"html snapshot failed: {exc}")

            if trace_started:
                try:
                    self._context.tracing.stop(path=str(trace_path))
                    artifacts["trace"] = str(trace_path)
                except Exception as exc:  # noqa: BLE001
                    capture_errors.append(f"trace stop failed: {exc}")

            metadata = {
                "application_key": str(application_key),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "browser_mode": self.mode,
                "page_url": getattr(page, "url", ""),
                "capture_errors": capture_errors,
            }
            try:
                metadata_path.write_text(
                    json.dumps(metadata, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                artifacts["metadata"] = str(metadata_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not write browser artifact metadata: %s", exc)

            self._artifact_index[str(application_key)] = artifacts
            self._applications_in_session += 1
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def artifacts_for(self, application_key: str) -> dict[str, str]:
        """Return the latest evidence paths captured for an application."""
        return dict(self._artifact_index.get(str(application_key), {}))

    def status(self) -> dict[str, Any]:
        """Return non-secret runtime state for diagnostics."""
        age_seconds = None
        if self._session_started_monotonic is not None:
            age_seconds = max(0, int(time.monotonic() - self._session_started_monotonic))
        return {
            "status": "connected" if self.connected else "disconnected",
            "mode": self.mode,
            "connected": self.connected,
            "applications_in_session": self._applications_in_session,
            "session_age_seconds": age_seconds,
            "artifact_dir": self.settings.BROWSER_ARTIFACT_DIR,
            "profile_dir": self.settings.BROWSER_PROFILE_DIR,
        }

    def close(self) -> None:
        """Release Playwright resources without killing native CDP Chromium."""
        if self._owner_thread_id is not None:
            self._assert_owner_thread()

        try:
            if self.mode in {"persistent", "managed"} and self._context is not None:
                self._context.close()
        except Exception:  # noqa: BLE001
            pass

        try:
            if self.mode == "managed" and self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            pass

        # In CDP mode Browser.close() would terminate the externally managed
        # native Chromium process. Stopping Playwright only disconnects the
        # client and leaves Chromium alive for the next worker session.
        self._reset_handles(stop_driver=True)

    def _recycle_if_needed(self) -> None:
        if not self.connected:
            return
        self._assert_owner_thread()
        age_minutes = 0.0
        if self._session_started_monotonic is not None:
            age_minutes = (time.monotonic() - self._session_started_monotonic) / 60
        if (
            self._applications_in_session >= self.settings.BROWSER_MAX_APPLICATIONS_PER_SESSION
            or age_minutes >= self.settings.BROWSER_MAX_SESSION_MINUTES
        ):
            logger.info(
                "recycling browser connection after %s applications and %.1f minutes",
                self._applications_in_session,
                age_minutes,
            )
            self.close()

    def _artifact_dir(self, application_key: str) -> Path:
        safe_key = _SAFE_PATH_RE.sub("_", str(application_key)).strip("._") or "application"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = Path(self.settings.BROWSER_ARTIFACT_DIR) / safe_key / timestamp
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _launch_args(self) -> list[str]:
        return [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
        ]

    def _assert_owner_thread(self) -> None:
        if self._owner_thread_id not in {None, threading.get_ident()}:
            raise BrowserRuntimeError(
                "BrowserRuntime cannot be shared across threads; inject or obtain "
                "a worker-thread-local runtime instead"
            )

    def _reset_handles(self, stop_driver: bool) -> None:
        if stop_driver and self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._owner_thread_id = None
        self._session_started_monotonic = None
        self._applications_in_session = 0


def get_browser_runtime(settings: Settings | None = None) -> BrowserRuntime:
    """Return a reusable runtime scoped to the current worker thread."""
    resolved = settings or get_settings()
    signature = (
        resolved.BROWSER_MODE,
        resolved.BROWSER_CDP_URL,
        resolved.BROWSER_EXECUTABLE_PATH,
        resolved.BROWSER_PROFILE_DIR,
        resolved.BROWSER_ARTIFACT_DIR,
        resolved.BROWSER_HEADLESS,
    )
    runtime: BrowserRuntime | None = getattr(_runtime_local, "runtime", None)
    current_signature = getattr(_runtime_local, "signature", None)
    if runtime is None or current_signature != signature:
        if runtime is not None and runtime.connected:
            runtime.close()
        runtime = BrowserRuntime(resolved)
        _runtime_local.runtime = runtime
        _runtime_local.signature = signature
    return runtime


def probe_browser(settings: Settings | None = None) -> dict[str, Any]:
    """Probe the externally managed browser without launching a new process."""
    resolved = settings or get_settings()
    mode = resolved.BROWSER_MODE.strip().lower()
    result: dict[str, Any] = {
        "mode": mode,
        "cdp_url": resolved.BROWSER_CDP_URL if mode == "cdp" else None,
        "profile_dir": resolved.BROWSER_PROFILE_DIR,
        "artifact_dir": resolved.BROWSER_ARTIFACT_DIR,
    }

    if mode != "cdp":
        result.update(
            {
                "status": "configured",
                "reachable": None,
                "detail": "browser starts lazily when an application is processed",
            }
        )
        return result

    if resolved.BROWSER_CDP_URL.startswith(("ws://", "wss://")):
        result.update(
            {
                "status": "configured",
                "reachable": None,
                "detail": "websocket CDP URL configured; HTTP probe skipped",
            }
        )
        return result

    version_url = resolved.BROWSER_CDP_URL.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(  # noqa: S310 - loopback/configured endpoint
            version_url,
            timeout=max(1.0, resolved.BROWSER_HEALTH_TIMEOUT_SECONDS),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        result.update(
            {
                "status": "ok",
                "reachable": True,
                "browser": payload.get("Browser"),
                "protocol_version": payload.get("Protocol-Version"),
            }
        )
    except (OSError, ValueError, urllib.error.URLError) as exc:
        result.update(
            {
                "status": "unavailable",
                "reachable": False,
                "detail": str(exc),
            }
        )
    return result
