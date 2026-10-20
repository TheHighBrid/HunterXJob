"""Automated Application Follow-up Cadence and Draft Generator.

Tracks submitted applications over time, identifies candidates due for a follow-up,
and generates polite, personalized check-in messages grounded in verified profile facts:
1. Cadence schedule: Follow-up 1 (~7-10 days), Follow-up 2 (~14-21 days).
2. Generates tailored email drafts referencing the submission date, role title, and top skills.
3. Automatically enqueues review tasks for owner verification before sending.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.materials import JobContext, skill_names
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
            SubmissionEvidence.sufficient == True,
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
