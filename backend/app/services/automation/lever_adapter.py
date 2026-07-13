"""Submits applications through public Lever forms using BrowserRuntime."""
from __future__ import annotations

from app.config import get_settings
from app.services.automation.base import ApplicationAdapter, AutomationResult
from app.services.browser_runtime import BrowserRuntime, get_browser_runtime

_CONFIRMATION_KEYWORDS = [
    "thank you for applying",
    "thanks for applying",
    "application submitted",
    "we've received your application",
    "we have received your application",
]

_NAME_SELECTORS = ["input[name='name']", "#name-input"]
_EMAIL_SELECTORS = ["input[name='email']", "#email-input"]
_PHONE_SELECTORS = ["input[name='phone']", "#phone-input"]
_RESUME_INPUT_SELECTORS = ["input[name='resume']", "input[type='file']"]
_ADDITIONAL_INFO_SELECTORS = [
    "textarea[name='comments']",
    "textarea[name='additional-information']",
    "textarea",
]
_SUBMIT_SELECTORS = ["button[type='submit']", "button[data-qa='btn-submit']"]


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


class LeverAdapter(ApplicationAdapter):
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
        if not apply_url.rstrip("/").endswith("/apply"):
            apply_url = apply_url.rstrip("/") + "/apply"

        application_key = str(getattr(application, "id", "lever-application"))
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
            result = AutomationResult("failed", f"Lever submission error: {exc}")

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
        email = contact_email or (
            profile.get("email") if isinstance(profile, dict) else ""
        ) or ""
        phone = (profile.get("phone") if isinstance(profile, dict) else "") or ""

        if not _try_fill(page, _EMAIL_SELECTORS, email):
            return AutomationResult(
                "needs_review", "could not locate email field on Lever form"
            )
        _try_fill(page, _NAME_SELECTORS, full_name)
        if phone:
            _try_fill(page, _PHONE_SELECTORS, phone)

        if not _try_set_input_files(page, _RESUME_INPUT_SELECTORS, resume_pdf_path):
            return AutomationResult(
                "needs_review", "could not locate resume upload field on Lever form"
            )

        _try_fill(page, _ADDITIONAL_INFO_SELECTORS, cover_letter)

        if self.dry_run:
            return AutomationResult(
                "needs_review", "dry-run mode: form filled but not submitted"
            )

        if not _try_click(page, _SUBMIT_SELECTORS):
            return AutomationResult(
                "needs_review", "could not locate submit button on Lever form"
            )

        page.wait_for_timeout(2000)
        page_text = (page.content() or "").lower()
        if any(kw in page_text for kw in _CONFIRMATION_KEYWORDS):
            return AutomationResult("submitted", f"confirmed via {page.url}")
        if "captcha" in page_text or "unusual traffic" in page_text:
            return AutomationResult("blocked", "CAPTCHA/anti-bot wall detected")
        return AutomationResult(
            "needs_review",
            "form submitted but no confirmation signal detected - verify manually",
        )
