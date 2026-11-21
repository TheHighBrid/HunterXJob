"""Regression tests for read-only browser isolation guards in ``app.browser_filler`` (Issues #30, #31, #32).

These tests launch a real Playwright Chromium browser against a local HTTP server
to verify fail-closed read-only isolation when ``allow_live_submit=False``:
- Issue #30: Service workers and downloads are blocked in read-only mode.
- Issue #31: HEAD and all other non-GET requests are aborted and recorded as non-GET evidence.
- Issue #32: Direct ``form.submit()``, ``HTMLFormElement.prototype.submit.call(form)``,
  ``form.requestSubmit()``, ``submit`` events, subframe/iframe submissions, and
  unreadable/detached frames are intercepted/detected and fail closed.
"""
from __future__ import annotations

import asyncio
import http.server
import threading
from typing import Any, ClassVar

import pytest

from app.browser_filler import FormFillResult, fill_application_form
from app.greenhouse_browser import READ_ONLY_METHODS, SUBMIT_MONITOR_JS
from app.submission_engine import SubmissionResult


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    routes: ClassVar[dict[str, tuple[int, str, bytes]]] = {}
    recorded_requests: ClassVar[list[dict[str, str]]] = []
    lock: ClassVar[threading.Lock] = threading.Lock()

    def _handle(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        with self.lock:
            self.recorded_requests.append({"method": method, "path": path, "full_path": self.path})
        entry = self.routes.get(path)
        if entry is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(b"not found")
            return
        status, content_type, body = entry
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            self.rfile.read(length)
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def recording_server():
    routes: dict[str, tuple[int, str, bytes]] = {}
    recorded: list[dict[str, str]] = []
    lock = threading.Lock()

    class Handler(_RecordingHandler):
        pass

    Handler.routes = routes
    Handler.recorded_requests = recorded
    Handler.lock = lock

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def register(path: str, body: str | bytes, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        raw = body.encode("utf-8") if isinstance(body, str) else body
        routes[path] = (status, content_type, raw)

    def requests_for(path: str | None = None, method: str | None = None) -> list[dict[str, str]]:
        with lock:
            return [
                dict(item)
                for item in recorded
                if (path is None or item["path"] == path) and (method is None or item["method"] == method)
            ]

    yield f"http://{host}:{port}", register, requests_for

    server.shutdown()
    server.server_close()


def test_issue_30_service_worker_blocked_in_read_only_mode(recording_server):
    """Issue #30: Read-only mode must block service worker registration and prevent /sw.js requests."""
    base_url, register, requests_for = recording_server
    register(
        "/sw.js",
        "self.addEventListener('install', () => self.skipWaiting());",
        content_type="application/javascript; charset=utf-8",
    )
    register(
        "/sw_form.html",
        """<!doctype html>
<html>
  <body>
    <form id="app">
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" required />
    </form>
    <script>
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(() => {});
      }
    </script>
  </body>
</html>
""",
    )

    result = asyncio.run(
        fill_application_form(
            url=f"{base_url}/sw_form.html",
            answers={"first_name": "Jane"},
            required_fields=["first_name"],
            allow_live_submit=False,
            headless=True,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert requests_for(path="/sw.js") == [], "Service worker script must never be fetched in read-only mode"
    assert result.browser_evidence.get("service_workers") == "block"
    assert result.browser_evidence.get("accept_downloads") is False


@pytest.mark.parametrize(
    "vector, trigger_js",
    [
        ("form.submit()", "form.submit();"),
        ("HTMLFormElement.prototype.submit.call", "HTMLFormElement.prototype.submit.call(form);"),
        ("form.requestSubmit()", "form.requestSubmit();"),
        ("submit_event", "document.getElementById('submit_btn').click();"),
    ],
)
def test_issue_32_direct_submit_vectors_intercepted_and_fail_closed(recording_server, vector, trigger_js):
    """Issue #32: Every form submission vector in read-only mode must be refused and fail closed."""
    base_url, register, requests_for = recording_server
    register("/submitted", "<html><body>Submitted!</body></html>")
    register(
        "/form.html",
        f"""<!doctype html>
<html>
  <body>
    <form id="app" method="get" action="/submitted" novalidate>
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" required />
      <button id="submit_btn" type="submit">Submit</button>
    </form>
    <script>
      window.addEventListener('load', () => {{
        const form = document.getElementById('app');
        {trigger_js}
      }});
    </script>
  </body>
</html>
""",
    )

    result = asyncio.run(
        fill_application_form(
            url=f"{base_url}/form.html",
            answers={"first_name": "Jane"},
            required_fields=["first_name"],
            allow_live_submit=False,
            headless=True,
        )
    )

    assert requests_for(path="/submitted") == [], f"Server must receive zero /submitted requests for {vector}"
    assert result.success is False
    assert result.submitted is False
    assert len(result.submit_events) >= 1
    assert result.error


def test_issue_32_subframe_iframe_submit_intercepted_and_fails_closed(recording_server):
    """Issue #32: Submissions triggered inside subframes/iframes must be intercepted and fail closed."""
    base_url, register, requests_for = recording_server
    register("/iframe-submitted", "<html><body>Iframe Submitted!</body></html>")
    register(
        "/iframe_host.html",
        """<!doctype html>
<html>
  <body>
    <form id="main_form">
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" required />
    </form>
    <iframe
      title="nested"
      style="display:none"
      srcdoc="<form id='nested' method='get' action='/iframe-submitted' target='_top'><input type='hidden' name='x' value='1'></form><script>window.addEventListener('load', () => document.getElementById('nested').submit());</script>"
    ></iframe>
  </body>
</html>
""",
    )

    result = asyncio.run(
        fill_application_form(
            url=f"{base_url}/iframe_host.html",
            answers={"first_name": "Jane"},
            required_fields=["first_name"],
            allow_live_submit=False,
            headless=True,
        )
    )

    assert requests_for(path="/iframe-submitted") == []
    assert result.success is False
    assert result.submitted is False
    assert len(result.submit_events) >= 1
    assert any(ev.get("via") == "form.submit()" for ev in result.submit_events)


def test_issue_32_unreadable_or_detached_frame_fails_closed(recording_server, monkeypatch):
    """Issue #32: If any frame is detached or unreadable during submit inspection, fail closed."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api._generated import Frame

    base_url, register, _ = recording_server
    register(
        "/with_frame.html",
        """<!doctype html>
<html>
  <body>
    <form id="app">
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" required />
    </form>
    <iframe id="child" srcdoc="<p>child</p>"></iframe>
  </body>
</html>
""",
    )

    orig_evaluate = Frame.evaluate

    async def patched_evaluate(self, expression, arg=None):
        if expression == "() => (window.__hunterxSubmitEvents || []).slice(0, 50)" and self.parent_frame is not None:
            raise PlaywrightError("Frame was detached")
        return await orig_evaluate(self, expression, arg)

    monkeypatch.setattr(Frame, "evaluate", patched_evaluate)

    result = asyncio.run(
        fill_application_form(
            url=f"{base_url}/with_frame.html",
            answers={"first_name": "Jane"},
            required_fields=["first_name"],
            allow_live_submit=False,
            headless=True,
        )
    )

    assert result.success is False
    assert result.submitted is False
    assert len(result.unreadable_frames) >= 1
    assert result.error


def test_issue_31_head_and_post_requests_aborted_and_recorded(recording_server):
    """Issue #31: HEAD and POST must be aborted via READ_ONLY_METHODS and recorded in evidence."""
    assert frozenset({"GET"}) == READ_ONLY_METHODS
    assert "HTMLFormElement.prototype" in SUBMIT_MONITOR_JS

    base_url, register, requests_for = recording_server
    register("/probe-head", "ok")
    register("/probe-post", "ok")
    register(
        "/beacon_form.html",
        """<!doctype html>
<html>
  <body>
    <form id="app">
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" required />
    </form>
    <script>
      fetch('/probe-head', { method: 'HEAD' }).catch(() => {});
      fetch('/probe-post', { method: 'POST', body: 'beacon=1' }).catch(() => {});
    </script>
  </body>
</html>
""",
    )

    result = asyncio.run(
        fill_application_form(
            url=f"{base_url}/beacon_form.html",
            answers={"first_name": "Jane"},
            required_fields=["first_name"],
            allow_live_submit=False,
            headless=True,
        )
    )

    assert requests_for(method="HEAD") == [], "Server must receive zero HEAD requests in read-only mode"
    assert requests_for(method="POST") == [], "Server must receive zero POST requests in read-only mode"
    assert result.submitted is False
    assert result.non_get_attempts >= 2
    assert result.aborted_requests >= 2
    assert result.browser_evidence["non_get_attempts"] >= 2
    assert result.browser_evidence["aborted"] >= 2
    logged = result.browser_evidence["requests"]
    assert any(r["method"] == "HEAD" and r["action"] == "aborted" for r in logged)
    assert any(r["method"] == "POST" and r["action"] == "aborted" for r in logged)


def test_allow_live_submit_true_allows_post_and_submit(recording_server):
    """Verify allow_live_submit=True is not blocked by read-only isolation hooks."""
    base_url, register, requests_for = recording_server
    register("/live-submit", "<html><body>Application Received</body></html>")
    register(
        "/live_form.html",
        """<!doctype html>
<html>
  <body>
    <form id="app" method="post" action="/live-submit">
      <label for="first_name">First Name</label>
      <input id="first_name" name="first_name" type="text" required />
      <button id="submit_btn" type="submit">Submit</button>
    </form>
  </body>
</html>
""",
    )

    async def real_live_submit(*, page, **_kwargs):
        await page.click("#submit_btn")
        await page.wait_for_load_state("networkidle")
        return SubmissionResult(
            success=True,
            status="submitted",
            confirmation_text="Application Received",
            final_url=page.url,
        )

    result: FormFillResult = asyncio.run(
        fill_application_form(
            url=f"{base_url}/live_form.html",
            answers={"first_name": "Jane"},
            required_fields=["first_name"],
            allow_live_submit=True,
            live_submit_fn=real_live_submit,
            headless=True,
            headed_pause_ms=0,
        )
    )

    assert result.success is True
    assert result.submitted is True
    assert len(requests_for(path="/live-submit", method="POST")) == 1
