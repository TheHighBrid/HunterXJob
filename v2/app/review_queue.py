from __future__ import annotations

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
    "form_fetch_failed",
    "form_unavailable",
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

