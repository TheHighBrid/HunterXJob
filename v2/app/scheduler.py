from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapter_runtime import ADAPTER_CATALOG, detect_platform
from app.config import Settings
from app.flags import is_enabled, kill_switch_engaged
from app.models import AdapterMaturity, Application, Job, PipelineEvent, PipelineStage

logger = logging.getLogger(__name__)


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


@lru_cache(maxsize=8)
def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown TIMEZONE %r; using UTC", name)
        return UTC


def local_now(settings: Settings, now: datetime | None = None) -> datetime:
    """``now`` (default: current time) in the configured TIMEZONE."""
    return (now or datetime.now(UTC)).astimezone(_zone(settings.timezone))


def day_start_utc(settings: Settings, now: datetime | None = None) -> datetime:
    """Start of the current local day (TIMEZONE), expressed in UTC."""
    local = local_now(settings, now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def dry_runs_today(db: Session, since: datetime) -> int:
    """Dry-run attempts that reached the form since ``since`` (manual or scheduled)."""
    return int(db.execute(
        select(func.count(PipelineEvent.id)).where(
            PipelineEvent.to_stage == PipelineStage.applying.value,
            PipelineEvent.created_at >= since,
        )
    ).scalar_one())


def submissions_today(db: Session, since: datetime | None = None) -> int:
    start = since or datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.execute(
        select(func.count(Application.id)).where(
            Application.stage.in_([PipelineStage.submitted.value, PipelineStage.confirmed.value]),
            Application.updated_at >= start,
        )
    ).scalar_one())


def can_run_unattended(db: Session, settings: Settings, job: Job | None = None, now: datetime | None = None) -> SchedulerDecision:
    now = local_now(settings, now)
    if kill_switch_engaged(db):
        return SchedulerDecision(False, "global kill switch")
    if not settings.automation_enabled:
        return SchedulerDecision(False, "automation disabled")
    if not is_enabled(db, "unattended_mode"):
        return SchedulerDecision(False, "unattended mode disabled")
    if in_quiet_hours(now, settings.quiet_hours_start, settings.quiet_hours_end):
        return SchedulerDecision(False, "quiet hours")
    if submissions_today(db, day_start_utc(settings, now)) >= settings.max_applications_per_day:
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
