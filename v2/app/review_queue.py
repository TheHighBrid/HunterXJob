from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Job, PipelineStage, ReviewTask


REASON_CODES = frozenset({
    "captcha_detected",
    "anti_bot_challenge",
    "mfa_required",
    "assessment_required",
    "legal_answer_missing",
    "sensitive_answer_missing",
    "ambiguous_question",
    "unsupported_control",
    "login_required",
    "submission_confirmation_uncertain",
    "decision_review",
    "liveness_review",
})


def open_task(
    db: Session,
    *,
    reason_code: str,
    title: str,
    detail: str = "",
    application: Application | None = None,
    job: Job | None = None,
    url: str | None = None,
) -> ReviewTask:
    if reason_code not in REASON_CODES:
        reason_code = "ambiguous_question"
    task = ReviewTask(
        application_id=application.id if application else None,
        job_id=job.id if job else (application.job_id if application else None),
        reason_code=reason_code,
        title=title,
        detail=detail,
        url=url,
        status="open",
    )
    db.add(task)
    if application is not None:
        application.stage = PipelineStage.needs_review.value
        db.add(application)
    if job is not None and job.stage not in {
        PipelineStage.submitted.value,
        PipelineStage.confirmed.value,
        PipelineStage.rejected.value,
    }:
        if job.stage != PipelineStage.review.value:
            job.stage = PipelineStage.needs_review.value
        db.add(job)
    db.commit()
    db.refresh(task)
    return task


def list_open(db: Session) -> list[ReviewTask]:
    return list(db.execute(select(ReviewTask).where(ReviewTask.status == "open").order_by(ReviewTask.created_at.desc())).scalars())


def resolve_task(db: Session, task_id: str, resolution: str = "resolved") -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if task is None:
        raise ValueError("review task not found")
    task.status = resolution
    task.resolved_at = datetime.now(timezone.utc)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
