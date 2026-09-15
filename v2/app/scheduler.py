from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapter_runtime import ADAPTER_CATALOG, detect_platform
from app.config import Settings
from app.flags import is_enabled, kill_switch_engaged
from app.models import AdapterMaturity, Application, Job, PipelineStage, utcnow


@dataclass(slots=True)
class SchedulerDecision:
    allowed: bool
    reason: str


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    current = now.time().replace(tzinfo=None)
    start_t = _parse_hhmm(start)
    end_t = _parse_hhmm(end)
    if start_t == end_t:
        return False
    if start_t < end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t


def submissions_today(db: Session) -> int:
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.execute(
        select(func.count(Application.id)).where(
            Application.stage.in_([PipelineStage.submitted.value, PipelineStage.confirmed.value]),
            Application.updated_at >= start,
        )
    ).scalar_one())


def can_run_unattended(db: Session, settings: Settings, job: Job | None = None, now: datetime | None = None) -> SchedulerDecision:
    now = now or datetime.now(timezone.utc)
    if kill_switch_engaged(db):
        return SchedulerDecision(False, "global kill switch")
    if not settings.automation_enabled:
        return SchedulerDecision(False, "automation disabled")
    if not is_enabled(db, "unattended_mode"):
        return SchedulerDecision(False, "unattended mode disabled")
    if in_quiet_hours(now, settings.quiet_hours_start, settings.quiet_hours_end):
        return SchedulerDecision(False, "quiet hours")
    if submissions_today(db) >= settings.max_applications_per_day:
        return SchedulerDecision(False, "daily cap reached")
    if job is not None:
        platform = job.platform or detect_platform(job.url)
        info = ADAPTER_CATALOG.get(platform)
        if info is None:
            return SchedulerDecision(False, "unknown platform")
        if not is_enabled(db, info.feature_flag):
            return SchedulerDecision(False, f"{platform} adapter disabled")
        if info.maturity is not AdapterMaturity.certified_autonomous:
            return SchedulerDecision(False, f"{platform} is {info.maturity.value}, not certified_autonomous")
        if job.company.lower() in {name.lower() for name in settings.blacklisted_company_list}:
            return SchedulerDecision(False, "employer excluded")
    return SchedulerDecision(True, "eligible for unattended apply")
