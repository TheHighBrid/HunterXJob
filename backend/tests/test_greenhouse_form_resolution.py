from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.services.automation.greenhouse_adapter import GreenhouseAdapter


class FakeElement:
    def __init__(self, *, href: str | None = None):
        self.href = href
        self.value: str | None = None
        self.file_path: str | None = None
        self.clicked = False


class FakeLocator:
    def __init__(self, elements: list[FakeElement]):
        self.elements = elements

    @property
    def first(self) -> "FakeLocator":
        return FakeLocator(self.elements[:1])

    def count(self) -> int:
        return len(self.elements)

    def fill(self, value: str) -> None:
        if not self.elements:
            raise RuntimeError("no element")
        self.elements[0].value = value

    def set_input_files(self, file_path: str) -> None:
        if not self.elements:
            raise RuntimeError("no element")
        self.elements[0].file_path = file_path

    def click(self) -> None:
        if not self.elements:
            raise RuntimeError("no element")
        self.elements[0].clicked = True

    def get_attribute(self, name: str) -> str | None:
        if not self.elements:
            return None
        if name == "href":
            return self.elements[0].href
        return None


class FakeScope:
    def __init__(
        self,
        url: str,
        elements: dict[str, list[FakeElement]] | None = None,
        html: str = "<html></html>",
    ):
        self.url = url
        self.elements = elements or {}
        self.html = html

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.elements.get(selector, []))

    def content(self) -> str:
        return self.html


class FakePage(FakeScope):
    def __init__(
        self,
        routes: dict[str, dict[str, list[FakeElement]]],
        *,
        frames: list[FakeScope] | None = None,
    ):
        super().__init__("about:blank")
        self.routes = routes
        self.frames = frames or []
        self.main_frame = None
        self.goto_calls: list[str] = []
        self.default_timeout: int | None = None

    def set_default_timeout(self, value: int) -> None:
        self.default_timeout = value

    def goto(self, url: str, wait_until: str) -> None:
        self.goto_calls.append(url)
        self.url = url
        self.elements = self.routes.get(url, {})

    def wait_for_timeout(self, value: int) -> None:
        self.last_wait = value

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        self.last_load_state = (state, timeout)

    def title(self) -> str:
        return "Risk Operations Analyst"


class FakeRuntime:
    def __init__(self, page: FakePage, tmp_path: Path):
        self.page = page
        self.tmp_path = tmp_path
        self._artifacts: dict[str, dict[str, str]] = {}

    @contextmanager
    def application_page(self, application_key: str):
        artifact_dir = self.tmp_path / application_key
        artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            yield self.page
        finally:
            metadata = artifact_dir / "metadata.json"
            html = artifact_dir / "page.html"
            screenshot = artifact_dir / "page.png"
            trace = artifact_dir / "trace.zip"
            metadata.write_text(json.dumps({"page_url": self.page.url}), encoding="utf-8")
            html.write_text(self.page.content(), encoding="utf-8")
            screenshot.write_bytes(b"png")
            trace.write_bytes(b"trace")
            self._artifacts[application_key] = {
                "metadata": str(metadata),
                "html_snapshot": str(html),
                "screenshot": str(screenshot),
                "trace": str(trace),
            }

    def artifacts_for(self, application_key: str) -> dict[str, str]:
        return dict(self._artifacts.get(application_key, {}))


def _form_elements():
    return {
        "input[type='email']": [FakeElement()],
        "input[name='first_name']": [FakeElement()],
        "input[name='last_name']": [FakeElement()],
        "input[type='file'][name*='resume']": [FakeElement()],
        "textarea[name*='cover_letter']": [FakeElement()],
        "button[type='submit']": [FakeElement()],
    }


def _application():
    return SimpleNamespace(
        id="application-1",
        profile={
            "full_name": "Mo Alem",
            "email": "mo@example.test",
            "phone": "6135550100",
        },
    )


def _job(url: str):
    return SimpleNamespace(
        url=url,
        source="greenhouse",
        company="stripe",
        external_id="7964759",
        title="Risk Operations Analyst - SSO",
    )


def test_branded_wrapper_follows_apply_link_and_fills_form(tmp_path):
    wrapper_url = "https://stripe.test/jobs/listing/risk/7964759"
    apply_url = "https://stripe.test/jobs/listing/risk/7964759/apply"
    apply_link = FakeElement(href="/jobs/listing/risk/7964759/apply")
    form = _form_elements()
    page = FakePage(
        {
            wrapper_url: {"a[href*='/apply']": [apply_link]},
            apply_url: form,
        }
    )
    runtime = FakeRuntime(page, tmp_path)
    adapter = GreenhouseAdapter(dry_run=True, browser_runtime=runtime)
    resume_path = str(tmp_path / "resume.pdf")
    Path(resume_path).write_bytes(b"pdf")

    result = adapter.submit(
        application=_application(),
        job_posting=_job(wrapper_url),
        resume_pdf_path=resume_path,
        cover_letter="Hello Stripe",
    )

    assert result.status == "needs_review"
    assert result.detail == "dry-run mode: form filled but not submitted"
    assert page.goto_calls == [wrapper_url, apply_url]
    assert form["input[type='email']"][0].value == "mo@example.test"
    assert form["input[name='first_name']"][0].value == "Mo"
    assert form["input[name='last_name']"][0].value == "Alem"
    assert form["input[type='file'][name*='resume']"][0].file_path == resume_path

    metadata = json.loads(Path(result.evidence["metadata"]).read_text(encoding="utf-8"))
    diagnostics = metadata["adapter_diagnostics"]
    assert diagnostics["final_url"] == apply_url
    assert diagnostics["selected_scope"] == "main"
    assert any(
        step["action"] == "form_found_after_apply_action"
        for step in diagnostics["resolution_steps"]
    )
    assert diagnostics["filled_fields"]["email"] == "input[type='email']"


def test_embedded_frame_is_used_without_guessing_across_scopes(tmp_path):
    wrapper_url = "https://company.test/jobs/123"
    frame_elements = _form_elements()
    frame = FakeScope(
        "https://job-boards.greenhouse.io/company/jobs/123",
        frame_elements,
    )
    page = FakePage({wrapper_url: {}}, frames=[frame])
    runtime = FakeRuntime(page, tmp_path)
    adapter = GreenhouseAdapter(dry_run=True, browser_runtime=runtime)
    resume_path = str(tmp_path / "resume.pdf")
    Path(resume_path).write_bytes(b"pdf")

    result = adapter.submit(
        application=_application(),
        job_posting=_job(wrapper_url),
        resume_pdf_path=resume_path,
        cover_letter="Hello",
    )

    assert result.status == "needs_review"
    assert result.detail == "dry-run mode: form filled but not submitted"
    assert frame_elements["input[type='email']"][0].value == "mo@example.test"
    assert frame_elements["input[type='file'][name*='resume']"][0].file_path == resume_path

    metadata = json.loads(Path(result.evidence["metadata"]).read_text(encoding="utf-8"))
    diagnostics = metadata["adapter_diagnostics"]
    assert diagnostics["selected_scope"].startswith("frame[0]")
    assert diagnostics["candidate_urls_tried"] == []


def test_direct_greenhouse_candidate_is_used_when_wrapper_has_no_apply_action(tmp_path):
    wrapper_url = "https://company.test/jobs/123"
    candidate_url = "https://job-boards.greenhouse.io/stripe/jobs/7964759"
    form = _form_elements()
    page = FakePage({wrapper_url: {}, candidate_url: form})
    runtime = FakeRuntime(page, tmp_path)
    adapter = GreenhouseAdapter(dry_run=True, browser_runtime=runtime)
    resume_path = str(tmp_path / "resume.pdf")
    Path(resume_path).write_bytes(b"pdf")

    result = adapter.submit(
        application=_application(),
        job_posting=_job(wrapper_url),
        resume_pdf_path=resume_path,
        cover_letter="Hello",
    )

    assert result.status == "needs_review"
    assert result.detail == "dry-run mode: form filled but not submitted"
    assert candidate_url in page.goto_calls

    metadata = json.loads(Path(result.evidence["metadata"]).read_text(encoding="utf-8"))
    diagnostics = metadata["adapter_diagnostics"]
    assert diagnostics["candidate_urls_tried"][0] == candidate_url
    assert any(
        step["action"] == "form_found_on_greenhouse_candidate"
        for step in diagnostics["resolution_steps"]
    )
