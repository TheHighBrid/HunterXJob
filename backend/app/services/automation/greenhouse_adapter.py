"""Submits applications via a job's public Greenhouse application form.

The adapter receives pages from BrowserRuntime. It never launches or closes a
Chromium process, preserving Android browser state and reducing memory churn.
"""
from __future__ import annotations

from app.config import get_settings
from app.services.automation.base import ApplicationAdapter, AutomationResult
from app.services.browser_runtime import BrowserRuntime, get_browser_runtime

_CONFIRMATION_KEYWORDS = [
    "thank you for applying",
    "thanks for applying",
    "application has been submitted",
    "successfully submitted",
    "we have received your application",
    "your application was submitted",
]

_FIRST_NAME_SELECTORS = ["#first_name", "input[name='job_application[first_name]']"]
_LAST_NAME_SELECTORS = ["#last_name", "input[name='job_application[last_name]']"]
_EMAIL_SELECTORS = ["#email", "input[name='job_application[email]']"]
_PHONE_SELECTORS = ["#phone", "input[name='job_application[phone]']"]
_RESUME_INPUT_SELECTORS = ["#resume", "input[type='file'][name*='resume']"]
_COVER_LETTER_TEXTAREA_SELECTORS = [
    "#cover_letter_text",
    "textarea[name*='cover_letter']",
]
_SUBMIT_SELECTORS = ["#submit_app", "button[type='submit']", "input[type='submit']"]


def _try_fill(page, selectors: list[str], value: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.fill(value)
                return True
        except Exception:
            continue
    return False


def _try_set_input_files(page, selectors: list[str], file_path: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.set_input_files(file_path)
                return True
        except Exception:
            continue
    return False


def _try_click(page, selectors: list[str]) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click()
                return True
        except Exception:
            continue
    return False


class GreenhouseAdapter(ApplicationAdapter):
    enabled = True

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30_000,
        dry_run: bool = False,
        browser_runtime: BrowserRuntime | None = None,
    ):
        settings = get_settings()
        self.browser_runtime = browser_runtime or get_browser_runtime(settings)
        self.timeout_ms = timeout_ms
        self.dry_run = bool(dry_run or not settings.ALLOW_LIVE_SUBMISSION)

    def submit(
        self,
        application,
        job_posting,
        resume_pdf_path: str,
        cover_letter: str,
        contact_email: str | None = None,
    ) -> AutomationResult:
        apply_url = getattr(job_posting, "url", "") or ""
        if not apply_url:
            return AutomationResult("failed", "job posting has no application URL")

        application_key = str(getattr(application, "id", "greenhouse-application"))
        try:
            with self.browser_runtime.application_page(application_key) as page:
                page.set_default_timeout(self.timeout_ms)
                page.goto(apply_url, wait_until="domcontentloaded")
                result = self._complete_form(
                    page,
                    application,
                    resume_pdf_path,
                    cover_letter,
                    contact_email,
                )
        except Exception as exc:  # noqa: BLE001
            result = AutomationResult("failed", f"Greenhouse submission error: {exc}")

        result.evidence.update(self.browser_runtime.artifacts_for(application_key))
        return result

    def _complete_form(
        self,
        page,
        application,
        resume_pdf_path: str,
        cover_letter: str,
        contact_email: str | None,
    ) -> AutomationResult:
        profile = getattr(application, "profile", None) or {}
        full_name = (profile.get("full_name") if isinstance(profile, dict) else "") or ""
        first_name, _, last_name = full_name.partition(" ")
        email = contact_email or (
            profile.get("email") if isinstance(profile, dict) else ""
        ) or ""
        phone = (profile.get("phone") if isinstance(profile, dict) else "") or ""

        if not _try_fill(page, _EMAIL_SELECTORS, email):
            return AutomationResult(
                "needs_review", "could not locate email field on Greenhouse form"
            )
        _try_fill(page, _FIRST_NAME_SELECTORS, first_name)
        _try_fill(page, _LAST_NAME_SELECTORS, last_name)
        if phone:
            _try_fill(page, _PHONE_SELECTORS, phone)

        if not _try_set_input_files(page, _RESUME_INPUT_SELECTORS, resume_pdf_path):
            return AutomationResult(
                "needs_review", "could not locate resume upload field on Greenhouse form"
            )

        _try_fill(page, _COVER_LETTER_TEXTAREA_SELECTORS, cover_letter)

        if self.dry_run:
            return AutomationResult(
                "needs_review", "dry-run mode: form filled but not submitted"
            )

        if not _try_click(page, _SUBMIT_SELECTORS):
            return AutomationResult(
                "needs_review", "could not locate submit button on Greenhouse form"
            )

        page.wait_for_timeout(2000)
        page_text = (page.content() or "").lower()
        page_url = page.url.lower()
        confirmed = any(kw in page_text for kw in _CONFIRMATION_KEYWORDS) or (
            "confirmation" in page_url or "thanks" in page_url
        )
        if confirmed:
            return AutomationResult("submitted", f"confirmed via {page.url}")
        if "captcha" in page_text or "unusual traffic" in page_text:
            return AutomationResult("blocked", "CAPTCHA/anti-bot wall detected")
        return AutomationResult(
            "needs_review",
            "form submitted but no confirmation signal detected - verify manually",
        )
