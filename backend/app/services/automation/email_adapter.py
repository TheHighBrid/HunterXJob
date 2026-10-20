"""Composes and sends direct-email applications via SMTP.

The environment-level ALLOW_LIVE_SUBMISSION gate cannot be overridden by the
mobile app. While it is false, this adapter prepares no outbound connection and
returns needs_review.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.config import Settings
from app.services.automation.base import ApplicationAdapter, AutomationResult


class EmailAdapter(ApplicationAdapter):
    enabled = True

    def __init__(self, settings: Settings, dry_run: bool | None = None):
        self.settings = settings
        self.dry_run = settings.AUTOMATION_DRY_RUN if dry_run is None else dry_run

    def submit(
        self,
        application,
        job_posting,
        resume_pdf_path: str,
        cover_letter: str,
        contact_email: str | None = None,
    ) -> AutomationResult:
        if not contact_email:
            return AutomationResult(
                "needs_review", "no contact email available for this job posting"
            )

        s = self.settings
        if self.dry_run or not s.ALLOW_LIVE_SUBMISSION:
            return AutomationResult(
                "needs_review",
                "dry-run mode: application email prepared but not sent",
            )

        if not s.SMTP_HOST or not s.SMTP_USERNAME or not s.SMTP_PASSWORD:
            return AutomationResult("failed", "SMTP is not configured (see .env SMTP_* settings)")

        title = getattr(job_posting, "title", "the role")
        company = getattr(job_posting, "company", "")

        msg = EmailMessage()
        msg["Subject"] = f"Application for {title}" + (f" at {company}" if company else "")
        msg["From"] = s.SMTP_FROM_ADDRESS or s.SMTP_USERNAME
        msg["To"] = contact_email
        msg.set_content(cover_letter or "Please find my application attached.")

        resume_path = Path(resume_pdf_path)
        if resume_path.exists():
            msg.add_attachment(
                resume_path.read_bytes(),
                maintype="application",
                subtype="pdf",
                filename=resume_path.name or "resume.pdf",
            )

        try:
            if s.SMTP_USE_TLS:
                with smtplib.SMTP(s.SMTP_HOST, s.SMTP_PORT, timeout=30) as server:
                    server.starttls()
                    server.login(s.SMTP_USERNAME, s.SMTP_PASSWORD)
                    server.send_message(msg)
            else:
                with smtplib.SMTP_SSL(s.SMTP_HOST, s.SMTP_PORT, timeout=30) as server:
                    server.login(s.SMTP_USERNAME, s.SMTP_PASSWORD)
                    server.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            return AutomationResult("failed", f"SMTP send failed: {exc}")

        return AutomationResult("submitted", f"emailed to {contact_email}")
