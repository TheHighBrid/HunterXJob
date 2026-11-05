"""Privacy-preserving telemetry and secret scrubber.

Implements Microsoft Presidio scrub semantics with a high-performance compiled
regex engine for zero-dependency CPU operation:
- Secret tokens: sk-..., Bearer ..., API keys, auth passwords
- Sensitive PII: SSN / SIN, credit cards, full phone numbers, email addresses
- Preserves domain semantics: company names, role titles, experience counts, skill keywords
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# Compiled regex patterns for secret tokens and high-risk PII
SECRET_PATTERNS = [
    # OpenAI, Anthropic, Gemini, AWS, GitHub tokens
    (re.compile(r"\b(sk-[a-zA-Z0-9_-]{20,})\b"), "<REDACTED_API_KEY>"),
    (re.compile(r"\b(Bearer\s+[a-zA-Z0-9._~+/=-]{20,})\b", re.I), "<REDACTED_BEARER_TOKEN>"),
    (re.compile(r"\b(ghp_[a-zA-Z0-9]{36,})\b"), "<REDACTED_GITHUB_TOKEN>"),
    (re.compile(r"\b(AIzaSy[a-zA-Z0-9_-]{33})\b"), "<REDACTED_GEMINI_KEY>"),
    (re.compile(r"(?i)(password|secret|passwd|token)\s*[:=]\s*['\"]?([^\s'\"]{6,})['\"]?"), r"\1=<REDACTED_SECRET>"),
]

PII_PATTERNS = [
    # Credit Cards (13-16 digits with optional dashes/spaces)
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"), "<REDACTED_CREDIT_CARD>"),
    # US SSN / Canadian SIN (3-3-3 or 3-2-4 digits)
    (re.compile(r"\b\d{3}[-\s]?\d{3}[-\s]?\d{3}\b"), "<REDACTED_SSN_SIN>"),
    (re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"), "<REDACTED_SSN_SIN>"),
]

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_PATTERN = re.compile(r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


class TraceRedactor:
    """Singleton PII and secret scrubbing engine."""

    _instance: TraceRedactor | None = None

    def __new__(cls) -> TraceRedactor:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_engine()
        return cls._instance

    def _init_engine(self) -> None:
        self.presidio_available = False
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine

            self.analyzer = AnalyzerEngine()
            self.anonymizer = AnonymizerEngine()
            self.presidio_available = True
        except Exception:
            # Fall back to high-performance regex scrubber
            self.presidio_available = False

    def scrub_text(self, text: str, hash_emails: bool = True) -> str:
        """Scrub secrets, payment info, and PII from a text string."""
        if not text or len(str(text).strip()) < 4:
            return str(text or "")

        cleaned = str(text)

        # 1. Scrub zero-tolerance secret tokens first
        for pattern, replacement in SECRET_PATTERNS:
            cleaned = pattern.sub(replacement, cleaned)

        # 2. Scrub financial & government IDs
        for pattern, replacement in PII_PATTERNS:
            cleaned = pattern.sub(replacement, cleaned)

        # 3. Scrub or hash email addresses
        if hash_emails:
            def _hash_email(m: re.Match) -> str:
                email = m.group(0).lower()
                digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
                return f"<EMAIL_HASH_{digest}>"

            cleaned = EMAIL_PATTERN.sub(_hash_email, cleaned)
        else:
            cleaned = EMAIL_PATTERN.sub("<REDACTED_EMAIL>", cleaned)

        # 4. Mask phone numbers
        def _mask_phone(m: re.Match) -> str:
            raw = m.group(0)
            digits = re.sub(r"\D", "", raw)
            last4 = digits[-4:] if len(digits) >= 4 else "0000"
            return f"***-***-{last4}"

        cleaned = PHONE_PATTERN.sub(_mask_phone, cleaned)

        return cleaned

    def scrub_object(self, obj: Any) -> Any:
        """Recursively scrub strings within JSON-compatible data structures (dicts, lists, primitives)."""
        if isinstance(obj, str):
            return self.scrub_text(obj)
        elif isinstance(obj, dict):
            return {self.scrub_text(str(k)): self.scrub_object(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self.scrub_object(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self.scrub_object(item) for item in obj)
        return obj
