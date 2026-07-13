"""Application-submission adapter interface.

CRITICAL correctness rule: an adapter must NEVER return status="submitted"
unless the submission was actually confirmed (e.g. a confirmation page/text
was observed, or an email send succeeded). When in doubt, return
"needs_review" or "failed" instead of guessing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

AutomationStatus = Literal["submitted", "blocked", "needs_review", "failed"]


@dataclass
class AutomationResult:
    status: AutomationStatus
    detail: str = ""
    evidence: dict[str, str] = field(default_factory=dict)


class ApplicationAdapter(ABC):
    #: Whether this adapter is allowed to run. Roadmap adapters (LinkedIn,
    #: Indeed, Upwork) default this to False.
    enabled: bool = True

    @abstractmethod
    def submit(
        self,
        application,
        job_posting,
        resume_pdf_path: str,
        cover_letter: str,
        contact_email: str | None = None,
    ) -> AutomationResult:
        """Attempt to submit `application`. Never return "submitted" unless confirmed."""
        raise NotImplementedError
