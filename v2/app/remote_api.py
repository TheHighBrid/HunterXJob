"""Phone remote-control API (all routes need the API key).

Safety invariants (covered by tests/test_remote_api.py):

* No route can enable live submission: ``allow_live_submission`` and
  ``unattended_mode`` can only be turned off, settings outside the editable
  subset are refused, and nothing here calls a submit path.
* Approving a review task never runs an apply cycle; it can at most queue the
  application for the next *dry-run*.
* The kill switch can always be engaged; disengaging needs ``confirm=true``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.api_schemas import (
    AuthCheckOut,
    BackupsOut,
    CycleOut,
    FlagIn,
    FlagOut,
    FormPreviewOut,
    JobDetailOut,
    JobOut,
    KillSwitchIn,
    KillSwitchOut,
    NoteIn,
    ReviewActionOut,
    ReviewResolveIn,
    ReviewTaskDetailOut,
    ReviewTaskOut,
    RunCycleOut,
    SchedulerStatusOut,
    SettingsOut,
    SummaryOut,
)
from app.backup import latest_backup_time, list_backups
from app.config import Settings, get_settings
from app.cycle import CycleRunner, recent_cycles
from app.db import get_db
from app.dedup import unlink
from app.flags import KILL_SWITCH, FlagChangeRefused, check_api_flag_change, set_flag, snapshot
from app.greenhouse_form import FormFetchError
from app.job_liveness import CheckContext, check_job
from app.job_views import job_detail, list_jobs
from app.models import FeatureFlag, Job, ReviewTask, iso_utc
from app.pipeline import preview_form
from app.real_forms import LiveFormProvider
from app.reports import summary
from app.review_actions import ReviewActionError, approve_task, reject_task, resolve_task, task_detail, task_summary
from app.runtime import get_runner
from app.runtime_settings import SettingsPatch, overridden_keys, save_patch, settings_view
from app.security import auth_posture, require_api_key

router = APIRouter(dependencies=[Depends(require_api_key)])

DbSession = Annotated[Session, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]
Runner = Annotated[CycleRunner, Depends(get_runner)]


def _refused(exc: FlagChangeRefused | ReviewActionError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


# ------------------------------------------------------------------ auth/settings


@router.get("/api/auth/check", response_model=AuthCheckOut, tags=["remote"])
def auth_check(current: CurrentSettings) -> dict[str, object]:
    """Succeeds only with a valid API key (used by the app's connection test)."""
    return {"ok": True, "version": __version__, "auth": auth_posture(current).mode}


@router.get("/api/settings", response_model=SettingsOut, tags=["remote"])
def get_remote_settings(db: DbSession, current: CurrentSettings) -> dict[str, object]:
    return settings_view(current, overridden_keys(db))


@router.patch("/api/settings", response_model=SettingsOut, tags=["remote"])
def patch_remote_settings(patch: SettingsPatch, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Change the safe subset (caps, quiet hours, targeting, automation on/off).

    Unknown keys (including ``allow_live_submission``) are rejected with 422.
    """
    save_patch(db, current, patch)
    return settings_view(current, overridden_keys(db))


@router.get("/api/reports/summary", response_model=SummaryOut, tags=["remote"])
def reports_summary(db: DbSession, current: CurrentSettings) -> dict[str, object]:
    return summary(db, current)


# -------------------------------------------------------------------------- jobs


@router.get("/api/jobs", response_model=list[JobOut], tags=["remote"])
def jobs(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    stage: str | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[dict[str, object]]:
    return list_jobs(db, limit=limit, offset=offset, stage=stage, q=q)


@router.get("/api/jobs/{job_id}", response_model=JobDetailOut, tags=["remote"])
def job(job_id: str, db: DbSession) -> dict[str, object]:
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    return job_detail(db, row)


@router.post("/api/jobs/{job_id}/unlink-duplicate", response_model=JobDetailOut, tags=["remote"])
def unlink_duplicate(job_id: str, db: DbSession) -> dict[str, object]:
    """Owner override: this posting is not a duplicate. It is re-gated from scratch (never applied)."""
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    try:
        unlink(db, row)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return job_detail(db, row)


@router.post("/api/jobs/{job_id}/check-liveness", response_model=JobDetailOut, tags=["remote"])
def check_liveness(job_id: str, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Ask the posting's public source whether it is still open (read-only GET).

    Follows the same conservative policy as scheduled checks: one "gone" answer
    only marks the posting suspect; errors never close it.
    """
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    check_job(db, current, row, CheckContext("manual"))
    return job_detail(db, row)


@router.get("/api/jobs/{job_id}/form", response_model=FormPreviewOut, tags=["remote"])
def job_form(job_id: str, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Fetch the job's real application form (read-only) and plan it against the vault.

    No state changes, no evidence, nothing submitted. Values for sensitive or
    legal fields are redacted in the response.
    """
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    try:
        form = LiveFormProvider(current).fetch(row)
    except FormFetchError as exc:
        raise HTTPException(502 if exc.reason_code == "form_fetch_failed" else 409, {"reason": exc.reason_code, "detail": exc.detail}) from exc
    plan, form_summary = preview_form(db, form)
    return {
        "form": form_summary,
        "ready": plan.ready,
        "blockers": plan.blockers,
        "fields": [{
            "key": item.control.key,
            "label": item.control.label,
            "type": item.control.control_type.value,
            "section": item.control.section,
            "required": item.control.required,
            "sensitive": item.control.sensitive,
            "legal": item.control.legal,
            "options": len(item.control.options),
            "status": item.status,
            "reason": item.reason,
            "value": ("[set]" if item.control.sensitive or item.control.legal else item.value) if item.status == "fill" else None,
        } for item in plan.items],
    }


# ------------------------------------------------------------------------ review


@router.get("/api/review-tasks", response_model=list[ReviewTaskOut], tags=["remote"])
def review_tasks(
    db: DbSession,
    status: Literal["open", "closed", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, object]]:
    query = select(ReviewTask).order_by(ReviewTask.created_at.desc()).limit(limit)
    if status == "open":
        query = query.where(ReviewTask.status == "open")
    elif status == "closed":
        query = query.where(ReviewTask.status != "open")
    return [task_summary(db, task) for task in db.execute(query).scalars()]


@router.get("/api/review-tasks/{task_id}", response_model=ReviewTaskDetailOut, tags=["remote"])
def review_task(task_id: str, db: DbSession) -> dict[str, object]:
    task = db.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "review task not found")
    return task_detail(db, task)


@router.post("/api/review-tasks/{task_id}/approve", response_model=ReviewActionOut, tags=["remote"])
def approve_review(task_id: str, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Approve the job/application behind a task. Never applies or submits."""
    try:
        return approve_task(db, current, task_id)
    except ReviewActionError as exc:
        raise _refused(exc) from exc


@router.post("/api/review-tasks/{task_id}/reject", response_model=ReviewActionOut, tags=["remote"])
def reject_review(task_id: str, db: DbSession) -> dict[str, object]:
    try:
        return reject_task(db, task_id)
    except ReviewActionError as exc:
        raise _refused(exc) from exc


@router.post("/api/review-tasks/{task_id}/resolve", response_model=ReviewActionOut, tags=["remote"])
def resolve_review(task_id: str, db: DbSession, payload: ReviewResolveIn | None = None) -> dict[str, object]:
    """Close a task without changing the job (e.g. you handled it by hand)."""
    try:
        return resolve_task(db, task_id, (payload or ReviewResolveIn()).resolution)
    except ReviewActionError as exc:
        raise _refused(exc) from exc


# --------------------------------------------------------------------- scheduler


@router.get("/api/scheduler/status", response_model=SchedulerStatusOut, tags=["remote"])
def scheduler_status(db: DbSession, runner: Runner) -> dict[str, object]:
    return runner.status(db)


@router.get("/api/scheduler/cycles", response_model=list[CycleOut], tags=["remote"])
def scheduler_cycles(db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict[str, object]]:
    return recent_cycles(db, limit)


@router.post("/api/scheduler/run", status_code=202, response_model=RunCycleOut, tags=["remote"],
             responses={409: {"model": RunCycleOut}})
def scheduler_run(runner: Runner) -> JSONResponse:
    """Start one cycle now in the background (same gates and caps as scheduled cycles)."""
    if not runner.trigger_async():
        return JSONResponse({"started": False, "reason": "a cycle is already running", "status_url": None}, status_code=409)
    return JSONResponse({"started": True, "reason": None, "status_url": "/api/scheduler/status"}, status_code=202)


@router.post("/api/scheduler/pause", response_model=SchedulerStatusOut, tags=["remote"])
def scheduler_pause(db: DbSession, runner: Runner, payload: NoteIn | None = None) -> dict[str, object]:
    """Stop starting new scheduled cycles (a running cycle finishes)."""
    set_flag(db, "scheduler_paused", True, (payload.note if payload else "") or "paused from the API")
    return runner.status(db)


@router.post("/api/scheduler/resume", response_model=SchedulerStatusOut, tags=["remote"])
def scheduler_resume(db: DbSession, runner: Runner, payload: NoteIn | None = None) -> dict[str, object]:
    """Allow scheduled cycles again (the kill switch, if engaged, still blocks them)."""
    set_flag(db, "scheduler_paused", False, (payload.note if payload else "") or "resumed from the API")
    return runner.status(db)


# ------------------------------------------------------------- kill switch/flags


def _kill_switch(db: Session) -> dict[str, object]:
    snapshot(db)  # make sure the default flags exist
    flag = db.get(FeatureFlag, KILL_SWITCH)
    return {"engaged": bool(flag and flag.enabled), "note": flag.note if flag else "", "updated_at": iso_utc(flag.updated_at) if flag else None}


@router.get("/api/kill-switch", response_model=KillSwitchOut, tags=["remote"])
def kill_switch(db: DbSession) -> dict[str, object]:
    return _kill_switch(db)


@router.post("/api/kill-switch", response_model=KillSwitchOut, tags=["remote"])
def set_kill_switch(payload: KillSwitchIn, db: DbSession) -> dict[str, object]:
    """Engage (always allowed) or disengage (needs ``confirm: true``) the kill switch."""
    try:
        check_api_flag_change(KILL_SWITCH, payload.engaged, payload.confirm)
    except FlagChangeRefused as exc:
        raise _refused(exc) from exc
    default_note = "engaged from the API" if payload.engaged else "disengaged from the API"
    set_flag(db, KILL_SWITCH, payload.engaged, payload.note or default_note)
    return _kill_switch(db)


@router.get("/api/flags", tags=["remote"])
def flags(db: DbSession) -> dict[str, bool]:
    return snapshot(db)


@router.put("/api/flags/{key}", response_model=FlagOut, tags=["remote"])
def update_flag(key: str, payload: FlagIn, db: DbSession) -> dict[str, object]:
    """Set a known flag. Live-submission flags can only be turned off."""
    try:
        check_api_flag_change(key, payload.enabled, payload.confirm)
    except FlagChangeRefused as exc:
        raise _refused(exc) from exc
    flag = set_flag(db, key, payload.enabled, payload.note)
    return {"key": flag.key, "enabled": flag.enabled, "note": flag.note}


# ----------------------------------------------------------------------- backups


@router.get("/api/backups", response_model=BackupsOut, tags=["remote"])
def backups(current: CurrentSettings) -> dict[str, object]:
    """Backup file names, sizes and times (newest first). Never the file contents."""
    backup_dir = Path(current.backup_dir)
    items = []
    for path in reversed(list_backups(backup_dir)):
        stat = path.stat()
        items.append({
            "name": path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        })
    latest = latest_backup_time(backup_dir)
    return {
        "items": items,
        "latest_at": latest.isoformat() if latest else None,
        "retention": current.backup_retention,
        "interval_hours": current.backup_interval_hours,
    }
