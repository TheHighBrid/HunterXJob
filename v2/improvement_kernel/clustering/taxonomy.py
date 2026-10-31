"""Controlled Failure Taxonomy and Standardization.

Standard categories:
- MODEL: hallucination, unsupported_claim, bad_format, incomplete_answer
- TOOL: wrong_tool, invalid_arguments, tool_timeout, selector_not_found, bad_tool_result
- WORKFLOW: wrong_route, unnecessary_handoff, missing_verification, state_corruption
- DATA: missing_input, stale_memory, bad_extraction, unmatched_field
- PRODUCT: failed_submission, submission_uncertain, captcha_blocked, rate_limited
- SAFETY: unverified_credential, privacy_violation, unauthorized_action
"""
from __future__ import annotations

import re


class FailureCategory:
    MODEL = "MODEL"
    TOOL = "TOOL"
    WORKFLOW = "WORKFLOW"
    DATA = "DATA"
    PRODUCT = "PRODUCT"
    SAFETY = "SAFETY"


FAILURE_TAXONOMY_RULES = [
    # SAFETY
    (re.compile(r"unverified.*(?:credential|cpa|cfa|clearance|designation)", re.I), FailureCategory.SAFETY, "SAFETY_UNVERIFIED_CREDENTIAL"),
    (re.compile(r"secret.*detected|privacy.*violation", re.I), FailureCategory.SAFETY, "SAFETY_PRIVACY_VIOLATION"),
    (re.compile(r"dry.*run.*submi", re.I), FailureCategory.SAFETY, "SAFETY_DRY_RUN_VIOLATION"),

    # TOOL
    (re.compile(r"selector.*(?:not.*found|failed)|could not find.*element", re.I), FailureCategory.TOOL, "TOOL_SELECTOR_NOT_FOUND"),
    (re.compile(r"timeout|timed out|navigation timeout", re.I), FailureCategory.TOOL, "TOOL_TIMEOUT"),
    (re.compile(r"invalid.*argument|schema.*validation", re.I), FailureCategory.TOOL, "TOOL_INVALID_ARGUMENTS"),

    # DATA
    (re.compile(r"unmatched.*field|missing.*required.*field", re.I), FailureCategory.DATA, "DATA_UNMATCHED_FIELD"),
    (re.compile(r"country.*mismatch|california.*instead of canada", re.I), FailureCategory.DATA, "DATA_BAD_LOCATION"),
    (re.compile(r"bad.*extraction|failed to parse form", re.I), FailureCategory.DATA, "DATA_BAD_EXTRACTION"),

    # PRODUCT
    (re.compile(r"captcha.*detected|blocked.*by.*bot", re.I), FailureCategory.PRODUCT, "PRODUCT_CAPTCHA_BLOCKED"),
    (re.compile(r"submission.*uncertain|no confirmation", re.I), FailureCategory.PRODUCT, "PRODUCT_SUBMISSION_UNCERTAIN"),
    (re.compile(r"rate.*limit|429|anti-bot", re.I), FailureCategory.PRODUCT, "PRODUCT_RATE_LIMITED"),

    # FIDELIS & VISION ARTIFACTS
    (re.compile(r"plastic.*skin|dollification|waxy.*face", re.I), FailureCategory.MODEL, "MODEL_PLASTIC_SKIN"),
    (re.compile(r"ringing|halo|oversharpen|gibbs", re.I), FailureCategory.MODEL, "MODEL_RINGING_HALO"),
    (re.compile(r"mutated.*text|warped.*glyph|pseudo.*alpha", re.I), FailureCategory.MODEL, "MODEL_MUTATED_TEXT"),
    (re.compile(r"color.*drift|cielab|delta.*e", re.I), FailureCategory.MODEL, "MODEL_COLOR_DRIFT"),
    (re.compile(r"black.*tile|dead.*tile|zero.*pixel.*tile", re.I), FailureCategory.TOOL, "TOOL_BLACK_TILE"),
    (re.compile(r"vram|out of memory|oom|gpu.*memory", re.I), FailureCategory.TOOL, "TOOL_VRAM_EXHAUSTED"),
    (re.compile(r"dimension.*mismatch|resolution.*mismatch", re.I), FailureCategory.DATA, "DATA_DIMENSION_MISMATCH"),
    (re.compile(r"exif.*corrupt|exif.*drop", re.I), FailureCategory.DATA, "DATA_EXIF_CORRUPTED"),

    # MODEL
    (re.compile(r"hallucination|unsupported|false assertion", re.I), FailureCategory.MODEL, "MODEL_UNSUPPORTED_CLAIM"),
    (re.compile(r"bad.*format|json.*decode|malformed output", re.I), FailureCategory.MODEL, "MODEL_BAD_FORMAT"),
]


def classify_failure(error_text: str | None, default_category: str = FailureCategory.TOOL) -> tuple[str, str]:
    """Classify an error message into a standardized (category, failure_code) tuple."""
    if not error_text:
        return default_category, f"{default_category}_UNKNOWN_DEFECT"

    text = str(error_text)
    for pattern, cat, code in FAILURE_TAXONOMY_RULES:
        if pattern.search(text):
            return cat, code

    return default_category, f"{default_category}_GENERIC_ERROR"
