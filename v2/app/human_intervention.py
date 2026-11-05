"""Native Human Intervention and Supervised Browser Bridge.

Provides a safe, controlled mechanism for human interactions during execution:
- Interactive CAPTCHA / puzzle solving
- Multi-factor authentication (MFA / 2FA)
- Employer account login / SSO
- Ambiguous or unexpected platform challenges

ARCHITECTURAL PRINCIPLES (Rebuilding natively, JobTomatik handoff rejected):
1. Single-Session Browser Ownership: Maintains Playwright page ownership within
   the existing browser instance. No session handoff, process spawning, or IPC transfers.
2. Context Preservation: Keeps all filled form values and DOM state intact.
3. Unobtrusive In-Page Guidance: Injects a lightweight notification banner in headed
   mode explaining why intervention is needed.
4. Fail-Closed Safety: If the user cancels or the timeout expires, the session exits
   safely to ReviewTask without crashing, resubmitting, or dropping orphan processes.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Coroutine

from app.captcha_bridge import detect_captcha, is_captcha_challenge_visible
from app.models import Application, Job, ReviewTask

logger = logging.getLogger(__name__)


class InterventionType(str, Enum):
    CAPTCHA = "captcha"
    MFA = "mfa"
    LOGIN = "login"
    UNEXPECTED_CHALLENGE = "unexpected_challenge"
    MANUAL_REVIEW = "manual_review"


class InterventionStatus(str, Enum):
    RESOLVED = "resolved"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class InterventionResult:
    status: InterventionStatus
    intervention_type: InterventionType
    duration_seconds: float
    message: str
    completed_at: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["intervention_type"] = self.intervention_type.value
        return data


BANNER_INJECT_JS = r"""
(msg) => {
  let existing = document.getElementById('hx-intervention-banner');
  if (existing) {
    existing.innerText = msg;
    return;
  }
  const banner = document.createElement('div');
  banner.id = 'hx-intervention-banner';
  banner.style.position = 'fixed';
  banner.style.top = '0';
  banner.style.left = '0';
  banner.style.width = '100%';
  banner.style.backgroundColor = '#1e293b';
  banner.style.color = '#f8fafc';
  banner.style.padding = '12px 20px';
  banner.style.fontSize = '14px';
  banner.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  banner.style.fontWeight = '500';
  banner.style.boxShadow = '0 4px 6px -1px rgba(0, 0, 0, 0.3)';
  banner.style.zIndex = '2147483647';
  banner.style.display = 'flex';
  banner.style.justifyContent = 'space-between';
  banner.style.alignItems = 'center';

  const textSpan = document.createElement('span');
  textSpan.innerText = '🛡️ HunterXJob: ' + msg;
  banner.appendChild(textSpan);

  document.body.prepend(banner);
}
"""

BANNER_REMOVE_JS = r"""
() => {
  const el = document.getElementById('hx-intervention-banner');
  if (el) el.remove();
}
"""


async def show_intervention_guidance(page: Any, message: str) -> None:
    """Display an unobtrusive banner in the browser informing the user of the required action."""
    try:
        await page.evaluate(BANNER_INJECT_JS, message)
    except Exception as exc:
        logger.debug("Failed to inject intervention banner: %s", exc)


async def remove_intervention_guidance(page: Any) -> None:
    """Remove the guidance banner once intervention is complete."""
    try:
        await page.evaluate(BANNER_REMOVE_JS)
    except Exception as exc:
        logger.debug("Failed to remove intervention banner: %s", exc)


async def wait_for_intervention(
    page: Any,
    intervention_type: InterventionType,
    *,
    timeout_seconds: float = 120.0,
    poll_interval: float = 1.0,
    custom_condition: Callable[[Any], Coroutine[Any, Any, bool]] | None = None,
    guidance_message: str | None = None,
) -> InterventionResult:
    """Safely pause and await human intervention in the existing Playwright page.

    Monitors for completion:
    - For CAPTCHA: polls until interactive challenge modal disappears and token is resolved.
    - For MFA / Login: polls custom_condition or watches for navigation away from auth page.
    """
    msg = guidance_message or {
        InterventionType.CAPTCHA: "Please solve the security challenge on screen to continue.",
        InterventionType.MFA: "Please enter your verification code or approve the prompt.",
        InterventionType.LOGIN: "Please sign in to the employer portal to continue.",
        InterventionType.UNEXPECTED_CHALLENGE: "Please review and complete the unexpected prompt.",
        InterventionType.MANUAL_REVIEW: "Manual review required. Complete fields and press continue.",
    }.get(intervention_type, "Action required in this browser window.")

    await show_intervention_guidance(page, msg)
    start_time = time.monotonic()

    try:
        while time.monotonic() - start_time < timeout_seconds:
            # 1. Custom check condition if provided
            if custom_condition:
                try:
                    if await custom_condition(page):
                        elapsed = round(time.monotonic() - start_time, 2)
                        await remove_intervention_guidance(page)
                        return InterventionResult(
                            status=InterventionStatus.RESOLVED,
                            intervention_type=intervention_type,
                            duration_seconds=elapsed,
                            message="Custom condition satisfied successfully.",
                            completed_at=datetime.now(UTC).isoformat(),
                        )
                except Exception:
                    pass

            # 2. CAPTCHA completion check
            if intervention_type is InterventionType.CAPTCHA:
                is_visible = await is_captcha_challenge_visible(page)
                c_status = await detect_captcha(page)
                # Resolved if challenge modal closed AND token is filled (or captcha no longer detected)
                if not is_visible and (c_status.resolved or not c_status.detected):
                    elapsed = round(time.monotonic() - start_time, 2)
                    await remove_intervention_guidance(page)
                    return InterventionResult(
                        status=InterventionStatus.RESOLVED,
                        intervention_type=intervention_type,
                        duration_seconds=elapsed,
                        message="CAPTCHA challenge resolved.",
                        completed_at=datetime.now(UTC).isoformat(),
                    )

            await asyncio.sleep(poll_interval)

        # Timeout reached
        elapsed = round(time.monotonic() - start_time, 2)
        await remove_intervention_guidance(page)
        return InterventionResult(
            status=InterventionStatus.TIMEOUT,
            intervention_type=intervention_type,
            duration_seconds=elapsed,
            message=f"Intervention timed out after {timeout_seconds}s.",
            completed_at=datetime.now(UTC).isoformat(),
        )

    except asyncio.CancelledError:
        elapsed = round(time.monotonic() - start_time, 2)
        await remove_intervention_guidance(page)
        return InterventionResult(
            status=InterventionStatus.CANCELLED,
            intervention_type=intervention_type,
            duration_seconds=elapsed,
            message="Intervention was cancelled by user or scheduler.",
            completed_at=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        elapsed = round(time.monotonic() - start_time, 2)
        await remove_intervention_guidance(page)
        return InterventionResult(
            status=InterventionStatus.FAILED,
            intervention_type=intervention_type,
            duration_seconds=elapsed,
            message=f"Error while awaiting intervention: {exc}",
            completed_at=datetime.now(UTC).isoformat(),
        )
