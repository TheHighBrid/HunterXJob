"""Best-effort generic form-filler for arbitrary company career-page ATS forms.

The adapter receives a page from BrowserRuntime, fills only recognized values,
and stops at needs_review when a required field is ambiguous. It never launches
its own browser process.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings
from app.services.automation.base import ApplicationAdapter, AutomationResult
from app.services.browser_runtime import BrowserRuntime, get_browser_runtime
from app.services.qa_bank import QAAnswerBank

_CONFIRMATION_KEYWORDS = [
    "thank you for applying",
    "thanks for applying",
    "application submitted",
    "application received",
    "successfully submitted",
]

_FIELD_PATTERNS: dict[str, list[str]] = {
    "first_name": [r"first.?name"],
    "last_name": [r"last.?name"],
    "full_name": [r"^name$", r"full.?name", r"your.?name"],
    "email": [r"e-?mail"],
    "phone": [r"phone", r"mobile", r"telephone"],
    "resume": [r"resume", r"r[eé]sum[eé]", r"cv"],
    "cover_letter": [r"cover.?letter"],
    "linkedin": [r"linkedin"],
    "portfolio": [r"portfolio", r"website", r"github"],
}

_CRITICAL_FIELDS = {"email", "resume"}
_MIN_CONFIDENCE = 0.6


@dataclass
class FieldMatch:
    field_key: str | None
    confidence: float


def _classify_label(label_text: str) -> FieldMatch:
    text = label_text.strip().lower()
    if not text:
        return FieldMatch(None, 0.0)
    for field_key, patterns in _FIELD_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return FieldMatch(field_key, 0.9)
    return FieldMatch(None, 0.0)


def _label_for(page, element) -> str:
    """Best-effort extraction of a human-readable label for a form element."""
    try:
        aria_label = element.get_attribute("aria-label")
        if aria_label:
            return aria_label
        placeholder = element.get_attribute("placeholder")
        if placeholder:
            return placeholder
        name = element.get_attribute("name") or ""
        elem_id = element.get_attribute("id") or ""
        if elem_id:
            label_loc = page.locator(f"label[for='{elem_id}']")
            if label_loc.count() > 0:
                text = label_loc.first.inner_text()
                if text:
                    return text
        return name or elem_id
    except Exception:
        return ""


class GenericPlaywrightAdapter(ApplicationAdapter):
    enabled = True

    def __init__(
        self,
        qa_bank: QAAnswerBank | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
        dry_run: bool = False,
        browser_runtime: BrowserRuntime | None = None,
    ):
        settings = get_settings()
        self.qa_bank = qa_bank
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

        application_key = str(getattr(application, "id", "generic-application"))
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
            result = AutomationResult("failed", f"generic form-fill error: {exc}")

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
        linkedin = (
            profile.get("linkedin_url") if isinstance(profile, dict) else ""
        ) or ""
        portfolio = (
            profile.get("portfolio_url") if isinstance(profile, dict) else ""
        ) or ""

        value_by_field = {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "linkedin": linkedin,
            "portfolio": portfolio,
            "cover_letter": cover_letter,
        }

        filled_fields: set[str] = set()
        low_confidence_required: list[str] = []

        inputs = page.locator("input:not([type='hidden']), textarea").all()
        for element in inputs:
            try:
                input_type = (element.get_attribute("type") or "text").lower()
            except Exception:
                continue
            if input_type in ("submit", "button", "checkbox", "radio"):
                continue

            label_text = _label_for(page, element)
            match = _classify_label(label_text)

            if input_type == "file":
                if match.field_key == "resume" or match.field_key is None:
                    try:
                        element.set_input_files(resume_pdf_path)
                        filled_fields.add("resume")
                    except Exception:
                        pass
                continue

            if match.field_key and match.confidence >= _MIN_CONFIDENCE:
                value = value_by_field.get(match.field_key)
                if value:
                    try:
                        element.fill(value)
                        filled_fields.add(match.field_key)
                    except Exception:
                        pass
                continue

            if self.qa_bank is not None and label_text:
                qa = self.qa_bank.match_question(label_text)
                if qa is not None:
                    try:
                        element.fill(qa.answer_text)
                    except Exception:
                        pass
                    continue

            try:
                is_required = element.get_attribute("required") is not None
            except Exception:
                is_required = False
            if is_required:
                low_confidence_required.append(label_text or "(unlabeled field)")

        missing_critical = [field for field in _CRITICAL_FIELDS if field not in filled_fields]
        if missing_critical or low_confidence_required:
            detail_parts = []
            if missing_critical:
                detail_parts.append(
                    f"could not confidently fill required field(s): {', '.join(missing_critical)}"
                )
            if low_confidence_required:
                detail_parts.append(
                    f"unrecognized required field(s): {', '.join(low_confidence_required)}"
                )
            return AutomationResult("needs_review", "; ".join(detail_parts))

        if self.dry_run:
            return AutomationResult(
                "needs_review", "dry-run mode: form filled but not submitted"
            )

        submit_btn = page.locator("button[type='submit'], input[type='submit']").first
        if submit_btn.count() == 0:
            return AutomationResult("needs_review", "could not locate a submit button")
        submit_btn.click()
        page.wait_for_timeout(2000)

        page_text = (page.content() or "").lower()
        if any(keyword in page_text for keyword in _CONFIRMATION_KEYWORDS):
            return AutomationResult("submitted", f"confirmed via {page.url}")
        if "captcha" in page_text or "unusual traffic" in page_text:
            return AutomationResult("blocked", "CAPTCHA/anti-bot wall detected")
        return AutomationResult(
            "needs_review",
            "form submitted but no confirmation signal detected - verify manually",
        )
