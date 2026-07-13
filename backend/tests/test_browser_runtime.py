from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

from app.config import Settings
from app.services.automation.email_adapter import EmailAdapter
from app.services.browser_runtime import BrowserRuntime


class FakeTracing:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self, **kwargs):
        self.started += 1

    def stop(self, path: str):
        self.stopped += 1
        Path(path).write_bytes(b"trace")


class FakePage:
    url = "https://example.test/apply"

    def set_default_timeout(self, value: int):
        self.default_timeout = value

    def set_default_navigation_timeout(self, value: int):
        self.navigation_timeout = value

    def screenshot(self, path: str, full_page: bool):
        Path(path).write_bytes(b"png")

    def content(self) -> str:
        return "<html><body>fixture</body></html>"

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.tracing = FakeTracing()
        self.pages: list[FakePage] = []

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, context: FakeContext):
        self.contexts = [context]


class FakeChromium:
    def __init__(self, browser: FakeBrowser):
        self.browser = browser
        self.connect_calls = 0

    def connect_over_cdp(self, url: str, timeout: int) -> FakeBrowser:
        self.connect_calls += 1
        self.last_url = url
        self.last_timeout = timeout
        return self.browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeStarter:
    def __init__(self, playwright: FakePlaywright):
        self.playwright = playwright

    def start(self) -> FakePlaywright:
        return self.playwright


def test_ten_application_pages_reuse_one_cdp_connection(monkeypatch, tmp_path):
    context = FakeContext()
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser)
    fake_playwright = FakePlaywright(chromium)

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakeStarter(fake_playwright)
    playwright_package = types.ModuleType("playwright")
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    settings = Settings(
        _env_file=None,
        BROWSER_MODE="cdp",
        BROWSER_CDP_URL="http://127.0.0.1:9222",
        BROWSER_ARTIFACT_DIR=str(tmp_path / "artifacts"),
        BROWSER_PROFILE_DIR=str(tmp_path / "profile"),
        BROWSER_MAX_APPLICATIONS_PER_SESSION=10,
    )
    runtime = BrowserRuntime(settings)

    for index in range(10):
        key = f"application-{index}"
        with runtime.application_page(key):
            pass
        evidence = runtime.artifacts_for(key)
        assert set(evidence) == {"screenshot", "html_snapshot", "trace", "metadata"}
        assert all(Path(path).exists() for path in evidence.values())

    assert chromium.connect_calls == 1
    assert len(context.pages) == 10
    assert runtime.status()["applications_in_session"] == 10


def test_runtime_rejects_cross_thread_page_use(tmp_path):
    settings = Settings(
        _env_file=None,
        BROWSER_MODE="managed",
        BROWSER_ARTIFACT_DIR=str(tmp_path / "artifacts"),
        BROWSER_PROFILE_DIR=str(tmp_path / "profile"),
    )
    runtime = BrowserRuntime(settings)
    runtime._owner_thread_id = threading.get_ident() + 1
    runtime._context = object()
    runtime._playwright = object()

    try:
        runtime.connect()
    except Exception as exc:
        assert "cannot be shared across threads" in str(exc)
    else:
        raise AssertionError("cross-thread runtime use should fail")


def test_email_submission_stays_locked_when_live_gate_is_false(tmp_path):
    settings = Settings(
        _env_file=None,
        AUTOMATION_DRY_RUN=False,
        ALLOW_LIVE_SUBMISSION=False,
        SMTP_HOST="smtp.example.test",
        SMTP_USERNAME="user",
        SMTP_PASSWORD="secret",
    )
    adapter = EmailAdapter(settings, dry_run=False)
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"pdf")

    result = adapter.submit(
        application=object(),
        job_posting=types.SimpleNamespace(title="Analyst", company="Example"),
        resume_pdf_path=str(resume),
        cover_letter="Hello",
        contact_email="jobs@example.test",
    )

    assert result.status == "needs_review"
    assert "not sent" in result.detail
