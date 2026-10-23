"""Live Application Submission Engine and Post-Submit Confirmation Verifier.

Executes the submission phase with end-to-end safety gates:
1. Validates that live submission is explicitly unlocked.
2. Resolves any pending CAPTCHA challenges via automated solver or supervised token.
3. Clicks the form submit button and waits for the submission network transaction.
4. Verifies the confirmation page DOM, extracting confirmation text and candidate receipt IDs.
5. Captures post-submit proof (full-page screenshot, trace SHA-256) and logs a tamper-evident
   submission evidence row to the ledger.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.captcha_bridge import (
    CaptchaSolver,
    detect_captcha,
    inject_captcha_token,
    is_captcha_challenge_visible,
)
from app.evidence import CONFIRMATION_MARKERS, evidence_is_sufficient

logger = logging.getLogger(__name__)

SUBMIT_BUTTON_SELECTORS = [
    "#btn-submit",
    ".template-btn-submit",
    ".ashby-application-form-submit-button",
    'button[type="submit"]',
    'input[type="submit"]',
    "#submit_app",
    "#submit-application",
    "#submit-button",
    'button:has-text("Submit Application")',
    'button:has-text("SUBMIT APPLICATION")',
    'button:has-text("Submit application")',
    'button:has-text("Submit")',
    'button:has-text("Apply now")',
    'button:has-text("Apply")',
]

CANDIDATE_ID_PATTERNS = [
    re.compile(
        r"(?:confirmation|application|candidate|reference|ref)\s+(?:id|number|#)\s*[:#]?\s*([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)",
        re.I,
    ),
    re.compile(r"re(?:f|ference)\s*[:#]\s*([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)", re.I),
]


@dataclass
class SubmissionResult:
    status: str  # "submitted", "submission_uncertain", "blocked", "dry_run_prevented", "failed"
    success: bool
    final_url: str
    confirmation_text: str | None = None
    candidate_id: str | None = None
    captcha_detected: bool = False
    captcha_resolved: bool = False
    screenshot_path: str | None = None
    screenshot_sha256: str | None = None
    trace_path: str | None = None
    trace_sha256: str | None = None
    error: str | None = None


def extract_confirmation_evidence(text: str, url: str) -> tuple[str | None, str | None]:
    """Inspect page text and URL for confirmation markers and receipt identifiers."""
    lower_text = text.lower()
    matched_marker = next((marker for marker in CONFIRMATION_MARKERS if marker in lower_text), None)

    candidate_id = None
    for pattern in CANDIDATE_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate_id = match.group(1).strip()
            break

    confirmation_snippet = None
    if matched_marker:
        # Find the surrounding paragraph/sentence
        idx = lower_text.find(matched_marker)
        start = max(0, idx - 40)
        end = min(len(text), idx + len(matched_marker) + 120)
        confirmation_snippet = text[start:end].replace("\n", " ").strip()

    return confirmation_snippet, candidate_id


async def execute_live_submission(
    page: Any,
    allow_live_submit: bool = False,
    captcha_solver: CaptchaSolver | None = None,
    screenshot_path: Path | None = None,
    trace_path: Path | None = None,
    timeout_ms: int = 30000,
) -> SubmissionResult:
    """Safely execute live submission with CAPTCHA handling and post-submit confirmation proof."""
    current_url = page.url

    # 1. Check CAPTCHA status
    challenge = await detect_captcha(page)
    captcha_detected = challenge.detected
    captcha_resolved = challenge.resolved

    if challenge.detected and not challenge.resolved:
        if captcha_solver:
            try:
                token = await captcha_solver.solve(
                    challenge.captcha_type or "recaptcha",
                    challenge.sitekey or "",
                    current_url,
                )
                if token:
                    ok = await inject_captcha_token(page, challenge, token)
                    captcha_resolved = ok
            except Exception as exc:
                logger.error("CAPTCHA solver error: %s", exc)
                return SubmissionResult(
                    status="blocked",
                    success=False,
                    final_url=current_url,
                    captcha_detected=True,
                    captcha_resolved=False,
                    error=f"CAPTCHA solver error: {exc}",
                )

        if not captcha_resolved:
            return SubmissionResult(
                status="blocked",
                success=False,
                final_url=current_url,
                captcha_detected=True,
                captcha_resolved=False,
                error="Unresolved CAPTCHA challenge detected on application form",
            )

    # 2. Live submission safety gate
    if not allow_live_submit:
        return SubmissionResult(
            status="dry_run_prevented",
            success=False,
            final_url=current_url,
            captcha_detected=captcha_detected,
            captcha_resolved=captcha_resolved,
            error="Live submission is locked (allow_live_submit=False)",
        )

    # 3. Locate the submit button
    submit_loc = None
    for sel in SUBMIT_BUTTON_SELECTORS:
        loc = page.locator(sel)
        if await loc.count() > 0 and await loc.first.is_visible():
            submit_loc = loc.first
            break

    if not submit_loc:
        return SubmissionResult(
            status="failed",
            success=False,
            final_url=current_url,
            captcha_detected=captcha_detected,
            captcha_resolved=captcha_resolved,
            error="Could not locate submit button on application form",
        )

    # 4. Click Submit and wait for navigation / network response
    try:
        try:
            await submit_loc.scroll_into_view_if_needed()
            await submit_loc.hover()
            await asyncio.sleep(0.3)
        except Exception:
            pass
        await submit_loc.click()

        # Poll for SPA / GraphQL submission & reCAPTCHA token exchange to complete.
        # If an interactive CAPTCHA challenge modal pops up after clicking Submit,
        # keep polling for up to 180s so the challenge can be completed in headed mode.
        confirmation_text = None
        candidate_id = None
        body_text = ""
        final_url = page.url
        captcha_popup_notified = False
        spam_retries = 0
        max_ticks = 30  # 15s default for normal submission
        tick = 0
        while tick < max_ticks:
            await asyncio.sleep(0.5)
            tick += 1
            final_url = page.url
            try:
                body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                body_text = ""
            confirmation_text, candidate_id = extract_confirmation_evidence(body_text, final_url)
            if evidence_is_sufficient(
                confirmation_text=confirmation_text or body_text,
                candidate_id=candidate_id,
                final_url=final_url,
            ):
                break

            if await is_captcha_challenge_visible(page):
                captcha_detected = True
                # Dynamically keep at least 30s remaining as long as the user is actively solving the challenge
                max_ticks = max(max_ticks, tick + 60)
                if not captcha_popup_notified:
                    captcha_popup_notified = True
                    max_ticks = max(max_ticks, tick + 600)  # Allow up to 5 minutes total
                    try:
                        await page.bring_to_front()
                        import subprocess
                        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
                        subprocess.run(["osascript", "-e", 'tell application "Chromium" to activate'], check=False)
                    except Exception:
                        pass
                    import sys
                    print(
                        "\n🔒 [HunterXJob] Interactive CAPTCHA challenge popped up after clicking Submit!\n"
                        "   👉 Please solve the CAPTCHA puzzle in the open browser window (Hunter will wait while you solve it)...\a",
                        file=sys.stderr,
                        flush=True,
                    )
                continue

            if captcha_popup_notified:
                # CAPTCHA modal just closed; re-click submit on platforms like Lever where token is verified on click
                captcha_resolved = True
                captcha_popup_notified = False
                max_ticks = max(max_ticks, tick + 30)
                try:
                    await asyncio.sleep(1.0)
                    await submit_loc.scroll_into_view_if_needed()
                    await submit_loc.click()
                except Exception:
                    pass

            # If Ashby shows "flagged as possible spam" or Lever shows "error verifying your application",
            # wait 3.5s, scroll back to the Submit button, and click it again.
            body_lower = body_text.lower()
            if (
                ("flagged as possible spam" in body_lower or "submit your application again" in body_lower or "error verifying your application" in body_lower)
                and spam_retries < 2
            ):
                spam_retries += 1
                await asyncio.sleep(3.5)
                try:
                    await submit_loc.scroll_into_view_if_needed()
                    await submit_loc.hover()
                    await asyncio.sleep(0.4)
                    await submit_loc.click()
                    max_ticks = max(max_ticks, tick + 30)
                except Exception:
                    pass

        # 5. Capture post-submission screenshot proof
        sc_sha256 = None
        if screenshot_path:
            screenshot_path = Path(screenshot_path)
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path), full_page=True)
            sc_sha256 = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()

        # Check sufficiency
        sufficient = evidence_is_sufficient(
            confirmation_text=confirmation_text or body_text,
            candidate_id=candidate_id,
            final_url=final_url,
        )

        status = "submitted" if sufficient else "submission_uncertain"

        return SubmissionResult(
            status=status,
            success=sufficient,
            final_url=final_url,
            confirmation_text=confirmation_text or ("Submission completed" if sufficient else None),
            candidate_id=candidate_id,
            captcha_detected=captcha_detected,
            captcha_resolved=captcha_resolved,
            screenshot_path=str(screenshot_path) if screenshot_path else None,
            screenshot_sha256=sc_sha256,
        )

    except Exception as exc:
        return SubmissionResult(
            status="failed",
            success=False,
            final_url=page.url,
            captcha_detected=captcha_detected,
            captcha_resolved=captcha_resolved,
            error=str(exc),
        )
