"""Read-only job views for the phone: list rows and a detail page."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Application, Job, PipelineEvent, ReviewTask, iso_utc
from app.review_actions import task_summary

MAX_PAGE = 500
_FORM_TASK_REASONS = frozenset({"form_fetch_failed", "form_unavailable"})


def _loads(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def job_row(job: Job, open_tasks: int = 0) -> dict[str, Any]:
    application = job.application
    return {
        "id": job.id, "title": job.title, "company": job.company, "location": job.location,
        "remote": bool(job.remote), "stage": job.stage, "eligible": job.eligible, "score": job.final_score,
        "url": job.url, "reason": job.eligibility_reason, "platform": job.platform,
        "discovered_at": iso_utc(job.discovered_at),
        "application_id": application.id if application else None,
        "application_stage": application.stage if application else None,
        "open_review_tasks": open_tasks,
    }


def list_jobs(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    stage: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    query = select(Job)
    if stage:
        query = query.where(Job.stage == stage)
    if q:
        needle = f"%{q.strip()}%"
        query = query.where(or_(Job.title.ilike(needle), Job.company.ilike(needle), Job.location.ilike(needle)))
    query = query.order_by(Job.final_score.desc().nullslast(), Job.discovered_at.desc())
    jobs = list(db.execute(query.offset(max(offset, 0)).limit(max(1, min(limit, MAX_PAGE)))).scalars())
    counts = dict(db.execute(
        select(ReviewTask.job_id, func.count()).where(
            ReviewTask.status == "open", ReviewTask.job_id.in_([job.id for job in jobs])
        ).group_by(ReviewTask.job_id)
    ).all()) if jobs else {}
    return [job_row(job, int(counts.get(job.id, 0))) for job in jobs]


def _decision_report(db: Session, job: Job) -> dict[str, Any] | None:
    """The deterministic scoring report stored with the latest gate event."""
    rows = db.execute(
        select(PipelineEvent.payload_json).where(
            PipelineEvent.job_id == job.id, PipelineEvent.payload_json.is_not(None)
        ).order_by(PipelineEvent.created_at.desc())
    ).scalars()
    for raw in rows:
        payload = _loads(raw)
        if payload and "decision" in payload and "dimensions" in payload:
            return {
                "decision": payload.get("decision"),
                "matched_keywords": payload.get("matched_keywords", []),
                "review_flags": payload.get("review_flags", []),
                "vetoes": payload.get("vetoes", []),
                "dimensions": payload.get("dimensions", []),
            }
    return None


def _form_base(application: Application, validation: dict[str, Any] | None) -> dict[str, Any]:
    form = (validation or {}).get("form") or {}
    return {
        "source": form.get("source"),
        "fields_total": form.get("fields_total"),
        "fields_required": form.get("fields_required"),
        "warnings": form.get("warnings", []),
        "fields_filled": None,
        "blockers": [],
        "blocked_fields": [],
        "last_error": application.last_error,
    }


def _form_outcome(validation: dict[str, Any], open_tasks: list[ReviewTask]) -> dict[str, Any]:
    """State and blockers for an application that has been dry-run at least once."""
    task_reasons = [task.reason_code for task in open_tasks]
    if validation.get("ok"):
        return {"state": "validated", "fields_filled": len(validation.get("fields") or [])}
    if any(reason in _FORM_TASK_REASONS for reason in task_reasons):
        return {"state": "unavailable", "blockers": task_reasons}
    blockers = list(validation.get("blockers") or [])
    if not (blockers or open_tasks):
        return {"state": "in_progress"}
    return {
        "state": "blocked",
        "blockers": blockers or task_reasons,
        "blocked_fields": list(validation.get("blocked_fields") or []),
    }


def form_status(application: Application | None, open_tasks: list[ReviewTask]) -> dict[str, Any]:
    """What the last dry-run learned about the job's application form."""
    if application is None:
        return {"state": "no_application", "blockers": [], "blocked_fields": []}
    validation = _loads(application.validation_json)
    base = _form_base(application, validation)
    if validation is None:
        return {**base, "state": "not_checked"}
    return {**base, **_form_outcome(validation, open_tasks)}


def job_detail(db: Session, job: Job) -> dict[str, Any]:
    tasks = list(db.execute(
        select(ReviewTask).where(ReviewTask.job_id == job.id).order_by(ReviewTask.created_at.desc())
    ).scalars())
    open_tasks = [task for task in tasks if task.status == "open"]
    application = job.application
    events = db.execute(
        select(PipelineEvent).where(PipelineEvent.job_id == job.id).order_by(PipelineEvent.created_at.desc()).limit(50)
    ).scalars()
    return {
        **job_row(job, len(open_tasks)),
        "description": job.description or "",
        "liveness": job.liveness,
        "scores": {
            "final": job.final_score,
            "deterministic": job.deterministic_score,
            "ai": job.ai_score,
        },
        "decision": _decision_report(db, job),
        "application": None if application is None else {
            "id": application.id, "stage": application.stage, "mode": application.mode,
            "attempts": application.attempts, "last_error": application.last_error,
            "adapter": application.adapter_name, "maturity": application.adapter_maturity,
            "has_cover_letter": bool(application.cover_letter_text),
            "updated_at": iso_utc(application.updated_at),
        },
        "form_status": form_status(application, open_tasks),
        "review_tasks": [task_summary(db, task) for task in tasks],
        "events": [{
            "from_stage": event.from_stage, "to_stage": event.to_stage,
            "message": event.message, "created_at": iso_utc(event.created_at),
        } for event in events],
    }
