"""Observational ATS Adapter Health Monitoring.

Derives platform-specific reliability metrics from durable database records:
- Success and failure rates per ATS platform.
- CAPTCHA and anti-bot challenge frequencies.
- Categorized breakdown of recent failure causes.
- Health status categorization (healthy, degraded, failing, insufficient_data).

ARCHITECTURAL SAFETY INVARIANTS:
1. Purely observational: Reports are read-only and contain no applicant PII.
2. Degraded health scores NEVER auto-disable adapters, prevent applications,
   or alter execution permissions.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Job, PipelineStage, ReviewTask, SubmissionEvidence

logger = logging.getLogger(__name__)


@dataclass
class PlatformHealthReport:
    platform: str
    total_attempts: int
    successful_submissions: int
    uncertain_outcomes: int
    failed_attempts: int
    review_tasks: int
    captcha_challenges: int
    success_rate: float
    health_status: str  # "healthy", "degraded", "failing", "insufficient_data"
    failure_reasons: dict[str, int]
    window_days: int
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_platform_health(
    db: Session,
    *,
    platform: str | None = None,
    window_days: int = 14,
) -> dict[str, PlatformHealthReport]:
    """Calculate rolling health metrics across ATS platforms from existing DB records."""
    since = datetime.now(UTC) - timedelta(days=window_days)

    query = (
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.updated_at >= since)
    )
    if platform:
        query = query.where(Job.platform == platform)

    rows = db.execute(query).all()

    # Group by platform
    platforms_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "attempts": 0,
        "successful": 0,
        "uncertain": 0,
        "failed": 0,
        "reasons": Counter(),
    })

    for app, job in rows:
        plat = job.platform or app.adapter_name or "generic"
        data = platforms_data[plat]
        data["attempts"] += 1

        if app.stage in {PipelineStage.submitted.value, PipelineStage.confirmed.value}:
            data["successful"] += 1
        elif app.stage == PipelineStage.submission_uncertain.value:
            data["uncertain"] += 1
        elif app.stage in {PipelineStage.failed.value, PipelineStage.rejected.value}:
            data["failed"] += 1

        if app.last_error:
            # Capture error snippet for reason grouping
            snippet = app.last_error.split(":")[0].strip()[:50]
            data["reasons"][snippet] += 1

    # Query review tasks in window for CAPTCHA counts
    task_query = select(ReviewTask).where(ReviewTask.created_at >= since)
    review_tasks = db.execute(task_query).scalars().all()
    captcha_counts: dict[str, int] = Counter()
    review_counts: dict[str, int] = Counter()

    for task in review_tasks:
        plat = "generic"
        if task.job_id:
            task_job = db.get(Job, task.job_id)
            if task_job and task_job.platform:
                plat = task_job.platform
        review_counts[plat] += 1
        if task.reason_code in {"captcha_detected", "anti_bot_challenge"}:
            captcha_counts[plat] += 1

    reports: dict[str, PlatformHealthReport] = {}

    for plat, data in platforms_data.items():
        attempts = data["attempts"]
        successful = data["successful"]
        uncertain = data["uncertain"]
        failed = data["failed"]
        captchas = captcha_counts.get(plat, 0)
        reviews = review_counts.get(plat, 0)

        rate = round(successful / attempts, 4) if attempts > 0 else 0.0

        if attempts < 3:
            status = "insufficient_data"
        elif rate >= 0.80:
            status = "healthy"
        elif rate >= 0.50:
            status = "degraded"
        else:
            status = "failing"

        reports[plat] = PlatformHealthReport(
            platform=plat,
            total_attempts=attempts,
            successful_submissions=successful,
            uncertain_outcomes=uncertain,
            failed_attempts=failed,
            review_tasks=reviews,
            captcha_challenges=captchas,
            success_rate=rate,
            health_status=status,
            failure_reasons=dict(data["reasons"]),
            window_days=window_days,
            generated_at=datetime.now(UTC).isoformat(),
        )

    return reports
