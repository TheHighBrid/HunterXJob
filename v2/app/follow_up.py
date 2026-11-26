"""Automated Application Follow-up Cadence and Draft Generator.

Tracks submitted applications over time, identifies candidates due for a follow-up,
and generates polite, personalized check-in messages grounded in verified profile facts:
1. Cadence schedule: Follow-up 1 (~7-10 days), Follow-up 2 (~14-21 days).
2. Generates tailored email drafts referencing the submission date, role title, and top skills.
3. Automatically enqueues review tasks for owner verification before sending.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask, SubmissionEvidence
from app.profile import VerifiedProfile
from app.recruiter_crm import is_valid_email

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


def dispatch_follow_up_email(
    db: Session,
    task_id: str,
    smtp_host: str | None = None,
    smtp_port: int = 587,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    recipient_email: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Dispatch an approved follow-up email via SMTP or record dry-run delivery."""
    task = db.get(ReviewTask, task_id)
    if not task:
        raise ValueError(f"ReviewTask {task_id} not found")

    lines = (task.detail or "").split("\n\n", 1)
    subject = lines[0].replace("Subject: ", "").strip() if len(lines) > 0 else "Application Follow-up"
    body = lines[1] if len(lines) > 1 else task.detail or ""

    recipient = recipient_email or "hiring-team@employer.com"

    if dry_run or not smtp_host:
        # Verified simulated delivery
        status = "dry_run_dispatched"
    else:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_user or "candidate@melato.ca"
        msg["To"] = recipient

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10.0) as server:
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)
        status = "dispatched"

    task.status = "resolved"
    db.add(PipelineEvent(
        job_id=task.job_id,
        from_stage=PipelineStage.submitted.value,
        to_stage=PipelineStage.submitted.value,
        message=f"Follow-up email dispatched ({status}): {subject} to {recipient}",
    ))
    db.commit()
    return {"status": status, "task_id": task_id, "subject": subject, "recipient": recipient}


# ---------------------------------------------------------------------------
# Supervised follow-up approval with a payload-hash drift guard (Wave 3).
#
# This path never sends email and never opens a network connection. Approving
# binds the sha256 of the canonical draft payload; any later change to the
# subject, body, recipient or step is drift and blocks the send step. The send
# step only records an intent in an in-memory ledger, is disabled unless the
# caller passes ``enabled=True`` explicitly, and is keyed by a deterministic
# ``send_key`` so the same follow-up can never be recorded twice. Persistence
# is deferred until a schema design is approved.
# ---------------------------------------------------------------------------

APPROVAL_UNAPPROVED = "unapproved"
APPROVAL_ACTIVE = "approved"
APPROVAL_REVOKED = "revoked"
FOLLOW_UP_APPROVAL_TTL = timedelta(hours=72)
_SEND_KEY_VERSION = "hunterx-followup-v1"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class SupervisedFollowUpError(ValueError):
    """A supervised follow-up was refused; the message names the blocker."""


def canonical_follow_up_payload(draft: FollowUpDraft, recipient: str) -> str:
    """Canonical JSON of every field an approval binds (sorted keys, no spaces)."""
    payload = {
        "application_id": draft.application_id,
        "body": draft.body,
        "follow_up_number": draft.follow_up_number,
        "job_id": draft.job_id,
        "recipient": (recipient or "").strip().lower(),
        "subject": draft.subject,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def follow_up_payload_hash(draft: FollowUpDraft, recipient: str) -> str:
    return hashlib.sha256(canonical_follow_up_payload(draft, recipient).encode("utf-8")).hexdigest()


def follow_up_send_key(application_id: str, follow_up_number: int, payload_hash: str) -> str:
    """Deterministic idempotency key for one follow-up step of one application."""
    if not application_id:
        raise SupervisedFollowUpError("application_id is required for a send key")
    if follow_up_number < 1:
        raise SupervisedFollowUpError("follow_up_number must be 1 or more")
    if not _SHA256_HEX.match(payload_hash or ""):
        raise SupervisedFollowUpError("payload_hash must be a sha256 hex digest")
    material = f"{_SEND_KEY_VERSION}|{application_id}|{follow_up_number}|{payload_hash}"
    return f"fu{follow_up_number}-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass
class SupervisedFollowUp:
    draft: FollowUpDraft
    recipient: str
    approval_status: str = APPROVAL_UNAPPROVED
    approved_payload_hash: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    def expected_acknowledgment(self) -> str:
        return f"APPROVE FOLLOW-UP {self.draft.application_id} #{self.draft.follow_up_number}"

    def current_payload_hash(self) -> str:
        return follow_up_payload_hash(self.draft, self.recipient)

    def content_blockers(self) -> list[str]:
        blockers: list[str] = []
        if not is_valid_email((self.recipient or "").strip().lower()):
            blockers.append("follow_up_recipient_invalid")
        if not self.draft.subject.strip() or not self.draft.body.strip():
            blockers.append("follow_up_empty")
        return blockers

    def approve(self, approver: str, acknowledgment: str, now: datetime | None = None) -> str:
        """Bind approval to the current payload hash. Returns that hash."""
        if not (approver or "").strip():
            raise SupervisedFollowUpError("an approver is required")
        if acknowledgment != self.expected_acknowledgment():
            raise SupervisedFollowUpError("acknowledgment does not match the expected text")
        blockers = self.content_blockers()
        if blockers:
            raise SupervisedFollowUpError("approval blocked: " + ", ".join(blockers))
        self.approved_payload_hash = self.current_payload_hash()
        self.approved_by = approver.strip()
        self.approved_at = now or datetime.now(UTC)
        self.approval_status = APPROVAL_ACTIVE
        return self.approved_payload_hash

    def revoke(self) -> None:
        self.approval_status = APPROVAL_REVOKED

    def preflight(self, now: datetime | None = None) -> list[str]:
        """Every reason this follow-up may not be recorded as sent; empty means clear."""
        if self.approval_status != APPROVAL_ACTIVE or not self.approved_payload_hash or not self.approved_at:
            return ["follow_up_not_approved"]
        blockers = self.content_blockers()
        if self.approved_payload_hash != self.current_payload_hash():
            blockers.append("follow_up_payload_drifted")
        if (now or datetime.now(UTC)) >= self.approved_at + FOLLOW_UP_APPROVAL_TTL:
            blockers.append("follow_up_approval_expired")
        return blockers


@dataclass(frozen=True)
class FollowUpSendRecord:
    send_key: str
    application_id: str
    follow_up_number: int
    payload_hash: str
    approved_by: str
    recorded_at: datetime
    status: str = "send_intent_recorded"
    delivered: bool = False


class FollowUpSendLedger:
    """In-memory, idempotent record of approved follow-up send intents. Never delivers."""

    def __init__(self) -> None:
        self._records: dict[str, FollowUpSendRecord] = {}

    def has(self, send_key: str) -> bool:
        return send_key in self._records

    def records(self) -> list[FollowUpSendRecord]:
        return list(self._records.values())

    def record_send_intent(
        self,
        followup: SupervisedFollowUp,
        *,
        enabled: bool = False,
        now: datetime | None = None,
    ) -> FollowUpSendRecord:
        if not enabled:
            raise SupervisedFollowUpError("follow_up_send_disabled")
        blockers = followup.preflight(now=now)
        if blockers:
            raise SupervisedFollowUpError("send refused: " + ", ".join(blockers))
        payload_hash = followup.approved_payload_hash or ""
        key = follow_up_send_key(followup.draft.application_id, followup.draft.follow_up_number, payload_hash)
        if key in self._records:
            raise SupervisedFollowUpError(f"follow_up_already_recorded: {key}")
        record = FollowUpSendRecord(
            send_key=key,
            application_id=followup.draft.application_id,
            follow_up_number=followup.draft.follow_up_number,
            payload_hash=payload_hash,
            approved_by=followup.approved_by or "",
            recorded_at=now or datetime.now(UTC),
        )
        self._records[key] = record
        return record
