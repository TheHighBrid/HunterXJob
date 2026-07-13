"""Submit applications through public Greenhouse application forms.

Greenhouse's Job Board API may return a company-branded job-detail URL rather
than a page that contains the application form itself. This adapter resolves
those wrappers, inspects embedded frames, and only fills a scope that contains
both an email field and a resume upload. It never launches or closes Chromium;
pages come from :class:`BrowserRuntime`.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from app.config import get_settings
from app.services.automation.base import ApplicationAdapter, AutomationResult
from app.services.browser_runtime import BrowserRuntime, get_browser_runtime

logger = logging.getLogger("hunterxjob.automation.greenhouse")

_CONFIRMATION_KEYWORDS = [
    "thank you for applying",
    "thanks for applying",
    "application has been submitted",
    "successfully submitted",
    "we have received your application",
    "your application was submitted",
]
_BLOCK_KEYWORDS = ["captcha", "unusual traffic", "verify you are human"]

_FIRST_NAME_SELECTORS = [
    "#first_name",
    "input[name='job_application[first_name]']",
    "input[name='first_name']",
    "input[name*='first_name']",
    "input[autocomplete='given-name']",
]
_LAST_NAME_SELECTORS = [
    "#last_name",
    "input[name='job_application[last_name]']",
    "input[name='last_name']",
    "input[name*='last_name']",
    "input[autocomplete='family-name']",
]
_EMAIL_SELECTORS = [
    "#email",
    "input[name='job_application[email]']",
    "input[type='email']",
    "input[name='email']",
    "input[name*='email']",
    "input[autocomplete='email']",
]
_PHONE_SELECTORS = [
    "#phone",
    "input[name='job_application[phone]']",
    "input[type='tel']",
    "input[name='phone']",
    "input[name*='phone']",
    "input[autocomplete='tel']",
]
_RESUME_INPUT_SELECTORS = [
    "#resume",
    "input[type='file'][name*='resume']",
    "input[type='file'][id*='resume']",
    "input[type='file'][aria-label*='resume' i]",
]
_COVER_LETTER_TEXTAREA_SELECTORS = [
    "#cover_letter_text",
    "textarea[name*='cover_letter']",
    "textarea[id*='cover_letter']",
]
_SUBMIT_SELECTORS = [
    "#submit_app",
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Submit Application')",
]
_APPLY_LINK_SELECTORS = [
    "a[href*='/apply']",
    "a[href*='job-boards.greenhouse.io']",
    "a[href*='boards.greenhouse.io']",
    "a[href*='greenhouse.io/embed/job_app']",
    "a:has-text('Apply for this role')",
    "a:has-text('Apply Now')",
]
_APPLY_BUTTON_SELECTORS = [
    "button:has-text('Apply for this role')",
    "button:has-text('Apply Now')",
    "button:has-text('Apply')",
]
_SAFE_GREENHOUSE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")

_FIELD_GROUPS: dict[str, list[str]] = {
    "first_name": _FIRST_NAME_SELECTORS,
    "last_name": _LAST_NAME_SELECTORS,
    "email": _EMAIL_SELECTORS,
    "phone": _PHONE_SELECTORS,
    "resume": _RESUME_INPUT_SELECTORS,
    "cover_letter": _COVER_LETTER_TEXTAREA_SELECTORS,
    "submit": _SUBMIT_SELECTORS,
}


def _scope_entries(page: Any) -> list[tuple[str, Any]]:
    """Return the main page plus non-main frames as locator scopes."""
    entries: list[tuple[str, Any]] = [("main", page)]
    try:
        frames = list(page.frames)
    except Exception:  # noqa: BLE001
        frames = []
    main_frame = getattr(page, "main_frame", None)
    for index, frame in enumerate(frames):
        if frame is main_frame:
            continue
        frame_url = getattr(frame, "url", "") or ""
        entries.append((f"frame[{index}]", frame))
        if frame_url:
            entries[-1] = (f"frame[{index}] {frame_url}", frame)
    return entries


def _locator_count(scope: Any, selector: str) -> int:
    try:
        return int(scope.locator(selector).count())
    except Exception:  # noqa: BLE001
        return 0


def _matching_selector(scope: Any, selectors: list[str]) -> str | None:
    for selector in selectors:
        if _locator_count(scope, selector) > 0:
            return selector
    return None


def _resume_selector(scope: Any) -> str | None:
    selector = _matching_selector(scope, _RESUME_INPUT_SELECTORS)
    if selector:
        return selector
    # A single generic file field is safe to treat as the resume. Multiple file
    # inputs are ambiguous, so the adapter refuses to guess.
    if _locator_count(scope, "input[type='file']") == 1:
        return "input[type='file']"
    return None


def _find_form_scope(page: Any) -> tuple[str, Any] | None:
    for name, scope in _scope_entries(page):
        if _matching_selector(scope, _EMAIL_SELECTORS) and _resume_selector(scope):
            return name, scope
    return None


def _wait_for_form(page: Any, timeout_ms: int = 5_000) -> tuple[str, Any] | None:
    attempts = max(1, timeout_ms // 250)
    for _ in range(attempts):
        resolved = _find_form_scope(page)
        if resolved:
            return resolved
        try:
            page.wait_for_timeout(250)
        except Exception:  # noqa: BLE001
            break
    return _find_form_scope(page)


def _try_fill(scope: Any, selectors: list[str], value: str) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if locator.count() > 0:
                locator.fill(value)
                return selector
        except Exception:  # noqa: BLE001
            continue
    return None


def _try_set_input_files(scope: Any, file_path: str) -> str | None:
    selectors = list(_RESUME_INPUT_SELECTORS)
    if _locator_count(scope, "input[type='file']") == 1:
        selectors.append("input[type='file']")
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if locator.count() > 0:
                locator.set_input_files(file_path)
                return selector
        except Exception:  # noqa: BLE001
            continue
    return None


def _try_click(scope: Any, selectors: list[str]) -> str | None:
    for selector in selectors:
        try:
            locator = scope.locator(selector).first
            if locator.count() > 0:
                locator.click()
                return selector
        except Exception:  # noqa: BLE001
            continue
    return None


def _follow_apply_action(page: Any, diagnostics: dict[str, Any]) -> bool:
    """Follow a branded site's apply link or button without opening a new tab."""
    for scope_name, scope in _scope_entries(page):
        for selector in _APPLY_LINK_SELECTORS:
            try:
                locator = scope.locator(selector).first
                if locator.count() == 0:
                    continue
                href = locator.get_attribute("href")
                if not href:
                    continue
                base_url = getattr(scope, "url", "") or getattr(page, "url", "")
                target = urljoin(base_url, href)
                diagnostics["resolution_steps"].append(
                    {"action": "follow_apply_link", "scope": scope_name, "target": target}
                )
                page.goto(target, wait_until="domcontentloaded")
                return True
            except Exception as exc:  # noqa: BLE001
                diagnostics["resolution_steps"].append(
                    {"action": "apply_link_failed", "selector": selector, "error": str(exc)}
                )

    for scope_name, scope in _scope_entries(page):
        selector = _try_click(scope, _APPLY_BUTTON_SELECTORS)
        if selector:
            diagnostics["resolution_steps"].append(
                {"action": "click_apply_button", "scope": scope_name, "selector": selector}
            )
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:  # noqa: BLE001
                pass
            return True
    return False


def _greenhouse_candidate_urls(job_posting: Any) -> list[str]:
    board_token = str(getattr(job_posting, "company", "") or "").strip()
    external_id = str(getattr(job_posting, "external_id", "") or "").strip()
    if not (
        board_token
        and external_id
        and _SAFE_GREENHOUSE_COMPONENT.fullmatch(board_token)
        and _SAFE_GREENHOUSE_COMPONENT.fullmatch(external_id)
    ):
        return []
    return [
        f"https://job-boards.greenhouse.io/{board_token}/jobs/{external_id}",
        f"https://boards.greenhouse.io/{board_token}/jobs/{external_id}",
        f"https://boards.greenhouse.io/embed/job_app?for={board_token}&token={external_id}",
    ]


def _resolve_form_scope(
    page: Any, job_posting: Any, diagnostics: dict[str, Any]
) -> tuple[str, Any] | None:
    resolved = _wait_for_form(page, timeout_ms=2_500)
    if resolved:
        diagnostics["resolution_steps"].append({"action": "form_found_on_initial_page"})
        return resolved

    if _follow_apply_action(page, diagnostics):
        resolved = _wait_for_form(page, timeout_ms=8_000)
        if resolved:
            diagnostics["resolution_steps"].append({"action": "form_found_after_apply_action"})
            return resolved

    current_url = str(getattr(page, "url", "") or "")
    for candidate in _greenhouse_candidate_urls(job_posting):
        if candidate.rstrip("/") == current_url.rstrip("/"):
            continue
        diagnostics["candidate_urls_tried"].append(candidate)
        try:
            page.goto(candidate, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            diagnostics["resolution_steps"].append(
                {"action": "candidate_navigation_failed", "target": candidate, "error": str(exc)}
            )
            continue

        resolved = _wait_for_form(page, timeout_ms=6_000)
        if resolved:
            diagnostics["resolution_steps"].append(
                {"action": "form_found_on_greenhouse_candidate", "target": candidate}
            )
            return resolved

        if _follow_apply_action(page, diagnostics):
            resolved = _wait_for_form(page, timeout_ms=6_000)
            if resolved:
                diagnostics["resolution_steps"].append(
                    {"action": "form_found_after_candidate_apply", "target": candidate}
                )
                return resolved
    return None


def _field_inventory(page: Any) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for scope_name, scope in _scope_entries(page):
        fields: dict[str, list[str]] = {}
        for field_name, selectors in _FIELD_GROUPS.items():
            matches = [selector for selector in selectors if _locator_count(scope, selector) > 0]
            if matches:
                fields[field_name] = matches
        generic_file_count = _locator_count(scope, "input[type='file']")
        inventory.append(
            {
                "scope": scope_name,
                "url": str(getattr(scope, "url", "") or ""),
                "fields": fields,
                "generic_file_inputs": generic_file_count,
            }
        )
    return inventory


def _page_text(page: Any) -> str:
    chunks: list[str] = []
    for _, scope in _scope_entries(page):
        try:
            chunks.append(scope.content() or "")
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(chunks).lower()


def _page_urls(page: Any) -> list[str]:
    urls: list[str] = []
    for _, scope in _scope_entries(page):
        url = str(getattr(scope, "url", "") or "")
        if url and url not in urls:
            urls.append(url)
    return urls


def _write_diagnostics(metadata_path: str | None, diagnostics: dict[str, Any]) -> None:
    if not metadata_path:
        return
    path = Path(metadata_path)
    try:
        payload: dict[str, Any] = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        payload["adapter_diagnostics"] = diagnostics
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not append Greenhouse diagnostics to metadata: %s", exc)


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
        diagnostics: dict[str, Any] = {
            "initial_url": apply_url,
            "candidate_urls_tried": [],
            "resolution_steps": [],
            "filled_fields": {},
        }
        try:
            with self.browser_runtime.application_page(application_key) as page:
                page.set_default_timeout(self.timeout_ms)
                page.goto(apply_url, wait_until="domcontentloaded")
                diagnostics["initial_landed_url"] = str(getattr(page, "url", "") or "")

                resolved = _resolve_form_scope(page, job_posting, diagnostics)
                diagnostics["final_url"] = str(getattr(page, "url", "") or "")
                try:
                    diagnostics["page_title"] = page.title()
                except Exception:  # noqa: BLE001
                    diagnostics["page_title"] = ""
                diagnostics["frame_urls"] = _page_urls(page)
                diagnostics["field_inventory"] = _field_inventory(page)

                if resolved is None:
                    if any(keyword in _page_text(page) for keyword in _BLOCK_KEYWORDS):
                        result = AutomationResult("blocked", "CAPTCHA/anti-bot wall detected")
                    else:
                        result = AutomationResult(
                            "needs_review",
                            "could not resolve a Greenhouse form with email and resume fields",
                        )
                else:
                    scope_name, form_scope = resolved
                    diagnostics["selected_scope"] = scope_name
                    result = self._complete_form(
                        page,
                        form_scope,
                        application,
                        resume_pdf_path,
                        cover_letter,
                        contact_email,
                        diagnostics,
                    )
        except Exception as exc:  # noqa: BLE001
            diagnostics["exception"] = str(exc)
            result = AutomationResult("failed", f"Greenhouse submission error: {exc}")

        result.evidence.update(self.browser_runtime.artifacts_for(application_key))
        _write_diagnostics(result.evidence.get("metadata"), diagnostics)
        return result

    def _complete_form(
        self,
        page: Any,
        form_scope: Any,
        application: Any,
        resume_pdf_path: str,
        cover_letter: str,
        contact_email: str | None,
        diagnostics: dict[str, Any],
    ) -> AutomationResult:
        profile = getattr(application, "profile", None) or {}
        full_name = (profile.get("full_name") if isinstance(profile, dict) else "") or ""
        first_name, _, last_name = full_name.partition(" ")
        email = contact_email or (
            profile.get("email") if isinstance(profile, dict) else ""
        ) or ""
        phone = (profile.get("phone") if isinstance(profile, dict) else "") or ""

        if not email:
            return AutomationResult("needs_review", "profile email is missing")

        email_selector = _try_fill(form_scope, _EMAIL_SELECTORS, email)
        if not email_selector:
            return AutomationResult(
                "needs_review", "could not fill email field on resolved Greenhouse form"
            )
        diagnostics["filled_fields"]["email"] = email_selector

        if first_name:
            selector = _try_fill(form_scope, _FIRST_NAME_SELECTORS, first_name)
            if selector:
                diagnostics["filled_fields"]["first_name"] = selector
        if last_name:
            selector = _try_fill(form_scope, _LAST_NAME_SELECTORS, last_name)
            if selector:
                diagnostics["filled_fields"]["last_name"] = selector
        if phone:
            selector = _try_fill(form_scope, _PHONE_SELECTORS, phone)
            if selector:
                diagnostics["filled_fields"]["phone"] = selector

        resume_selector = _try_set_input_files(form_scope, resume_pdf_path)
        if not resume_selector:
            return AutomationResult(
                "needs_review", "could not fill resume upload on resolved Greenhouse form"
            )
        diagnostics["filled_fields"]["resume"] = resume_selector

        if cover_letter:
            selector = _try_fill(form_scope, _COVER_LETTER_TEXTAREA_SELECTORS, cover_letter)
            if selector:
                diagnostics["filled_fields"]["cover_letter"] = selector

        if self.dry_run:
            return AutomationResult("needs_review", "dry-run mode: form filled but not submitted")

        submit_selector = _try_click(form_scope, _SUBMIT_SELECTORS)
        if not submit_selector:
            return AutomationResult(
                "needs_review", "could not locate submit button on Greenhouse form"
            )
        diagnostics["filled_fields"]["submit"] = submit_selector

        try:
            page.wait_for_timeout(2_000)
        except Exception:  # noqa: BLE001
            pass
        page_text = _page_text(page)
        page_urls = [url.lower() for url in _page_urls(page)]
        confirmed = any(keyword in page_text for keyword in _CONFIRMATION_KEYWORDS) or any(
            "confirmation" in url or "thanks" in url for url in page_urls
        )
        if confirmed:
            return AutomationResult("submitted", f"confirmed via {getattr(page, 'url', '')}")
        if any(keyword in page_text for keyword in _BLOCK_KEYWORDS):
            return AutomationResult("blocked", "CAPTCHA/anti-bot wall detected")
        return AutomationResult(
            "needs_review",
            "form submitted but no confirmation signal detected - verify manually",
        )
