"""Read-only summary numbers for the phone's Dashboard and Reports screens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.cycle import recent_cycles
from app.models import Application, Job, PipelineStage, ReviewTask, SchedulerCycle, SubmissionEvidence
from app.scheduler import day_start_utc, dry_runs_today, local_now, submissions_today

SHORTLIST_OR_LATER = frozenset({
    PipelineStage.shortlisted.value,
    PipelineStage.approved.value,
    PipelineStage.preparing.value,
    PipelineStage.materials_generated.value,
    PipelineStage.materials_reviewed.value,
    PipelineStage.ready_to_apply.value,
    PipelineStage.applying.value,
    PipelineStage.form_filled.value,
    PipelineStage.validated.value,
    PipelineStage.needs_review.value,
})
SUBMITTED_STAGES = (PipelineStage.submitted.value, PipelineStage.confirmed.value)
HISTORY_DAYS = 7


def _count(db: Session, query) -> int:
    return int(db.execute(query).scalar_one())


def _grouped(db: Session, column, *where) -> dict[str, int]:
    query = select(column, func.count()).group_by(column)
    for clause in where:
        query = query.where(clause)
    return {str(key): int(count) for key, count in db.execute(query).all()}


def _as_utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _daily_history(db: Session, settings: Settings, now: datetime) -> list[dict[str, Any]]:
    """Per local day (oldest first): jobs discovered, dry-runs completed, cycles run."""
    today = local_now(settings, now).date()
    days = [today - timedelta(days=offset) for offset in range(HISTORY_DAYS - 1, -1, -1)]
    since = day_start_utc(settings, now) - timedelta(days=HISTORY_DAYS - 1)
    buckets = {day: {"date": day.isoformat(), "discovered": 0, "dry_runs": 0, "cycles": 0} for day in days}

    def tally(rows, field: str) -> None:
        for (stamp,) in rows:
            day = local_now(settings, _as_utc(stamp)).date()
            if day in buckets:
                buckets[day][field] += 1

    tally(db.execute(select(Job.discovered_at).where(Job.discovered_at >= since)).all(), "discovered")
    tally(db.execute(select(SubmissionEvidence.created_at).where(
        SubmissionEvidence.kind == "dry_run", SubmissionEvidence.created_at >= since)).all(), "dry_runs")
    tally(db.execute(select(SchedulerCycle.started_at).where(
        SchedulerCycle.started_at >= since, SchedulerCycle.status != "skipped")).all(), "cycles")
    return [buckets[day] for day in days]


def summary(db: Session, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = day_start_utc(settings, now)
    jobs_by_stage = _grouped(db, Job.stage)
    return {
        "generated_at": now.isoformat(),
        "timezone": settings.timezone,
        "live_submission_locked": True,
        "jobs": {
            "total": sum(jobs_by_stage.values()),
            "discovered_today": _count(db, select(func.count(Job.id)).where(Job.discovered_at >= since)),
            "scored": _count(db, select(func.count(Job.id)).where(Job.final_score.is_not(None))),
            "shortlisted": sum(count for stage, count in jobs_by_stage.items() if stage in SHORTLIST_OR_LATER),
            "rejected": jobs_by_stage.get(PipelineStage.rejected.value, 0),
            "by_stage": jobs_by_stage,
        },
        "applications": {
            "total": _count(db, select(func.count(Application.id))),
            "ready_to_apply": _count(db, select(func.count(Application.id)).where(
                Application.stage == PipelineStage.ready_to_apply.value)),
            "by_stage": _grouped(db, Application.stage),
        },
        "dry_runs": {
            "today": dry_runs_today(db, since),
            "completed_today": _count(db, select(func.count(SubmissionEvidence.id)).where(
                SubmissionEvidence.kind == "dry_run", SubmissionEvidence.created_at >= since)),
            "completed_total": _count(db, select(func.count(SubmissionEvidence.id)).where(
                SubmissionEvidence.kind == "dry_run")),
            "daily_cap": settings.max_dry_runs_per_day,
        },
        "submissions": {
            "today": submissions_today(db, since),
            "total": _count(db, select(func.count(Application.id)).where(Application.stage.in_(SUBMITTED_STAGES))),
            "daily_cap": settings.max_applications_per_day,
        },
        "review": {
            "open": _count(db, select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")),
            "open_by_reason": _grouped(db, ReviewTask.reason_code, ReviewTask.status == "open"),
            "closed_total": _count(db, select(func.count(ReviewTask.id)).where(ReviewTask.status != "open")),
        },
        "cycles": {
            "last_24h": _grouped(db, SchedulerCycle.status, SchedulerCycle.started_at >= now - timedelta(hours=24)),
            "last": next(iter(recent_cycles(db, 1)), None),
        },
        "history": _daily_history(db, settings, now),
    }
