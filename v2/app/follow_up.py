"""Automated Application Follow-up Cadence and Draft Generator.

Tracks submitted applications over time, identifies candidates due for a follow-up,
and generates polite, personalized check-in messages grounded in verified profile facts:
1. Cadence schedule: Follow-up 1 (~7-10 days), Follow-up 2 (~14-21 days).
2. Generates tailored email drafts referencing the submission date, role title, and top skills.
3. Automatically enqueues review tasks for owner verification before sending.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask, SubmissionEvidence
from app.profile import VerifiedProfile

logger = logging.getLogger(__name__)

FIRST_FOLLOW_UP_DAYS = 7
SECOND_FOLLOW_UP_DAYS = 14
MAX_FOLLOW_UPS = 2


@dataclass
class FollowUpDraft:
    application_id: str
    job_id: str
    company: str
    title: str
    follow_up_number: int
    subject: str
    body: str


def generate_follow_up_draft(
    profile: VerifiedProfile,
    job: Job,
    application: Application,
    follow_up_number: int = 1,
) -> FollowUpDraft:
    """Generate a polite, tailored follow-up check-in note."""
    candidate_name = profile.full_name or profile.contact.get("first_name", "Applicant")
    email = profile.contact.get("email", "")
    phone = profile.contact.get("phone", "")
    company = job.company or "the recruiting team"
    title = job.title or "the position"

    # Top skills from profile
    skills = [f.data.get("name") for f in profile.of("skill")[:3] if f.data.get("name")]
    skills_phrase = f" with strong background in {', '.join(skills)}" if skills else ""

    subject = f"Following up on application for {title} - {candidate_name}"

    if follow_up_number == 1:
        body = (
            f"Dear {company} Hiring Team,\n\n"
            f"I hope you are having a productive week. I am following up on my application for the {title} role "
            f"that I submitted recently.\n\n"
            f"I remain very enthusiastic about the opportunity to contribute to {company}{skills_phrase}. "
            f"Please let me know if there are any additional materials, references, or details I can provide to assist your evaluation.\n\n"
            f"Thank you for your time and consideration, and I look forward to hearing from you.\n\n"
            f"Sincerely,\n"
            f"{candidate_name}\n"
            f"{email}\n"
            f"{phone}"
        )
    else:
        body = (
            f"Dear {company} Hiring Team,\n\n"
            f"I wanted to briefly check in regarding my application for the {title} position.\n\n"
            f"I understand you are likely reviewing many qualified applicants. I remain very interested in the team's work "
            f"at {company} and would welcome the opportunity to discuss how my experience can support your goals.\n\n"
            f"Best regards,\n"
            f"{candidate_name}\n"
            f"{email}\n"
            f"{phone}"
        )

    return FollowUpDraft(
        application_id=application.id,
        job_id=job.id,
        company=company,
        title=title,
        follow_up_number=follow_up_number,
        subject=subject,
        body=body.strip(),
    )


def count_previous_follow_ups(db: Session, job_id: str) -> int:
    """Count how many follow-up events have already been generated for this job."""
    events = db.execute(select(PipelineEvent).where(
        PipelineEvent.job_id == job_id,
        PipelineEvent.message.like("Scheduled follow-up #%"),
    )).scalars().all()
    return len(events)


def get_due_follow_ups(
    db: Session,
    as_of: datetime | None = None,
) -> list[tuple[Application, Job, int]]:
    """Identify applications that are due for follow-up 1 or 2."""
    now = as_of or datetime.now(UTC)

    # Query submitted applications that are not rejected or closed
    stmt = (
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(
            Application.stage == PipelineStage.submitted.value,
            Job.stage != PipelineStage.rejected.value,
            Job.stage != PipelineStage.closed.value,
        )
    )
    rows = db.execute(stmt).all()
    due_items: list[tuple[Application, Job, int]] = []

    for app, job in rows:
        # Find submission timestamp
        sub_evidence = db.execute(select(SubmissionEvidence).where(
            SubmissionEvidence.application_id == app.id,
            SubmissionEvidence.kind == "submission",
            SubmissionEvidence.sufficient.is_(True),
        ).order_by(SubmissionEvidence.created_at.desc())).first()

        if not sub_evidence or not sub_evidence[0].created_at:
            continue

        submitted_at = sub_evidence[0].created_at
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=UTC)

        days_elapsed = (now - submitted_at).days
        prev_count = count_previous_follow_ups(db, job.id)

        # Check cadence
        if prev_count == 0 and days_elapsed >= FIRST_FOLLOW_UP_DAYS:
            due_items.append((app, job, 1))
        elif prev_count == 1 and days_elapsed >= SECOND_FOLLOW_UP_DAYS:
            due_items.append((app, job, 2))

    return due_items


def process_due_follow_ups(
    db: Session,
    profile: VerifiedProfile,
    as_of: datetime | None = None,
) -> list[ReviewTask]:
    """Scan for due follow-ups, generate drafts, and queue review tasks."""
    due = get_due_follow_ups(db, as_of=as_of)
    created_tasks: list[ReviewTask] = []

    for app, job, follow_up_num in due:
        draft = generate_follow_up_draft(profile, job, app, follow_up_number=follow_up_num)

        task = ReviewTask(
            application_id=app.id,
            job_id=job.id,
            reason_code=f"follow_up_{follow_up_num}_due",
            title=f"Follow-up #{follow_up_num} Due: {job.company} - {job.title}",
            detail=f"Subject: {draft.subject}\n\n{draft.body}",
        )
        db.add(task)
        db.add(PipelineEvent(
            job_id=job.id,
            from_stage=job.stage,
            to_stage=job.stage,
            message=f"Scheduled follow-up #{follow_up_num} review task ({draft.subject})",
        ))
        created_tasks.append(task)

    db.commit()
    return created_tasks


def schedule_follow_up(db: Session, job_id: str) -> None:
    """Log an event or marker for follow-up tracking."""
    db.add(PipelineEvent(
        job_id=job_id,
        from_stage=PipelineStage.submitted.value,
        to_stage=PipelineStage.submitted.value,
        message="Follow-up cadence scheduled (Day 7 / Day 14 checkpoints)",
    ))
    db.commit()


# ---------------------------------------------------------------------------
# Fail-closed dispatch. ReviewTask has no column for an approval hash, so an
# approval is recorded as a trailer line on ReviewTask.detail:
#   "\n\nApproved-Payload-SHA256: <hex>"
# The hash is sha256 over canonical JSON of (subject, body, recipient). Any edit
# to the draft or a different recipient after approval no longer matches, and
# nothing is sent. No trailer, or a status other than "approved", means refuse.
# ---------------------------------------------------------------------------

APPROVAL_TRAILER = "Approved-Payload-SHA256: "
_TRAILER_RE = re.compile(r"\n\nApproved-Payload-SHA256: ([0-9a-f]{64})\s*$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_PLACEHOLDER_DOMAINS = frozenset({"example.com", "example.org", "example.net", "employer.com", "localhost"})
_PLACEHOLDER_TLDS = (".example", ".invalid", ".test", ".localhost")
_NOREPLY_RE = re.compile(r"^(?:no-?reply|do-?not-?reply|donotreply)(?:[+._-].*)?$")


class FollowUpRefused(ValueError):
    """A follow-up send was refused; ``code`` names the blocker, ``http_status`` the API status."""

    def __init__(self, code: str, http_status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _split_detail(detail: str | None) -> tuple[str, str, str | None]:
    """Return (subject, body, approved_hash) from a follow-up ReviewTask.detail."""
    text = detail or ""
    match = _TRAILER_RE.search(text)
    approved = match.group(1) if match else None
    if match:
        text = text[:match.start()]
    parts = text.split("\n\n", 1)
    subject = parts[0].replace("Subject: ", "", 1).strip()
    body = parts[1] if len(parts) > 1 else ""
    return subject, body, approved


def follow_up_payload_hash(subject: str, body: str, recipient: str) -> str:
    canonical = json.dumps(
        {"body": body, "recipient": recipient.strip().lower(), "subject": subject},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def recipient_problem(recipient: str | None) -> str | None:
    """Why this address may not receive a follow-up (None = acceptable)."""
    address = (recipient or "").strip().lower()
    if not address:
        return "recipient_missing"
    if not _EMAIL_RE.match(address) or ".." in address:
        return "recipient_invalid"
    local, domain = address.rsplit("@", 1)
    if domain in _PLACEHOLDER_DOMAINS or domain.endswith(_PLACEHOLDER_TLDS) or _NOREPLY_RE.match(local):
        return "recipient_placeholder"
    if any(domain.endswith("." + d) for d in _PLACEHOLDER_DOMAINS):
        return "recipient_placeholder"
    return None


def _check_recipient(recipient: str | None) -> None:
    problem = recipient_problem(recipient)
    if problem:
        raise FollowUpRefused(problem, 422)


def _check_draft(task: ReviewTask, subject: str, body: str) -> None:
    if not (task.reason_code or "").startswith("follow_up_"):
        raise FollowUpRefused("not_a_follow_up_task")
    if not subject or not body.strip() or "\r" in subject or "\n" in subject:
        raise FollowUpRefused("draft_invalid", 422)


def _check_approval(task: ReviewTask, approved_hash: str | None, current_hash: str) -> None:
    if task.status != "approved":
        raise FollowUpRefused("follow_up_not_approved")
    if not approved_hash:
        raise FollowUpRefused("approval_hash_missing")
    if not hmac.compare_digest(approved_hash, current_hash):
        raise FollowUpRefused("payload_changed_since_approval")


def approve_follow_up_task(db: Session, task_id: str, recipient_email: str) -> str:
    """Approve an open follow-up draft for one recipient; binds and returns the payload hash."""
    task = db.get(ReviewTask, task_id)
    if not task:
        raise ValueError(f"ReviewTask {task_id} not found")
    if task.status != "open":
        raise FollowUpRefused(f"review task is already {task.status}")
    subject, body, _ = _split_detail(task.detail)
    _check_draft(task, subject, body)
    _check_recipient(recipient_email)
    payload_hash = follow_up_payload_hash(subject, body, recipient_email)
    task.detail = f"Subject: {subject}\n\n{body}\n\n{APPROVAL_TRAILER}{payload_hash}"
    task.status = "approved"
    db.commit()
    return payload_hash


def _smtp_send(smtp: dict[str, Any], subject: str, body: str, recipient: str) -> None:
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp["user"] or "candidate@melato.ca"
    msg["To"] = recipient
    with smtplib.SMTP(smtp["host"], smtp["port"], timeout=10.0) as server:
        server.starttls()
        if smtp["user"] and smtp["password"]:
            server.login(smtp["user"], smtp["password"])
        server.send_message(msg)


def dispatch_follow_up_email(
    db: Session,
    task_id: str,
    smtp_host: str | None = None,
    smtp_port: int = 587,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    recipient_email: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Send an approved follow-up, fail closed.

    Refuses (status "refused", nothing sent, task untouched) unless the task is
    an approved follow-up whose approved payload hash matches the current draft
    and recipient, and the recipient is a real address. ``dry_run`` defaults to
    True and only reports that the send would be allowed. The task is resolved
    only after an SMTP send has actually succeeded.
    """
    task = db.get(ReviewTask, task_id)
    if not task:
        raise ValueError(f"ReviewTask {task_id} not found")
    subject, body, approved_hash = _split_detail(task.detail)
    recipient = (recipient_email or "").strip()
    result: dict[str, Any] = {"task_id": task_id, "subject": subject, "recipient": recipient or None, "sent": False}
    try:
        _check_draft(task, subject, body)
        _check_recipient(recipient)
        _check_approval(task, approved_hash, follow_up_payload_hash(subject, body, recipient))
        if not dry_run and not smtp_host:
            raise FollowUpRefused("smtp_not_configured")
    except FollowUpRefused as refusal:
        # A refused address is never echoed back as if it were the recipient.
        return {**result, "recipient": None, "status": "refused", "reason": refusal.code,
                "http_status": refusal.http_status}
    if dry_run:
        return {**result, "status": "dry_run"}

    _smtp_send({"host": smtp_host, "port": smtp_port, "user": smtp_user, "password": smtp_password},
               subject, body, recipient)
    task.status = "resolved"
    task.resolved_at = datetime.now(UTC)
    db.add(PipelineEvent(
        job_id=task.job_id,
        from_stage=PipelineStage.submitted.value,
        to_stage=PipelineStage.submitted.value,
        message=f"Follow-up email sent via SMTP: {subject} to {recipient}",
    ))
    db.commit()
    return {**result, "status": "dispatched", "sent": True}
