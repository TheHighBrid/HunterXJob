"""Safety rails, always enforced regardless of automation mode.

- Configurable daily submission cap
- Configurable minimum delay between application submissions
- Company/domain blacklist

All checks take a `RuntimeSettings` (app.services.runtime_settings) rather
than the raw env-only `Settings`, so overrides made via `PUT /api/settings`
are honored on the very next cycle instead of requiring a process restart.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Application
from app.services.runtime_settings import RuntimeSettings


def _submitted_today_count(db: Session) -> int:
    start_of_day = dt.datetime.combine(dt.date.today(), dt.time.min)
    stmt = select(func.count(Application.id)).where(
        Application.submitted_at.is_not(None),
        Application.submitted_at >= start_of_day,
    )
    return db.execute(stmt).scalar_one()


def enforce_daily_cap(db: Session, settings: RuntimeSettings) -> bool:
    """Return True if another submission is allowed today under the daily cap."""
    return _submitted_today_count(db) < settings.max_applications_per_day


def enforce_delay(db: Session, settings: RuntimeSettings) -> bool:
    """Return True if enough time has passed since the last submission."""
    stmt = (
        select(Application.submitted_at)
        .where(Application.submitted_at.is_not(None))
        .order_by(Application.submitted_at.desc())
        .limit(1)
    )
    last_submitted_at = db.execute(stmt).scalar_one_or_none()
    if last_submitted_at is None:
        return True
    elapsed = (dt.datetime.utcnow() - last_submitted_at).total_seconds()
    return elapsed >= settings.min_delay_between_applications_seconds


def is_blacklisted(company: str, settings: RuntimeSettings) -> bool:
    if not company:
        return False
    company_lower = company.strip().lower()
    for entry in settings.blacklisted_companies:
        if entry and (entry == company_lower or entry in company_lower):
            return True
    return False


def can_submit_now(db: Session, settings: RuntimeSettings, company: str) -> tuple[bool, str]:
    """Combined safety check. Returns (allowed, reason_if_blocked)."""
    if not settings.automation_enabled:
        return False, "automation is disabled in settings"
    if is_blacklisted(company, settings):
        return False, f"company '{company}' is blacklisted"
    if not enforce_daily_cap(db, settings):
        return False, f"daily cap of {settings.max_applications_per_day} applications reached"
    if not enforce_delay(db, settings):
        return False, (
            f"minimum delay of {settings.min_delay_between_applications_seconds}s "
            "since last submission not yet elapsed"
        )
    return True, ""
