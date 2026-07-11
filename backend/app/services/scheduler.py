"""APScheduler background scheduler wiring together all periodic cycles.

- job_search_cycle: pulls from configured job sources, scores + stores new
  JobPosting rows (skips postings already seen by source+external_id).
- automation_cycle: for eligible queued applications, enforces safety rails
  (daily cap / delay / blacklist) then calls the right channel adapter,
  updates status, and writes an AutomationRun row.
- email_check_cycle: polls IMAP for reply-driven status updates.
- report_generation_cycle: writes a periodic summary Report row.

Kept intentionally simple (in-process APScheduler, no Redis/Celery) per
docs/ARCHITECTURE.md's "genuinely free/zero-ops for a single user" choice.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db as db_module
from app.config import Settings, get_settings
from app.models import (
    Application,
    ApplicationStatus,
    AutomationRun,
    JobPosting,
    Profile,
    Report,
    ResumeVersion,
)
from app.services import safety
from app.services.runtime_settings import RuntimeSettings, load_runtime_settings
from app.services.automation.base import ApplicationAdapter
from app.services.automation.email_adapter import EmailAdapter
from app.services.automation.generic_playwright_adapter import GenericPlaywrightAdapter
from app.services.automation.greenhouse_adapter import GreenhouseAdapter
from app.services.automation.lever_adapter import LeverAdapter
from app.services.content_generation import generate_application_email, generate_cover_letter
from app.services.email_monitor import check_inbox_for_status_updates
from app.services.job_sources.generic_feed import GenericFeedAdapter
from app.services.job_sources.greenhouse import GreenhouseAdapter as GreenhouseSourceAdapter
from app.services.job_sources.lever import LeverAdapter as LeverSourceAdapter
from app.services.llm.factory import get_llm_provider
from app.services.matching import score_job_fit
from app.services.qa_bank import QAAnswerBank
from app.services.resume_render import render_resume_pdf

logger = logging.getLogger("hunterxjob.scheduler")

_scheduler: BackgroundScheduler | None = None


# --------------------------------------------------------------------------
# job_search_cycle
# --------------------------------------------------------------------------
def job_search_cycle() -> int:
    """Pull postings from all configured job sources, score + store new ones."""
    settings = get_settings()
    db = db_module.SessionLocal()
    new_count = 0
    try:
        adapters = []
        if settings.greenhouse_board_tokens_list:
            adapters.append(GreenhouseSourceAdapter(settings.greenhouse_board_tokens_list))
        if settings.lever_companies_list:
            adapters.append(LeverSourceAdapter(settings.lever_companies_list))
        if settings.generic_feed_urls_list:
            adapters.append(GenericFeedAdapter(settings.generic_feed_urls_list))

        base_resume = db.execute(
            select(ResumeVersion).where(ResumeVersion.is_base.is_(True))
        ).scalars().first()
        resume_text = ""
        if base_resume:
            resume_text = _resume_json_to_text(json.loads(base_resume.resume_json))

        for adapter in adapters:
            try:
                postings = adapter.search()
            except Exception as exc:  # noqa: BLE001
                logger.warning("job source %s failed: %s", adapter, exc)
                continue

            for dto in postings:
                exists = db.execute(
                    select(JobPosting).where(
                        JobPosting.source == dto.source,
                        JobPosting.external_id == dto.external_id,
                    )
                ).scalars().first()
                if exists:
                    continue

                match_score = score_job_fit(resume_text, dto.description) if resume_text else None
                job = JobPosting(
                    source=dto.source,
                    external_id=dto.external_id,
                    title=dto.title,
                    company=dto.company,
                    location=dto.location,
                    remote=dto.remote,
                    description=dto.description,
                    url=dto.url,
                    match_score=match_score,
                )
                db.add(job)
                new_count += 1
        db.commit()
    finally:
        db.close()
    logger.info("job_search_cycle: %d new postings", new_count)
    return new_count


def _resume_json_to_text(resume_json: dict) -> str:
    parts = [resume_json.get("summary", "")]
    parts.extend(resume_json.get("skills", []) or [])
    for job in resume_json.get("experience", []) or []:
        parts.append(job.get("title", ""))
        parts.extend(job.get("bullets", []) or [])
    return "\n".join(str(p) for p in parts if p)


# --------------------------------------------------------------------------
# automation_cycle
# --------------------------------------------------------------------------
def _get_adapter_for_channel(
    channel: str, runtime: RuntimeSettings, settings: Settings, db: Session
) -> ApplicationAdapter | None:
    dry_run = runtime.automation_dry_run
    if channel == "greenhouse":
        return GreenhouseAdapter(dry_run=dry_run)
    if channel == "lever":
        return LeverAdapter(dry_run=dry_run)
    if channel == "email":
        return EmailAdapter(settings)
    if channel == "generic":
        return GenericPlaywrightAdapter(qa_bank=QAAnswerBank(db), dry_run=dry_run)
    # linkedin/indeed/upwork: roadmap scaffolds, inert unless explicitly enabled elsewhere
    return None


def automation_cycle() -> AutomationRun:
    """Process queued applications, respecting safety rails, one run record per cycle."""
    settings = get_settings()
    db = db_module.SessionLocal()
    run = AutomationRun(started_at=dt.datetime.utcnow())
    errors: list[str] = []
    try:
        runtime = load_runtime_settings(db, settings)

        db.add(run)
        db.commit()
        db.refresh(run)

        if not runtime.automation_enabled:
            run.finished_at = dt.datetime.utcnow()
            run.errors_json = json.dumps(["automation is disabled in settings; cycle skipped"])
            db.add(run)
            db.commit()
            db.refresh(run)
            return run

        profile = db.execute(select(Profile)).scalars().first()
        llm = None

        queued_apps = db.execute(
            select(Application).where(Application.status == ApplicationStatus.QUEUED.value)
        ).scalars().all()

        for application in queued_apps:
            job_posting = db.get(JobPosting, application.job_posting_id)
            if job_posting is None:
                continue

            allowed, reason = safety.can_submit_now(db, runtime, job_posting.company)
            if not allowed:
                if "blacklisted" in reason:
                    application.status = ApplicationStatus.BLOCKED.value
                    application.notes = reason
                    db.add(application)
                    db.commit()
                    run.applications_blocked += 1
                # cap/delay: stop processing further applications this cycle
                break

            resume_version = None
            if application.resume_version_id:
                resume_version = db.get(ResumeVersion, application.resume_version_id)
            else:
                resume_version = db.execute(
                    select(ResumeVersion).where(ResumeVersion.is_base.is_(True))
                ).scalars().first()

            if resume_version is None or not resume_version.pdf_path:
                application.status = ApplicationStatus.NEEDS_REVIEW.value
                application.notes = "no rendered resume PDF available"
                db.add(application)
                db.commit()
                continue

            cover_letter_text = application.cover_letter_text
            try:
                if not cover_letter_text and profile is not None:
                    if llm is None:
                        llm = get_llm_provider(settings, provider_override=runtime.llm_provider)
                    resume_json = json.loads(resume_version.resume_json)
                    profile_dict = {
                        "full_name": profile.full_name,
                        "email": profile.email,
                        "phone": profile.phone,
                        "location": profile.location,
                    }
                    job_dict = {
                        "title": job_posting.title,
                        "company": job_posting.company,
                        "description": job_posting.description,
                    }
                    cover_letter_text = generate_cover_letter(profile_dict, resume_json, job_dict, llm)
                    application.cover_letter_text = cover_letter_text
                    db.add(application)
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"content generation failed for application {application.id}: {exc}")
                application.status = ApplicationStatus.NEEDS_REVIEW.value
                application.notes = f"content generation failed: {exc}"
                db.add(application)
                db.commit()
                continue

            adapter = _get_adapter_for_channel(application.channel, runtime, settings, db)
            if adapter is None or not getattr(adapter, "enabled", True):
                application.status = ApplicationStatus.NEEDS_REVIEW.value
                application.notes = (
                    f"channel '{application.channel}' has no active adapter "
                    "(roadmap channel or unrecognized)"
                )
                db.add(application)
                db.commit()
                continue

            contact_email = None
            profile_for_adapter = {
                "full_name": profile.full_name if profile else "",
                "email": profile.email if profile else "",
                "phone": profile.phone if profile else "",
            }
            # attach a lightweight profile dict the adapters can read
            application.profile = profile_for_adapter  # type: ignore[attr-defined]

            try:
                result = adapter.submit(
                    application=application,
                    job_posting=job_posting,
                    resume_pdf_path=resume_version.pdf_path,
                    cover_letter=cover_letter_text or "",
                    contact_email=contact_email,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"adapter error for application {application.id}: {exc}")
                application.status = ApplicationStatus.FAILED.value
                application.notes = str(exc)
                db.add(application)
                db.commit()
                continue

            application.notes = result.detail
            if result.status == "submitted":
                application.status = ApplicationStatus.APPLIED.value
                application.submitted_at = dt.datetime.utcnow()
                run.applications_submitted += 1
            elif result.status == "blocked":
                application.status = ApplicationStatus.BLOCKED.value
                run.applications_blocked += 1
            elif result.status == "needs_review":
                application.status = ApplicationStatus.NEEDS_REVIEW.value
            else:  # failed
                application.status = ApplicationStatus.FAILED.value

            db.add(application)
            db.commit()

        run.finished_at = dt.datetime.utcnow()
        run.errors_json = json.dumps(errors)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run
    finally:
        db.close()


# --------------------------------------------------------------------------
# email_check_cycle
# --------------------------------------------------------------------------
def email_check_cycle():
    settings = get_settings()
    db = db_module.SessionLocal()
    try:
        result = check_inbox_for_status_updates(db, settings)
        logger.info(
            "email_check_cycle: scanned=%d updates=%d error=%s",
            result.scanned_count,
            len(result.updates),
            result.error,
        )
        return result
    finally:
        db.close()


# --------------------------------------------------------------------------
# report_generation_cycle
# --------------------------------------------------------------------------
def report_generation_cycle(period: str = "daily") -> Report:
    """Write a periodic summary report.

    Regardless of `period` (which controls the label and the window used for
    `new_jobs_found`/`status_counts`), the "_today"/"_this_week" fields
    always use fixed 24h/7d windows so the mobile dashboard's stat cards are
    meaningful whether the latest report happens to be a daily or weekly one.
    """
    db = db_module.SessionLocal()
    try:
        now = dt.datetime.utcnow()
        since_period = now - (dt.timedelta(days=1) if period == "daily" else dt.timedelta(days=7))
        since_today = now - dt.timedelta(days=1)
        since_week = now - dt.timedelta(days=7)

        new_jobs = db.execute(
            select(JobPosting).where(JobPosting.discovered_at >= since_period)
        ).scalars().all()
        applications = db.execute(
            select(Application).where(Application.last_status_change >= since_period)
        ).scalars().all()

        status_counts: dict[str, int] = {}
        for app_row in applications:
            status_counts[app_row.status] = status_counts.get(app_row.status, 0) + 1

        jobs_discovered_today = db.execute(
            select(func.count(JobPosting.id)).where(JobPosting.discovered_at >= since_today)
        ).scalar_one()
        applications_submitted_today = db.execute(
            select(func.count(Application.id)).where(
                Application.submitted_at.is_not(None), Application.submitted_at >= since_today
            )
        ).scalar_one()
        applications_submitted_this_week = db.execute(
            select(func.count(Application.id)).where(
                Application.submitted_at.is_not(None), Application.submitted_at >= since_week
            )
        ).scalar_one()
        pending_review_count = db.execute(
            select(func.count(Application.id)).where(
                Application.status == ApplicationStatus.NEEDS_REVIEW.value
            )
        ).scalar_one()
        applications_blocked = status_counts.get(ApplicationStatus.BLOCKED.value, 0)

        highlights: list[str] = []
        submitted_in_period = [a for a in applications if a.status == ApplicationStatus.APPLIED.value]
        for app_row in submitted_in_period[:5]:
            job = db.get(JobPosting, app_row.job_posting_id)
            if job:
                highlights.append(f"Applied to {job.title} at {job.company}.")
        needs_review_in_period = [
            a for a in applications if a.status == ApplicationStatus.NEEDS_REVIEW.value
        ]
        for app_row in needs_review_in_period[:5]:
            job = db.get(JobPosting, app_row.job_posting_id)
            if job:
                highlights.append(f"Flagged {job.title} at {job.company} for manual review.")

        errors: list[str] = []
        recent_runs = db.execute(
            select(AutomationRun).where(AutomationRun.started_at >= since_period)
        ).scalars().all()
        for run in recent_runs:
            errors.extend(json.loads(run.errors_json or "[]"))

        summary = {
            "period": period,
            "since": since_period.isoformat(),
            "new_jobs_found": len(new_jobs),
            "applications_touched": len(applications),
            "status_counts": status_counts,
            "jobs_discovered_today": jobs_discovered_today,
            "applications_submitted_today": applications_submitted_today,
            "applications_submitted_this_week": applications_submitted_this_week,
            "pending_review_count": pending_review_count,
            "applications_blocked": applications_blocked,
            "highlights": highlights,
            "errors": errors,
        }

        report = Report(period=period, summary_json=json.dumps(summary))
        db.add(report)
        db.commit()
        db.refresh(report)
        logger.info("report_generation_cycle: %s report generated", period)
        return report
    finally:
        db.close()


# --------------------------------------------------------------------------
# Scheduler lifecycle
# --------------------------------------------------------------------------
def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler:
    global _scheduler
    settings = settings or get_settings()

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        job_search_cycle,
        "interval",
        minutes=settings.JOB_SEARCH_INTERVAL_MINUTES,
        id="job_search_cycle",
        replace_existing=True,
    )
    scheduler.add_job(
        automation_cycle,
        "interval",
        minutes=settings.AUTOMATION_INTERVAL_MINUTES,
        id="automation_cycle",
        replace_existing=True,
    )
    scheduler.add_job(
        email_check_cycle,
        "interval",
        minutes=settings.EMAIL_CHECK_INTERVAL_MINUTES,
        id="email_check_cycle",
        replace_existing=True,
    )
    scheduler.add_job(
        report_generation_cycle,
        "interval",
        minutes=settings.REPORT_INTERVAL_MINUTES,
        id="report_generation_cycle",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler_status() -> dict:
    """Lightweight status used by GET /api/health for the mobile dashboard."""
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "next_automation_run": None}

    job = _scheduler.get_job("automation_cycle")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {"running": True, "next_automation_run": next_run}
