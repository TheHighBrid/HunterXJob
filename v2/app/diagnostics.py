"""Failure Taxonomy and Root-Cause Diagnostic Intelligence.

Provides structured diagnostic classification for HunterXJob execution issues:
- Classifies errors across standardized failure categories.
- Scrubs PII, candidate facts, credentials, and sensitive tokens from diagnostics.
- Identifies whether an issue requires human intervention or is recurring.
- Purely observational: Never triggers automatic retries or mutates state.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FailureCategory(str, Enum):
    ATS_IDENTIFICATION_FAILURE = "ats_identification_failure"
    NAVIGATION_FAILURE = "navigation_failure"
    PAGE_LOADING_TIMEOUT = "page_loading_timeout"
    MISSING_OR_CHANGED_SELECTOR = "missing_or_changed_selector"
    DYNAMIC_FIELD_FAILURE = "dynamic_field_failure"
    VALIDATION_FAILURE = "validation_failure"
    AUTHENTICATION_REQUIREMENT = "authentication_requirement"
    CAPTCHA_CHALLENGE = "captcha_challenge"
    FILE_UPLOAD_FAILURE = "file_upload_failure"
    NETWORK_FAILURE = "network_failure"
    FORM_SUBMISSION_UNCERTAINTY = "form_submission_uncertainty"
    CONFIRMATION_EVIDENCE_UNAVAILABLE = "confirmation_evidence_unavailable"
    UNSUPPORTED_APPLICATION_FEATURE = "unsupported_application_feature"
    UNEXPECTED_PLATFORM_BEHAVIOUR = "unexpected_platform_behaviour"


# PII scrubbing patterns
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_API_KEY_RE = re.compile(r"(?:api[_-]?key|token|secret)[=:\s]+[A-Za-z0-9_\-]{16,}", re.IGNORECASE)


def scrub_pii(text: str) -> str:
    """Scrub candidate PII and credentials from error logs and diagnostic evidence."""
    if not text:
        return ""
    scrubbed = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    scrubbed = _PHONE_RE.sub("[REDACTED_PHONE]", scrubbed)
    scrubbed = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", scrubbed)
    scrubbed = _API_KEY_RE.sub("api_key=[REDACTED_KEY]", scrubbed)
    return scrubbed


@dataclass(frozen=True)
class DiagnosticRecord:
    platform: str
    adapter_version: str
    stage: str
    category: FailureCategory
    summary: str
    scrubbed_detail: str
    http_status: int | None
    human_intervention_required: bool
    outcome_is_uncertain: bool
    timestamp: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


def classify_failure(
    raw_error: str,
    *,
    platform: str = "generic",
    adapter_version: str = "2.0.0",
    stage: str = "unknown",
    http_status: int | None = None,
) -> DiagnosticRecord:
    """Classify a raw error message or exception into the standardized taxonomy."""
    error_lower = (raw_error or "").lower()
    clean_error = scrub_pii(raw_error)

    # 1. CAPTCHA & Bot challenges
    if any(k in error_lower for k in ("captcha", "datadome", "cloudflare", "turnstile", "anti-bot", "challenge")):
        category = FailureCategory.CAPTCHA_CHALLENGE
        summary = "Interactive CAPTCHA or anti-bot challenge detected"
        human_req = True
        uncertain = True

    # 2. Authentication & MFA
    elif any(k in error_lower for k in ("login", "signin", "sign in", "mfa", "2fa", "password", "session expired")):
        category = FailureCategory.AUTHENTICATION_REQUIREMENT
        summary = "Candidate authentication or MFA required by employer portal"
        human_req = True
        uncertain = False

    # 3. Timeouts & Navigation
    elif any(k in error_lower for k in ("timeout", "timed out", "navigation timeout", "wait_for")):
        category = FailureCategory.PAGE_LOADING_TIMEOUT
        summary = "Page loading or element wait exceeded threshold"
        human_req = False
        uncertain = True

    # 4. Network & HTTP status
    elif http_status in {500, 502, 503, 504} or any(k in error_lower for k in ("connection error", "dns", "econnreset", "network")):
        category = FailureCategory.NETWORK_FAILURE
        summary = f"Network or upstream server error (HTTP {http_status or 'N/A'})"
        human_req = False
        uncertain = True

    # 5. Form validation errors
    elif any(k in error_lower for k in ("validation", "invalid value", "required field missing", "not listed option")):
        category = FailureCategory.VALIDATION_FAILURE
        summary = "Form field validation rejected submitted input"
        human_req = True
        uncertain = False

    # 6. Selectors and dynamic fields
    elif any(k in error_lower for k in ("selector", "locator", "not found", "element not visible")):
        category = FailureCategory.MISSING_OR_CHANGED_SELECTOR
        summary = "DOM selector failed to resolve candidate input or button"
        human_req = False
        uncertain = False

    # 7. File uploads
    elif any(k in error_lower for k in ("file", "resume upload", "attachment", "pdf")):
        category = FailureCategory.FILE_UPLOAD_FAILURE
        summary = "File attachment or upload input failed"
        human_req = False
        uncertain = False

    # 8. Confirmation evidence
    elif any(k in error_lower for k in ("confirmation", "uncertain outcome", "receipt")):
        category = FailureCategory.CONFIRMATION_EVIDENCE_UNAVAILABLE
        summary = "Submission confirmation banner or candidate ID could not be verified"
        human_req = True
        uncertain = True

    else:
        category = FailureCategory.UNEXPECTED_PLATFORM_BEHAVIOUR
        summary = "Unexpected error encountered during application processing"
        human_req = False
        uncertain = True

    return DiagnosticRecord(
        platform=platform,
        adapter_version=adapter_version,
        stage=stage,
        category=category,
        summary=summary,
        scrubbed_detail=clean_error[:500],
        http_status=http_status,
        human_intervention_required=human_req,
        outcome_is_uncertain=uncertain,
        timestamp=datetime.now(UTC).isoformat(),
    )
