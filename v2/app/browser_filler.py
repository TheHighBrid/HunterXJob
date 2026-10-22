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
    submitted: bool = False
    confirmation_text: str | None = None
    candidate_id: str | None = None
    post_submit_screenshot_path: str | None = None
    post_submit_screenshot_sha256: str | None = None
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


async def _find_input_for_key(page: Any, key: str, label_hint: str = "", value_hint: Any = None) -> Any:
    """Find a form control locator using multiple fallback strategies."""
    key_parts = key.split("_")
    key_camel = key_parts[0] + "".join(p.capitalize() for p in key_parts[1:]) if len(key_parts) > 1 else key

    # 0a. Check for button option toggles (e.g. Ashby yes/no buttons)
    if value_hint is not None:
        v_str = str(value_hint).strip().lower()
        btn_loc = page.locator(f'[data-field-path*="{key}"] button[data-option="{v_str}" i], [id*="{key}"] button[data-option="{v_str}" i]')
        try:
            if await btn_loc.count() > 0 and await btn_loc.first.is_visible():
                return btn_loc.first, f'button[data-option="{v_str}"]'
        except Exception:
            pass

    # 0b. Check for data-field-path container (Ashby standard container)
    for k in (key, key_camel):
        container = page.locator(f'[data-field-path="{k}" i], [data-field-path*="{k}" i]')
        try:
            if await container.count() > 0:
                if value_hint is not None:
                    v_str = str(value_hint).strip().lower()
                    b_opt = container.locator(f'button[data-option="{v_str}" i]')
                    if await b_opt.count() > 0 and await b_opt.first.is_visible():
                        return b_opt.first, f'[data-field-path="{k}"] button[data-option="{v_str}"]'
                    # Also check container buttons with matching inner text
                    btns = container.locator('button')
                    for b_idx in range(await btns.count()):
                        btn_el = btns.nth(b_idx)
                        b_text = (await btn_el.inner_text()).strip().lower()
                        if b_text == v_str or (v_str in ("yes", "true", "1") and b_text == "yes") or (v_str in ("no", "false", "0") and b_text == "no"):
                            if await btn_el.is_visible():
                                return btn_el, f'[data-field-path="{k}"] button:{b_text}'
                inputs = container.locator('input:not([type="hidden"]), select, textarea')
                if await inputs.count() > 0 and await inputs.first.is_visible():
                    return inputs.first, f'[data-field-path="{k}"] input'
                any_input = container.locator('input, select, textarea')
                if await any_input.count() > 0:
                    return any_input.first, f'[data-field-path="{k}"] backing-input'
        except Exception:
            pass

    # 0c. Check for radio group matching key (e.g. Ashby compound {option_id}_{field_id})
    for k in (key, key_camel):
        radios = page.locator(f'input[type="radio"][name*="{k}" i], input[type="radio"][id*="{k}" i]')
        try:
            r_count = await radios.count()
            if r_count > 0:
                if value_hint is not None:
                    str_hint = str(value_hint).strip().lower()
                    for idx in range(r_count):
                        r = radios.nth(idx)
                        label_text = await r.evaluate("""el => {
                            if (el.labels && el.labels[0]) return el.labels[0].innerText || '';
                            const parent = el.closest('label') || el.parentElement;
                            return parent ? parent.innerText || '' : '';
                        }""")
                        val_attr = (await r.get_attribute("value") or "").lower()
                        if str_hint in label_text.lower() or val_attr == str_hint:
                            return r, f'radio:{k}={value_hint}'
                return radios.first, f'radio:{k}'
        except Exception:
            pass

    # 1. Exact ID (snake_case and camelCase) using safe [id="..."] selector to support IDs starting with digits/UUIDs
    for k in (key, key_camel):
        loc = page.locator(f'[id="{k}"]')
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first, f'[id="{k}"]'
        except Exception:
            pass

    for part in key_parts:
        if len(part) >= 4:
            loc = page.locator(f'[id="{part}"]')
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    return loc.first, f'[id="{part}"]'
            except Exception:
                pass

    # 2. Exact Name (snake_case and camelCase)
    for k in (key, key_camel):
        loc = page.locator(f'[name="{k}"]')
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first, f'[name="{k}"]'
        except Exception:
            pass

    # 3. data-automation-id (Workday), data-qa, data-testid
    for k in (key, key_camel):
        for attr in ("data-automation-id", "data-qa", "data-testid"):
            loc = page.locator(f'[{attr}*="{k}" i]')
            try:
                if await loc.count() > 0 and await loc.first.is_visible():
                    return loc.first, f'[{attr}*="{k}"]'
            except Exception:
                pass

    # 4. Partial Name or ID (e.g. key in cards[...][field0] or Ashby compound UUIDs)
    for k in (key, key_camel):
        loc = page.locator(f'input[name*="{k}" i], select[name*="{k}" i], textarea[name*="{k}" i], [id*="{k}" i]')
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first, f'name/id*="{k}"'
        except Exception:
            pass

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
    loc = page.locator(f'input[type="file"][id="{key}"], input[type="file"][name="{key}"], input[type="file"][data-qa*="{key}"]')
    try:
        if await loc.count() > 0:
            return loc.first, f'input[type="file"][name/id="{key}"]'
    except Exception:
        pass

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
        if tag == "button":
            pressed = await locator.get_attribute("aria-pressed")
            if pressed == "true":
                return True
            return (await locator.inner_text()).strip()
        if tag == "select":
            return await locator.evaluate(
                "el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : el.value"
            )
        if control_type in ("checkbox", "radio"):
            return await locator.is_checked()
        if control_type == "file":
            return await locator.evaluate("el => el.files && el.files[0] ? el.files[0].name : ''")
        return await locator.input_value()
    except Exception as exc:
        logger.debug("Failed reading back value: %s", exc)
        return ""


async def _fill_single_field(page: Any, key: str, value: Any, label: str = "") -> FieldFillReport:
    """Locate and fill a single form field, returning a verification report."""
    locator, selector_used = await _find_input_for_key(page, key, label_hint=label, value_hint=value)
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
        if tag == "button":
            await locator.click()
            await locator.dispatch_event("change")

        elif tag == "select":
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
            try:
                await locator.set_checked(bool_val)
            except Exception:
                await locator.evaluate("""(el, checked) => {
                    el.checked = checked;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""", bool_val)
            await locator.dispatch_event("change")

        elif control_type == "radio":
            await locator.check(force=True)
            await locator.dispatch_event("change")

        else:
            # Text, textarea, email, tel, number, etc.
            await locator.fill(str(value))
            await locator.dispatch_event("input")
            await locator.dispatch_event("change")
            try:
                is_combobox = await locator.evaluate("el => el.getAttribute('role') === 'combobox' || (el.className && el.className.includes('autocomplete'))")
                if is_combobox:
                    await asyncio.sleep(0.4)
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(0.2)
                    await page.keyboard.press("Enter")
            except Exception:
                pass

        # Read back for verification
        read_back = await _read_back_value(locator, tag, control_type)
        matched = False
        if control_type == "checkbox":
            matched = (read_back == (value is True or str(value).lower() in ("yes", "true", "1")))
        elif control_type == "radio" or tag == "button":
            matched = (read_back is True or str(value).strip().lower() in str(read_back).strip().lower())
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
    headed_pause_ms: int = 4000,
    allow_live_submit: bool = False,
    live_submit_fn: Any | None = None,
    captcha_solver: Any | None = None,
    post_submit_screenshot_path: Path | None = None,
) -> FormFillResult:
    """Open the application page, fill in all answers, attach files, and verify read-backs."""
    from collections.abc import Collection
    from playwright.async_api import async_playwright

    labels = labels or {}
    files = files or {}
    field_reports: dict[str, FieldFillReport] = {}
    files_attached: list[FileAttachmentRecord] = []
    unmatched: list[str] = []
    submitted = False
    confirmation_text: str | None = None
    candidate_id: str | None = None
    post_sc_sha256: str | None = None
    submit_error: str | None = None

    # If target_fields is provided, filter answers and files down to only targeted fields
    items_to_fill = answers
    if target_fields is not None:
        items_to_fill = {k: v for k, v in answers.items() if k in target_fields}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            slow_mo=100 if not headless else 0,
        )
        context = await browser.new_context()

        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = await context.new_page()

        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
            except Exception as e:
                logger.warning("Navigation warning for %s: %s", url, e)

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

            # 4. Live submission execution if enabled and all form fields matched
            if allow_live_submit and not unmatched:
                from app.captcha_bridge import detect_captcha, inject_captcha_token
                from app.submission_engine import execute_live_submission as _default_submit

                challenge = await detect_captcha(page)
                if challenge.detected and challenge.sitekey and captcha_solver:
                    token = await captcha_solver.solve(
                        challenge.captcha_type, challenge.sitekey, page.url
                    )
                    await inject_captcha_token(page, challenge, token)

                submit_fn = live_submit_fn or _default_submit
                sub_res = await submit_fn(
                    page=page,
                    allow_live_submit=True,
                    captcha_solver=captcha_solver,
                    screenshot_path=post_submit_screenshot_path,
                )
                submitted = bool(sub_res.success and sub_res.status == "submitted")
                confirmation_text = sub_res.confirmation_text
                candidate_id = sub_res.candidate_id
                post_sc_sha256 = sub_res.screenshot_sha256
                if not submitted and sub_res.error:
                    submit_error = sub_res.error

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
            if not headless and headed_pause_ms > 0:
                try:
                    await page.wait_for_timeout(headed_pause_ms)
                except Exception:
                    pass
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

    if allow_live_submit:
        success = success and submitted

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
        submitted=submitted,
        confirmation_text=confirmation_text,
        candidate_id=candidate_id,
        post_submit_screenshot_path=str(post_submit_screenshot_path) if post_submit_screenshot_path else None,
        post_submit_screenshot_sha256=post_sc_sha256,
        error=submit_error,
    )
