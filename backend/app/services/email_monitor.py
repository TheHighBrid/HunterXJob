"""IMAP inbox polling to infer application status changes from reply emails.

Scans recent emails for sender/subject keywords matching tracked
applications' company/thread, and infers a status change (interview/
rejection/offer) from a keyword list. Updates the Application row and
returns a summary usable for report logging.
"""
from __future__ import annotations

import email
import imaplib
from dataclasses import dataclass, field
from email.header import decode_header

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Application, ApplicationStatus, JobPosting

_INTERVIEW_KEYWORDS = [
    "interview", "schedule a call", "schedule a chat", "phone screen",
    "next steps", "would like to speak", "chat with you",
]
_OFFER_KEYWORDS = ["offer", "excited to extend", "pleased to offer", "job offer"]
_REJECTION_KEYWORDS = [
    "unfortunately", "not moving forward", "other candidates",
    "decided not to proceed", "not selected", "position has been filled",
    "will not be moving forward",
]


@dataclass
class EmailStatusUpdate:
    application_id: str
    old_status: str
    new_status: str
    subject: str
    sender: str


@dataclass
class EmailCheckResult:
    scanned_count: int = 0
    updates: list[EmailStatusUpdate] = field(default_factory=list)
    error: str | None = None


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded += text.decode(charset or "utf-8", errors="ignore")
        else:
            decoded += text
    return decoded


def _infer_status(text: str) -> str | None:
    lowered = text.lower()
    if any(kw in lowered for kw in _OFFER_KEYWORDS):
        return ApplicationStatus.OFFER.value
    if any(kw in lowered for kw in _REJECTION_KEYWORDS):
        return ApplicationStatus.REJECTED.value
    if any(kw in lowered for kw in _INTERVIEW_KEYWORDS):
        return ApplicationStatus.INTERVIEW.value
    return None


def check_inbox_for_status_updates(
    db: Session, settings: Settings, max_messages: int = 50
) -> EmailCheckResult:
    """Poll the configured IMAP mailbox and update Application rows from replies."""
    if not settings.IMAP_HOST or not settings.IMAP_USERNAME or not settings.IMAP_PASSWORD:
        return EmailCheckResult(error="IMAP is not configured (see .env IMAP_* settings)")

    applications = (
        db.execute(
            select(Application, JobPosting)
            .join(JobPosting, Application.job_posting_id == JobPosting.id)
            .where(
                Application.status.in_(
                    [
                        ApplicationStatus.APPLIED.value,
                        ApplicationStatus.INTERVIEW.value,
                    ]
                )
            )
        )
        .all()
    )
    if not applications:
        return EmailCheckResult()

    company_to_apps: dict[str, list[Application]] = {}
    for app_row, job in applications:
        company_to_apps.setdefault(job.company.strip().lower(), []).append(app_row)

    result = EmailCheckResult()
    try:
        conn = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT)
        try:
            conn.login(settings.IMAP_USERNAME, settings.IMAP_PASSWORD)
            conn.select(settings.IMAP_MAILBOX)

            status, data = conn.search(None, "ALL")
            if status != "OK":
                return EmailCheckResult(error=f"IMAP search failed: {status}")

            message_ids = data[0].split()
            recent_ids = message_ids[-max_messages:] if message_ids else []

            for msg_id in recent_ids:
                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    continue
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                subject = _decode(msg.get("Subject"))
                sender = _decode(msg.get("From"))
                result.scanned_count += 1

                matched_company = None
                for company in company_to_apps:
                    if company and company in sender.lower():
                        matched_company = company
                        break
                    if company and company in subject.lower():
                        matched_company = company
                        break
                if not matched_company:
                    continue

                new_status = _infer_status(subject)
                if new_status is None:
                    body_text = _extract_body(msg)
                    new_status = _infer_status(body_text)
                if new_status is None:
                    continue

                for app_row in company_to_apps[matched_company]:
                    if app_row.status == new_status:
                        continue
                    old_status = app_row.status
                    app_row.status = new_status
                    app_row.thread_email_id = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                    db.add(app_row)
                    result.updates.append(
                        EmailStatusUpdate(
                            application_id=app_row.id,
                            old_status=old_status,
                            new_status=new_status,
                            subject=subject,
                            sender=sender,
                        )
                    )
            db.commit()
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        return EmailCheckResult(error=f"IMAP polling failed: {exc}")

    return result


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(errors="ignore")
    except Exception:
        return ""
