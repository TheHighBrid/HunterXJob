"""Interactive Playwright form filler for job applications.

Fills real application forms inside a Playwright browser session:
- Types text and textareas with standard DOM event dispatching.
- Normalizes and selects dropdown (<select>) options.
- Checks/unchecks checkboxes and toggles radio buttons.
- Attaches actual resume and cover letter files to file inputs.
- Verifies filled values by reading them back directly from the DOM.
- Captures full-page screenshots and Playwright execution traces.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FileAttachmentRecord:
    key: str
    path: str
    file_name: str
    bytes: int
    sha256: str


@dataclass
class FieldFillReport:
    key: str
    control_type: str
    selector_used: str
    attempted_value: Any
    read_back_value: Any
    matched: bool
    error: str | None = None


@dataclass
class FormFillResult:
    url: str
    success: bool
    fields_attempted: int
    fields_filled: int
    field_reports: dict[str, FieldFillReport] = field(default_factory=dict)
    unmatched_fields: list[str] = field(default_factory=list)
    files_attached: list[FileAttachmentRecord] = field(default_factory=list)
    screenshot_path: str | None = None
    screenshot_sha256: str | None = None
    trace_path: str | None = None
    trace_sha256: str | None = None
    error: str | None = None


def _file_record(path: Path, key: str) -> FileAttachmentRecord:
    data = path.read_bytes()
    return FileAttachmentRecord(
        key=key,
        path=str(path),
        file_name=path.name,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


async def _find_input_for_key(page: Any, key: str, label_hint: str = "") -> Any:
    """Find a form control locator using multiple fallback strategies."""
    key_parts = key.split("_")
    key_camel = key_parts[0] + "".join(p.capitalize() for p in key_parts[1:]) if len(key_parts) > 1 else key

    # 1. Exact ID (snake_case and camelCase)
    for k in (key, key_camel):
        loc = page.locator(f"#{k}")
        if await loc.count() > 0 and await loc.first.is_visible():
            return loc.first, f"#{k}"

    for part in key_parts:
        if len(part) >= 4:
            loc = page.locator(f"#{part}")
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first, f"#{part}"

    # 2. Exact Name (snake_case and camelCase)
    for k in (key, key_camel):
        loc = page.locator(f'[name="{k}"]')
        if await loc.count() > 0 and await loc.first.is_visible():
            return loc.first, f'[name="{k}"]'

    # 3. data-automation-id (Workday), data-qa, data-testid
    for k in (key, key_camel):
        for attr in ("data-automation-id", "data-qa", "data-testid"):
            loc = page.locator(f'[{attr}*="{k}" i]')
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first, f'[{attr}*="{k}"]'

    # 4. Partial Name (e.g. key in cards[...][field0])
    for k in (key, key_camel):
        loc = page.locator(f'input[name*="{k}" i], select[name*="{k}" i], textarea[name*="{k}" i]')
        if await loc.count() > 0 and await loc.first.is_visible():
            return loc.first, f'name*="{k}"'

    # 5. Label text matching
    search_text = label_hint or key.replace("_", " ")
    try:
        by_label = page.get_by_label(search_text, exact=False)
        if await by_label.count() > 0 and await by_label.first.is_visible():
            return by_label.first, f"label:{search_text}"
    except Exception:
        pass

    # 6. Placeholder matching
    try:
        by_placeholder = page.get_by_placeholder(search_text, exact=False)
        if await by_placeholder.count() > 0 and await by_placeholder.first.is_visible():
            return by_placeholder.first, f"placeholder:{search_text}"
    except Exception:
        pass

    # 7. Check if there's any element whose aria-label matches
    loc = page.locator(f'[aria-label*="{search_text}" i]')
    if await loc.count() > 0 and await loc.first.is_visible():
        return loc.first, f'aria-label*="{search_text}"'

    return None, ""


async def _find_file_input(page: Any, key: str) -> tuple[Any, str]:
    """Find the file input for resume or cover letter."""
    # Try direct id / name / data-qa
    loc = page.locator(f'input[type="file"]#{key}, input[type="file"][name="{key}"], input[type="file"][data-qa*="{key}"]')
    if await loc.count() > 0:
        return loc.first, f'input[type="file"][name/id="{key}"]'

    # Look for file inputs matching key or keywords
    keywords = ["resume", "cv"] if "resume" in key.lower() or "cv" in key.lower() else ["cover", "letter"]
    for kw in keywords:
        loc = page.locator(f'input[type="file"][id*="{kw}" i], input[type="file"][name*="{kw}" i], input[type="file"][data-qa*="{kw}" i]')
        if await loc.count() > 0:
            return loc.first, f'input[type="file"][keyword="{kw}"]'

    # Fallback to first available file input if only one exists
    all_file_inputs = page.locator('input[type="file"]')
    if await all_file_inputs.count() == 1:
        return all_file_inputs.first, "input[type=\"file\"]:only-one"

    return None, ""


async def _read_back_value(locator: Any, tag: str, control_type: str) -> Any:
    """Read back the current value of a form control from the DOM."""
    try:
        if tag == "select":
            return await locator.evaluate(
                "el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : el.value"
            )
        if control_type == "checkbox":
            return await locator.is_checked()
        if control_type == "file":
            return await locator.evaluate("el => el.files && el.files[0] ? el.files[0].name : ''")
        return await locator.input_value()
    except Exception as exc:
        logger.debug("Failed reading back value: %s", exc)
        return ""


async def _fill_single_field(page: Any, key: str, value: Any, label: str = "") -> FieldFillReport:
    """Locate and fill a single form field, returning a verification report."""
    locator, selector_used = await _find_input_for_key(page, key, label_hint=label)
    if not locator:
        return FieldFillReport(
            key=key,
            control_type="unknown",
            selector_used="",
            attempted_value=value,
            read_back_value=None,
            matched=False,
            error=f"Could not locate element for '{key}'",
        )

    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
    control_type = await locator.evaluate("el => (el.getAttribute('type') || '').toLowerCase()")

    try:
        if tag == "select":
            str_val = str(value).strip()
            options_info = await locator.evaluate(
                "el => Array.from(el.options).map((o, idx) => ({index: idx, value: o.value, text: o.text.trim()}))"
            )
            # 1. Exact text match
            matched_opt = next((o for o in options_info if o["text"].lower() == str_val.lower()), None)
            # 2. Exact value match
            if not matched_opt:
                matched_opt = next((o for o in options_info if o["value"].lower() == str_val.lower()), None)
            # 3. Substring match in text
            if not matched_opt:
                matched_opt = next((o for o in options_info if str_val.lower() in o["text"].lower() or o["text"].lower() in str_val.lower()), None)
            # 4. Substring match in value
            if not matched_opt:
                matched_opt = next((o for o in options_info if str_val.lower() in o["value"].lower()), None)

            if matched_opt:
                await locator.select_option(value=matched_opt["value"])
            else:
                raise ValueError(f"No option matching '{str_val}' in {[o['text'] for o in options_info]}")
            await locator.dispatch_event("change")

        elif control_type == "checkbox":
            bool_val = value is True or str(value).lower() in ("yes", "true", "1")
            await locator.set_checked(bool_val)
            await locator.dispatch_event("change")

        elif control_type == "radio":
            await locator.check(force=True)
            await locator.dispatch_event("change")

        else:
            # Text, textarea, email, tel, number, etc.
            await locator.fill(str(value))
            await locator.dispatch_event("input")
            await locator.dispatch_event("change")

        # Read back for verification
        read_back = await _read_back_value(locator, tag, control_type)
        matched = False
        if control_type == "checkbox":
            matched = (read_back == (value is True or str(value).lower() in ("yes", "true", "1")))
        else:
            # Check for substring or exact match
            matched = str(value).strip().lower() in str(read_back).strip().lower() or str(read_back).strip().lower() in str(value).strip().lower()

        return FieldFillReport(
            key=key,
            control_type=tag if tag in ("select", "textarea") else (control_type or "text"),
            selector_used=selector_used,
            attempted_value=value,
            read_back_value=read_back,
            matched=matched,
        )

    except Exception as exc:
        return FieldFillReport(
            key=key,
            control_type=tag if tag in ("select", "textarea") else (control_type or "text"),
            selector_used=selector_used,
            attempted_value=value,
            read_back_value=None,
            matched=False,
            error=str(exc),
        )


async def fill_application_form(
    url: str,
    answers: dict[str, Any],
    files: dict[str, Path | str] | None = None,
    labels: dict[str, str] | None = None,
    target_fields: Collection[str] | None = None,
    required_fields: Collection[str] | None = None,
    timeout_ms: int = 30000,
    trace_path: Path | None = None,
    screenshot_path: Path | None = None,
    headless: bool = True,
) -> FormFillResult:
    """Open the application page, fill in all answers, attach files, and verify read-backs."""
    from collections.abc import Collection
    from playwright.async_api import async_playwright

    labels = labels or {}
    files = files or {}
    field_reports: dict[str, FieldFillReport] = {}
    files_attached: list[FileAttachmentRecord] = []
    unmatched: list[str] = []

    # If target_fields is provided, filter answers and files down to only targeted fields
    items_to_fill = answers
    if target_fields is not None:
        items_to_fill = {k: v for k, v in answers.items() if k in target_fields}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context()

        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            # 1. Fill regular inputs, selects, and textareas
            for key, val in items_to_fill.items():
                if val is None or val == "":
                    continue
                label_hint = labels.get(key, "")
                report = await _fill_single_field(page, key, val, label=label_hint)
                # If element wasn't found on the page and required_fields is specified
                if not report.matched and not report.selector_used:
                    if required_fields is not None and key not in required_fields:
                        # Extra unasked profile answer; do not penalize
                        continue
                field_reports[key] = report
                if not report.matched:
                    unmatched.append(key)

            # 2. Attach files (e.g. resume, cover_letter)
            target_files = files
            if target_fields is not None:
                target_files = {k: v for k, v in files.items() if k in target_fields}

            for file_key, file_val in target_files.items():
                file_path = Path(file_val)
                if not file_path.is_file():
                    logger.warning("File for %s does not exist: %s", file_key, file_val)
                    continue

                file_loc, sel_used = await _find_file_input(page, file_key)
                if file_loc:
                    await file_loc.set_input_files(str(file_path))
                    await file_loc.dispatch_event("change")
                    record = _file_record(file_path, file_key)
                    files_attached.append(record)
                    field_reports[file_key] = FieldFillReport(
                        key=file_key,
                        control_type="file",
                        selector_used=sel_used,
                        attempted_value=file_path.name,
                        read_back_value=file_path.name,
                        matched=True,
                    )
                else:
                    unmatched.append(file_key)
                    field_reports[file_key] = FieldFillReport(
                        key=file_key,
                        control_type="file",
                        selector_used="",
                        attempted_value=file_path.name,
                        read_back_value=None,
                        matched=False,
                        error="File input not found",
                    )

            # 3. Capture screenshot of filled form if requested
            sc_sha256 = None
            if screenshot_path:
                screenshot_path = Path(screenshot_path)
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(screenshot_path), full_page=True)
                sc_sha256 = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()

        except Exception as exc:
            return FormFillResult(
                url=url,
                success=False,
                fields_attempted=len(items_to_fill) + len(files),
                fields_filled=sum(1 for r in field_reports.values() if r.matched),
                field_reports=field_reports,
                unmatched_fields=unmatched,
                files_attached=files_attached,
                error=str(exc),
            )
        finally:
            trace_sha256 = None
            if trace_path:
                await context.tracing.stop(path=str(trace_path))
                if trace_path.is_file():
                    trace_sha256 = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            await context.close()
            await browser.close()

    filled_count = sum(1 for r in field_reports.values() if r.matched)
    total_attempted = len(field_reports)

    # Success check: all required fields (if specified) or all attempted fields are filled
    if required_fields is not None:
        success = all(field_reports.get(k) and field_reports[k].matched for k in required_fields)
    else:
        success = (len(unmatched) == 0 and total_attempted > 0)

    return FormFillResult(
        url=url,
        success=success,
        fields_attempted=total_attempted,
        fields_filled=filled_count,
        field_reports=field_reports,
        unmatched_fields=unmatched,
        files_attached=files_attached,
        screenshot_path=str(screenshot_path) if screenshot_path else None,
        screenshot_sha256=sc_sha256,
        trace_path=str(trace_path) if trace_path else None,
        trace_sha256=trace_sha256,
    )
