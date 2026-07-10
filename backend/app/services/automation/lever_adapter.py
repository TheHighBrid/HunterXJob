"""Submits applications via a job's public Lever application form.

Lever job pages (jobs.lever.co/<company>/<posting-id>/apply) embed a
standard application form with predictable field names (name, email, phone,
a resume file input, and an "Additional Information" textarea often used
for cover letters). This adapter drives that form with Playwright and only
reports "submitted" after observing a real confirmation signal.
"""
from __future__ import annotations

from app.services.automation.base import ApplicationAdapter, AutomationResult

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

    def __init__(self, headless: bool = True, timeout_ms: int = 30_000, dry_run: bool = False):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.dry_run = dry_run

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

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AutomationResult("failed", "Playwright is not installed")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                try:
                    page = browser.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    page.goto(apply_url, wait_until="domcontentloaded")

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

                    resume_ok = _try_set_input_files(page, _RESUME_INPUT_SELECTORS, resume_pdf_path)
                    if not resume_ok:
                        return AutomationResult(
                            "needs_review", "could not locate resume upload field on Lever form"
                        )

                    _try_fill(page, _ADDITIONAL_INFO_SELECTORS, cover_letter)

                    if self.dry_run:
                        return AutomationResult(
                            "needs_review", "dry-run mode: form filled but not submitted"
                        )

                    clicked = _try_click(page, _SUBMIT_SELECTORS)
                    if not clicked:
                        return AutomationResult(
                            "needs_review", "could not locate submit button on Lever form"
                        )

                    page.wait_for_timeout(2000)
                    page_text = (page.content() or "").lower()

                    confirmed = any(kw in page_text for kw in _CONFIRMATION_KEYWORDS)
                    if confirmed:
                        return AutomationResult("submitted", f"confirmed via {page.url}")

                    if "captcha" in page_text or "unusual traffic" in page_text:
                        return AutomationResult("blocked", "CAPTCHA/anti-bot wall detected")

                    return AutomationResult(
                        "needs_review",
                        "form submitted but no confirmation signal detected - verify manually",
                    )
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001
            return AutomationResult("failed", f"Lever submission error: {exc}")
