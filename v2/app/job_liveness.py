"""Conservative liveness checks for queued postings.

Each check asks the posting's own public source whether it still exists:

* Greenhouse: ``GET boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}`` (404 = gone)
* Lever: ``GET api.lever.co/v0/postings/{company}/{id}`` (404 = gone)
* Ashby: read-only GraphQL GET for the posting (``jobPosting: null`` = gone)
* anything else: the posting page itself (404/410, or an explicit
  "no longer accepting applications"-style marker = gone)

Policy (never closes on ambiguity):

* ``live`` resets the failure counter and schedules the next routine check.
  A posting that was closed is reopened (re-gated from ``discovered``).
* The first definitive ``gone`` only marks the posting ``suspect`` and
  schedules a confirmation; a second definitive ``gone`` at least
  ``liveness_confirm_minutes`` later closes it, closing its open review tasks
  with the reason.
* Anything else (network errors, timeouts, 5xx, 429, 403, CAPTCHA/challenge
  pages, unexpected payloads, GraphQL errors) is ``unknown``: it is recorded,
  backs off exponentially, never closes, and after
  ``liveness_review_after_failures`` consecutive failures opens a non-blocking
  ``liveness_review`` task.

Every observation is stored as a :class:`~app.models.LivenessCheck` row.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ashby_form import LIVENESS_QUERY, ashby_ref_for_job, graphql_get
from app.config import Settings
from app.dedup import deactivate, regate, release_duplicates
from app.greenhouse_form import GREENHOUSE_API_HOST, FormFetchError, ref_for_job
from app.lever_form import LEVER_API_HOST, USER_AGENT, lever_ref_for_job
from app.liveness import Liveness, classify_liveness
from app.models import Job, LivenessCheck, PipelineStage, ReviewTask
from app.review_queue import open_task

LIVE, GONE, UNKNOWN = "live", "gone", "unknown"
SUSPECT, CLOSED = "suspect", "closed"

#: Stages whose postings are checked on a schedule (queued, not yet applied).
QUEUED_STAGES = (
    PipelineStage.review, PipelineStage.scored, PipelineStage.shortlisted, PipelineStage.approved,
    PipelineStage.materials_generated, PipelineStage.materials_reviewed, PipelineStage.ready_to_apply,
    PipelineStage.needs_review, PipelineStage.validated,
)


@dataclass(frozen=True, slots=True)
class Observation:
    outcome: str
    signal: str
    http_status: int | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LivenessDecision:
    allowed: bool
    status: str
    action: str
    reason: str


Probe = Callable[[Job], Observation]


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Why and when a check runs, and which probe to use (default: the live read-only probe)."""

    trigger: str = "manual"
    probe: Probe | None = None
    now: datetime | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _transient(status: int) -> Observation:
    return Observation(UNKNOWN, f"http_{status}", status, f"HTTP {status} is not a definitive answer")


def _json_probe(http: httpx.Client, url: str, host: str) -> Observation:
    request = http.build_request("GET", url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    if request.method != "GET" or request.url.host != host:
        return Observation(UNKNOWN, "refused", None, "refusing non-GET or off-host liveness request")
    response = http.send(request)
    if response.status_code in {404, 410}:
        return Observation(GONE, "ats_api_not_found", response.status_code, f"{host} reports the posting does not exist")
    if response.status_code != 200:
        return _transient(response.status_code)
    try:
        data = response.json()
    except ValueError:
        return Observation(UNKNOWN, "non_json", 200, "unexpected non-JSON response")
    if isinstance(data, dict) and data.get("id"):
        return Observation(LIVE, "ats_api_ok", 200, "posting returned by the public API")
    return Observation(UNKNOWN, "unexpected_payload", 200, "API answered without the posting id")


def _ashby_probe(http: httpx.Client, job: Job) -> Observation:
    ref = ashby_ref_for_job(job)
    if ref is None:
        return Observation(UNKNOWN, "unidentified", None, "could not identify the Ashby posting")
    try:
        response = graphql_get(ref, LIVENESS_QUERY, client=http)
    except FormFetchError as exc:
        return Observation(UNKNOWN, "refused", None, exc.detail)
    if response.status_code != 200:
        return _transient(response.status_code)
    try:
        data = response.json()
    except ValueError:
        return Observation(UNKNOWN, "non_json", 200, "unexpected non-JSON response")
    if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
        return Observation(UNKNOWN, "graphql_error", 200, "Ashby answered with errors or no data")
    posting = data["data"].get("jobPosting")
    if posting is None:
        return Observation(GONE, "ashby_posting_null", 200, "Ashby reports no such posting")
    note = " (unlisted on the public board)" if isinstance(posting, dict) and posting.get("isListed") is False else ""
    return Observation(LIVE, "ashby_posting_ok", 200, f"posting returned by Ashby{note}")


def _page_probe(http: httpx.Client, job: Job) -> Observation:
    url = job.url or ""
    if not url.startswith("https://"):
        return Observation(UNKNOWN, "unsupported_url", None, "posting URL is not https; not checked")
    response = http.get(url, headers={"Accept": "text/html", "User-Agent": USER_AGENT}, follow_redirects=True)
    result = classify_liveness(response.status_code, str(response.url), response.text[:200_000])
    if result.status is Liveness.EXPIRED:
        return Observation(GONE, "page_closed_signal", response.status_code, result.reason)
    if result.status is Liveness.LIVE:
        return Observation(LIVE, "page_apply_markers", response.status_code, result.reason)
    return Observation(UNKNOWN, f"page_{result.status.value}", response.status_code, result.reason)


def probe_posting(job: Job, *, client: httpx.Client | None = None, timeout: float = 15.0) -> Observation:
    """One read-only GET to the posting's public source. Never raises for network trouble."""
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, follow_redirects=False)
    try:
        greenhouse = ref_for_job(job) if job.source == "greenhouse" or "greenhouse" in (job.url or "") else None
        if greenhouse is not None:
            return _json_probe(http, greenhouse.api_url, GREENHOUSE_API_HOST)
        lever = lever_ref_for_job(job)
        if lever is not None:
            return _json_probe(http, lever.api_url, LEVER_API_HOST)
        if job.source == "ashby" or "ashbyhq.com" in (job.url or ""):
            return _ashby_probe(http, job)
        return _page_probe(http, job)
    except httpx.HTTPError as exc:
        return Observation(UNKNOWN, "network_error", None, f"{exc.__class__.__name__}: {exc}"[:300])
    finally:
        if owns_client:
            http.close()


# --- policy -------------------------------------------------------------------------------------

def _backoff(settings: Settings, failures: int) -> timedelta:
    minutes = settings.liveness_backoff_minutes * (2 ** max(failures - 1, 0))
    return min(timedelta(minutes=minutes), timedelta(hours=settings.liveness_backoff_max_hours))


def _on_live(db: Session, settings: Settings, job: Job, now: datetime) -> str:
    action = "none"
    if job.stage == PipelineStage.closed.value:
        regate(db, job, "posting is live again; re-gated from scratch")
        job.closed_at = None
        action = "reopened"
    job.liveness = LIVE
    job.liveness_failures = 0
    job.liveness_next_check_at = now + timedelta(hours=settings.liveness_recheck_hours)
    return action


def _on_gone(db: Session, settings: Settings, job: Job, observation: Observation, now: datetime) -> str:
    confirm = timedelta(minutes=settings.liveness_confirm_minutes)
    last = _utc(job.liveness_checked_at)
    if job.stage == PipelineStage.closed.value:
        job.liveness = CLOSED
        job.liveness_next_check_at = now + timedelta(hours=settings.liveness_recheck_hours)
        return "none"
    if job.liveness != SUSPECT or last is None:
        job.liveness = SUSPECT
        job.liveness_next_check_at = now + confirm
        return "suspect"
    if now - last < confirm:
        # Two answers in quick succession are one observation; wait for the confirmation window.
        job.liveness_next_check_at = last + confirm
        return "awaiting_confirmation"
    reason = f"posting closed: {observation.detail or observation.signal} (confirmed by two checks)"
    if not deactivate(db, job, PipelineStage.closed, reason, {"signal": observation.signal, "http_status": observation.http_status}):
        job.liveness_next_check_at = now + confirm
        return "close_blocked"
    job.liveness = CLOSED
    job.closed_at = now
    job.liveness_next_check_at = now + timedelta(hours=settings.liveness_recheck_hours)
    release_duplicates(db, job, "closed")
    return "closed"


def _on_unknown(db: Session, settings: Settings, job: Job, observation: Observation, now: datetime) -> str:
    job.liveness_failures = (job.liveness_failures or 0) + 1
    if job.liveness not in {SUSPECT, CLOSED}:
        job.liveness = UNKNOWN
    job.liveness_next_check_at = now + _backoff(settings, job.liveness_failures)
    if job.liveness_failures < settings.liveness_review_after_failures:
        return "backoff"
    already = db.execute(select(ReviewTask.id).where(
        ReviewTask.job_id == job.id, ReviewTask.reason_code == "liveness_review", ReviewTask.status == "open"
    )).first()
    if already:
        return "backoff"
    open_task(db, reason_code="liveness_review", job=job, url=job.url, hold=False,
              title=f"Can't confirm whether this posting is open: {job.title}",
              detail=f"{job.liveness_failures} consecutive liveness checks were inconclusive "
                     f"(last: {observation.signal}: {observation.detail}). Nothing was closed; please check the posting.")
    return "review_opened"


def record_observation(db: Session, settings: Settings, job: Job, observation: Observation,
                       ctx: CheckContext) -> LivenessCheck:
    """Apply the liveness policy to one observation and store the evidence."""
    now = ctx.now or datetime.now(UTC)
    if observation.outcome == LIVE:
        action = _on_live(db, settings, job, now)
    elif observation.outcome == GONE:
        action = _on_gone(db, settings, job, observation, now)
    else:
        action = _on_unknown(db, settings, job, observation, now)
    if observation.outcome != UNKNOWN:
        job.liveness_failures = 0 if observation.outcome == LIVE else (job.liveness_failures or 0)
        job.liveness_checked_at = now
    job.liveness_reason = f"{observation.signal}: {observation.detail}"[:500]
    check = LivenessCheck(job_id=job.id, trigger=ctx.trigger, outcome=observation.outcome, signal=observation.signal,
                          http_status=observation.http_status, detail=observation.detail[:1000], action=action,
                          checked_at=now)
    db.add(check)
    db.commit()
    return check


def check_job(db: Session, settings: Settings, job: Job, ctx: CheckContext) -> LivenessCheck:
    observation = (ctx.probe or (lambda item: probe_posting(item, timeout=settings.liveness_timeout)))(job)
    return record_observation(db, settings, job, observation, ctx)


def ensure_live(db: Session, settings: Settings, job: Job, *, action: str,
                ctx: CheckContext | None = None) -> LivenessDecision:
    """Gate before preparing (``action="prepare"``) or dry-running (``"dry_run"``).

    Uses a recent ``live`` result when fresh enough, otherwise checks now.
    Only a confirmed-live posting is allowed through; suspect/unknown postings
    are deferred (never closed on that basis) and closed ones refused.
    """
    now = (ctx.now if ctx else None) or datetime.now(UTC)
    max_age = (timedelta(hours=settings.liveness_max_age_prepare_hours) if action == "prepare"
               else timedelta(minutes=settings.liveness_max_age_dry_run_minutes))
    checked = _utc(job.liveness_checked_at)
    if job.liveness == LIVE and checked is not None and now - checked <= max_age:
        return LivenessDecision(True, LIVE, "cached", "recently confirmed live")
    check = check_job(db, settings, job, CheckContext(f"pre_{action}", ctx.probe if ctx else None, now))
    if check.outcome == LIVE:
        return LivenessDecision(True, LIVE, check.action, check.detail)
    return LivenessDecision(False, job.liveness or UNKNOWN, check.action, f"{check.signal}: {check.detail}")


def due_jobs(db: Session, now: datetime, limit: int) -> list[Job]:
    """Queued postings whose next liveness check is due (never-checked first)."""
    stages = [stage.value for stage in QUEUED_STAGES]
    query = (
        select(Job)
        .where(Job.stage.in_(stages), or_(Job.liveness_next_check_at.is_(None), Job.liveness_next_check_at <= now))
        .order_by(Job.liveness_next_check_at.is_(None).desc(), Job.liveness_next_check_at, Job.discovered_at)
        .limit(max(limit, 0))
    )
    return list(db.execute(query).scalars())


def run_due_checks(db: Session, settings: Settings, ctx: CheckContext | None = None) -> dict[str, Any]:
    now = (ctx.now if ctx else None) or datetime.now(UTC)
    scheduled = CheckContext("scheduled", ctx.probe if ctx else None, now)
    summary: dict[str, Any] = {"checked": 0, "live": 0, "gone": 0, "unknown": 0, "closed": [], "suspect": []}
    for job in due_jobs(db, now, settings.cycle_max_liveness):
        check = check_job(db, settings, job, scheduled)
        summary["checked"] += 1
        summary[check.outcome] = summary.get(check.outcome, 0) + 1
        if check.action == "closed":
            summary["closed"].append(job.id)
        elif check.action == "suspect":
            summary["suspect"].append(job.id)
    return summary


def hint_missing_from_feed(db: Session, source: str, board: str, seen_canonical_ids: set[str],
                           now: datetime | None = None) -> int:
    """A board fetched completely no longer lists some queued postings: check them soon.

    Absence from a feed is only a hint (feeds paginate, filter and lag); it
    schedules an immediate liveness check and never closes anything itself.
    """
    now = now or datetime.now(UTC)
    stages = [stage.value for stage in QUEUED_STAGES]
    rows = db.execute(select(Job).where(Job.source == source, Job.board == board, Job.stage.in_(stages))).scalars()
    hinted = 0
    for job in rows:
        if job.canonical_id in seen_canonical_ids:
            continue
        due = _utc(job.liveness_next_check_at)
        if due is None or due > now:
            job.liveness_next_check_at = now
            hinted += 1
    db.commit()
    return hinted


def recent_checks(db: Session, job: Job, limit: int = 5) -> list[LivenessCheck]:
    return list(db.execute(
        select(LivenessCheck).where(LivenessCheck.job_id == job.id).order_by(LivenessCheck.checked_at.desc()).limit(limit)
    ).scalars())
