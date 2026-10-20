"""Owner actions on review tasks (approve / reject / resolve) for the phone.

Safety rules:

* Approving never runs an apply cycle and never submits. The most it does is
  move work back to ``ready_to_apply``, where the next cycle may *dry-run* it
  (still subject to the kill switch, AUTOMATION_ENABLED and the daily caps).
* Approving is refused while the kill switch is engaged, for excluded
  employers, and for applications that already reached a submission stage.
* Rejecting and resolving are always allowed for open tasks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.flags import kill_switch_engaged
from app.material_store import MaterialsError, material_view, materials_for, materials_ready
from app.material_workflow import REVIEW_REASON, approve_pending
from app.models import Application, Job, PipelineStage, ReviewTask, iso_utc
from app.pipeline import approve_application, shortlist_job, transition
from app.state_machine import can_transition

Resolution = Literal["resolved", "dismissed"]

_SUBMISSION_STAGES = frozenset({
    PipelineStage.submitted.value,
    PipelineStage.confirmed.value,
    PipelineStage.submission_uncertain.value,
})
_PREPARATION_STAGES = frozenset({
    PipelineStage.shortlisted.value,
    PipelineStage.approved.value,
    PipelineStage.materials_generated.value,
    PipelineStage.materials_reviewed.value,
})


class ReviewActionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _job_brief(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": job.id, "title": job.title, "company": job.company, "location": job.location,
        "stage": job.stage, "score": job.final_score, "url": job.url, "platform": job.platform,
    }


def _application_brief(db: Session, application: Application | None) -> dict[str, Any] | None:
    if application is None:
        return None
    return {
        "id": application.id, "stage": application.stage, "mode": application.mode,
        "attempts": application.attempts, "last_error": application.last_error,
        "adapter": application.adapter_name, "maturity": application.adapter_maturity,
        "has_materials": bool(materials_for(db, application.id)),
    }


def _latest_materials(db: Session, application: Application | None) -> list[dict[str, Any]]:
    """Newest version of each kind (what the owner is asked to look at)."""
    if application is None:
        return []
    latest: dict[str, dict[str, Any]] = {}
    for row in materials_for(db, application.id):
        latest.setdefault(row.kind, material_view(row))
    return list(latest.values())


def _task_job(db: Session, task: ReviewTask) -> Job | None:
    if task.job_id:
        return db.get(Job, task.job_id)
    return task.application.job if task.application else None


def _task_application(db: Session, task: ReviewTask, job: Job | None) -> Application | None:
    if task.application is not None:
        return task.application
    if job is not None:
        return db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none()
    return None


def task_summary(db: Session, task: ReviewTask) -> dict[str, Any]:
    job = _task_job(db, task)
    return {
        "id": task.id, "reason_code": task.reason_code, "status": task.status,
        "title": task.title, "detail": task.detail, "url": task.url,
        "application_id": task.application_id, "job_id": task.job_id,
        "job_title": job.title if job else None, "company": job.company if job else None,
        "created_at": iso_utc(task.created_at), "resolved_at": iso_utc(task.resolved_at),
    }


def available_actions(db: Session, task: ReviewTask) -> list[str]:
    if task.status != "open":
        return []
    actions = ["resolve"]
    job = _task_job(db, task)
    if job is not None:
        if can_transition(job.stage, PipelineStage.rejected.value) or can_transition(job.stage, PipelineStage.withdrawn.value):
            actions.insert(0, "reject")
        if _approve_plan(db, task, job) is not None:
            actions.insert(0, "approve")
    return actions


def task_detail(db: Session, task: ReviewTask) -> dict[str, Any]:
    job = _task_job(db, task)
    application = _task_application(db, task, job)
    blocked_fields: list[dict[str, Any]] = []
    if application is not None and application.validation_json:
        try:
            blocked_fields = list(json.loads(application.validation_json).get("blocked_fields") or [])
        except ValueError:
            blocked_fields = []
    return {
        **task_summary(db, task),
        "job": _job_brief(job),
        "application": _application_brief(db, application),
        "materials": _latest_materials(db, application),
        "blocked_fields": blocked_fields,
        "actions": available_actions(db, task),
        "approve_effect": _approve_effect(db, task, job),
        "live_submission_locked": True,
    }


def _approve_plan(db: Session, task: ReviewTask, job: Job) -> str | None:
    """Which approval applies to this task (None = nothing to approve)."""
    if job.stage == PipelineStage.review.value:
        return "shortlist"
    application = _task_application(db, task, job)
    if application is None or application.stage in _SUBMISSION_STAGES or job.stage in _SUBMISSION_STAGES:
        return None
    if job.stage == PipelineStage.needs_review.value and materials_ready(db, application):
        return "requeue"
    if job.stage in _PREPARATION_STAGES:
        return "approve_application"
    return None


_EFFECTS = {
    "shortlist": "Shortlists the job. Materials are prepared later and still need your approval.",
    "requeue": "Puts the application back in the dry-run queue. The next cycle may dry-run it; nothing is submitted.",
    "approve_application": "Approves the application and any pending draft résumé/cover letter (only approved "
                           "versions are ever attached). Nothing is submitted.",
}


def _approve_effect(db: Session, task: ReviewTask, job: Job | None) -> str | None:
    if task.status != "open" or job is None:
        return None
    plan = _approve_plan(db, task, job)
    return _EFFECTS.get(plan) if plan else None


def _open_task(db: Session, task_id: str) -> ReviewTask:
    task = db.get(ReviewTask, task_id)
    if task is None:
        raise ReviewActionError(404, "review task not found")
    if task.status != "open":
        raise ReviewActionError(409, f"review task is already {task.status}")
    return task


def _close(db: Session, task: ReviewTask, status: str) -> None:
    task.status = status
    task.resolved_at = datetime.now(UTC)
    db.add(task)
    db.commit()


def _result(db: Session, task: ReviewTask, action: str) -> dict[str, Any]:
    job = _task_job(db, task)
    application = _task_application(db, task, job)
    return {
        "task": task_summary(db, task),
        "action": action,
        "job_stage": job.stage if job else None,
        "application_stage": application.stage if application else None,
        "submitted": False,
        "live_submission_locked": True,
    }


def approve_task(db: Session, settings: Settings, task_id: str) -> dict[str, Any]:
    task = _open_task(db, task_id)
    if kill_switch_engaged(db):
        raise ReviewActionError(409, "the kill switch is engaged; approvals are paused")
    job = _task_job(db, task)
    if job is None:
        raise ReviewActionError(409, "this task has no job to approve; resolve it instead")
    if job.company.lower() in {name.lower() for name in settings.blacklisted_company_list}:
        raise ReviewActionError(409, "employer is excluded (BLACKLISTED_COMPANIES)")
    plan = _approve_plan(db, task, job)
    if plan is None:
        raise ReviewActionError(409, f"nothing to approve while the job is {job.stage}")

    if plan == "shortlist":
        shortlist_job(db, settings, job, "owner approved after review")
    elif plan == "requeue":
        application = _task_application(db, task, job)
        transition(db, job, PipelineStage.ready_to_apply, "owner approved after review; queued for a dry-run")
        application.stage = PipelineStage.ready_to_apply.value
        db.add(application)
        db.commit()
    else:
        application = _task_application(db, task, job)
        if task.reason_code == REVIEW_REASON:
            try:
                approve_pending(db, application)
            except MaterialsError as exc:
                raise ReviewActionError(exc.status_code, exc.detail) from exc
        approve_application(db, application.id)
    _close(db, task, "approved")
    return _result(db, task, plan)


def reject_task(db: Session, task_id: str) -> dict[str, Any]:
    task = _open_task(db, task_id)
    job = _task_job(db, task)
    action = "closed"
    if job is not None:
        target = next((stage for stage in (PipelineStage.rejected, PipelineStage.withdrawn)
                       if can_transition(job.stage, stage.value)), None)
        if target is None:
            raise ReviewActionError(409, f"a job in {job.stage} cannot be rejected")
        if job.stage != target.value:
            transition(db, job, target, "owner rejected after review")
        application = _task_application(db, task, job)
        if application is not None and application.stage not in _SUBMISSION_STAGES:
            application.stage = target.value
            db.add(application)
            db.commit()
        action = target.value
    _close(db, task, "rejected")
    return _result(db, task, action)


def resolve_task(db: Session, task_id: str, resolution: Resolution = "resolved") -> dict[str, Any]:
    task = _open_task(db, task_id)
    _close(db, task, resolution)
    return _result(db, task, resolution)
