"""Owner workflow for application materials: generate drafts, approve, reject.

* Generating needs a ready verified profile; it creates new draft versions,
  never touches approved ones, and opens a ``materials_review`` task without
  holding the job (the task is the owner's prompt to look).
* Approving checks that the draft is intact (hashes) and that every fact it
  uses is still verified. Once nothing is pending and the résumé is
  approved, a job waiting in ``materials_generated`` moves on to
  ``ready_to_apply``, where only a *dry-run* can happen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dedup import duplicate_guard
from app.flags import kill_switch_engaged
from app.material_llm import LLMClient, llm_client, reword_cover_letter, reword_resume
from app.material_store import (
    APPROVED,
    DRAFT,
    REJECTED,
    MaterialsError,
    create_draft,
    decide,
    get_material,
    integrity_problem,
    materials_ready,
    pending_drafts,
    stale_facts,
    supersede_drafts,
)
from app.materials import JobContext, build_cover_letter, build_resume, cover_letter_text, resume_text
from app.models import Application, ApplicationMaterial, Job, PipelineStage, ReviewTask
from app.pipeline import approve_application, transition
from app.profile import VerifiedProfile, readiness, verified_profile
from app.review_queue import open_task
from app.truth_guard import check_cover_letter, check_resume

REVIEW_REASON = "materials_review"
GENERATABLE_STAGES = frozenset({
    PipelineStage.shortlisted.value, PipelineStage.approved.value, PipelineStage.preparing.value,
    PipelineStage.materials_generated.value, PipelineStage.materials_reviewed.value,
    PipelineStage.ready_to_apply.value, PipelineStage.needs_review.value, PipelineStage.validated.value,
})
_LOCKED_APPLICATION_STAGES = frozenset({
    PipelineStage.submitted.value, PipelineStage.confirmed.value, PipelineStage.submission_uncertain.value,
    PipelineStage.applying.value,
})
_USE_SETTINGS: Any = object()


def job_context(job: Job) -> JobContext:
    return JobContext(title=job.title or "", company=job.company or "", location=job.location or "",
                      description=job.description or "")


def _fact_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = {value["fact"]} if isinstance(value.get("fact"), str) else set()
        keys |= set(value.get("facts", [])) if isinstance(value.get("facts"), list) else set()
        return keys.union(*[_fact_keys(item) for item in value.values()])
    if isinstance(value, list):
        return set().union(*[_fact_keys(item) for item in value]) if value else set()
    return set()


def _name_facts(profile: VerifiedProfile) -> set[str]:
    return {fact.key for fact in profile.of("contact")
            if fact.data["field"] in {"first_name", "last_name", "preferred_name"}}


def _meta(report: dict[str, Any], used: set[str]) -> dict[str, Any]:
    return {
        "generator": "llm" if report.get("status") == "accepted" else "template",
        "llm": report,
        "guard": {"status": "passed", "rule": "every line maps to a verified fact; numbers, dates and names match"},
        "facts_used": sorted(used),
    }


def _build(profile: VerifiedProfile, job: JobContext, client: LLMClient | None) -> list[tuple[str, dict[str, Any], str, dict[str, Any]]]:
    resume = build_resume(profile, job)
    letter = build_cover_letter(profile, job, resume)
    # The deterministic baseline must pass the guard too; if it does not, refuse.
    problems = check_resume(resume, profile) + check_cover_letter(letter, profile, job_description=job.text)
    if problems:
        raise MaterialsError(500, f"generated material failed the truthfulness guard: {problems[0]}")
    resume_final, resume_report = reword_resume(resume, profile, client)
    letter_final, letter_report = reword_cover_letter(letter, profile, client, job.text)
    names = _name_facts(profile)
    return [
        ("resume", resume_final, resume_text(resume_final), _meta(resume_report, _fact_keys(resume_final) | names)),
        ("cover_letter", letter_final, cover_letter_text(letter_final),
         _meta(letter_report, _fact_keys(letter_final) | _fact_keys(resume_final.get("contact", [])) | names)),
    ]


def _review_detail(job: Job, rows: list[ApplicationMaterial]) -> str:
    lines = [f"New drafts for {job.title} at {job.company}. Nothing is attached to any form until you approve it."]
    lines += [f"- {row.kind} v{row.version} ({row.generator}) sha256 {row.content_sha256[:16]}…" for row in rows]
    return "\n".join(lines)


def _open_review(db: Session, application: Application, job: Job, rows: list[ApplicationMaterial]) -> None:
    task = db.execute(select(ReviewTask).where(
        ReviewTask.application_id == application.id, ReviewTask.reason_code == REVIEW_REASON,
        ReviewTask.status == "open")).scalars().first()
    if task is not None:
        task.detail = _review_detail(job, rows)
        db.commit()
        return
    open_task(db, reason_code=REVIEW_REASON, title=f"Approve résumé and cover letter: {job.title} at {job.company}",
              detail=_review_detail(job, rows), application=application, job=job, url=job.url, hold=False)


def generate_for_application(db: Session, settings: Settings, application_id: str, *,
                             client: Any = _USE_SETTINGS) -> list[ApplicationMaterial]:
    """Create new draft versions of the résumé and cover letter for one application."""
    application = db.get(Application, application_id)
    if application is None:
        raise MaterialsError(404, "application not found")
    job = application.job
    if job.stage not in GENERATABLE_STAGES or application.stage in _LOCKED_APPLICATION_STAGES:
        raise MaterialsError(409, f"materials cannot be generated while the job is {job.stage}")
    duplicate = duplicate_guard(db, job, "prepare")
    if duplicate:
        raise MaterialsError(409, duplicate)
    profile = verified_profile(db)
    missing = readiness(profile)
    if missing:
        raise MaterialsError(409, "profile is not ready: " + "; ".join(missing))
    built = _build(profile, job_context(job), llm_client(settings) if client is _USE_SETTINGS else client)
    supersede_drafts(db, application.id)
    if job.stage in {PipelineStage.shortlisted.value, PipelineStage.approved.value}:
        transition(db, job, PipelineStage.preparing, "preparing application materials")
    rows = [create_draft(db, settings, application, kind=kind, document=document, text=text, meta=meta,
                         profile_sha256=profile.sha256) for kind, document, text, meta in built]
    application.last_error = None
    if job.stage == PipelineStage.preparing.value:
        application.stage = PipelineStage.materials_generated.value
        transition(db, job, PipelineStage.materials_generated, "draft materials generated; awaiting owner approval",
                   {"materials": [row.id for row in rows]})
    db.add(application)
    db.commit()
    _open_review(db, application, job, rows)
    return rows


def _application(db: Session, row: ApplicationMaterial) -> Application:
    application = db.get(Application, row.application_id)
    if application is None:  # pragma: no cover - foreign key
        raise MaterialsError(404, "application not found")
    if application.stage in _LOCKED_APPLICATION_STAGES:
        raise MaterialsError(409, f"materials are locked while the application is {application.stage}")
    return application


def _check_approvable(db: Session, row: ApplicationMaterial) -> None:
    if row.status != DRAFT:
        raise MaterialsError(409, f"only a draft can be approved (this version is {row.status})")
    problem = integrity_problem(row)
    if problem:
        raise MaterialsError(409, f"cannot approve: {problem}; regenerate it")
    stale = stale_facts(db, row)
    if stale:
        raise MaterialsError(409, f"cannot approve: uses facts that are no longer verified ({', '.join(stale)}); regenerate it")


def _after_decision(db: Session, application: Application) -> None:
    if pending_drafts(db, application.id):
        return
    ready = materials_ready(db, application)
    for task in db.execute(select(ReviewTask).where(
            ReviewTask.application_id == application.id, ReviewTask.reason_code == REVIEW_REASON,
            ReviewTask.status == "open")).scalars():
        task.status = "approved" if ready else "resolved"
        task.resolved_at = datetime.now(UTC)
    db.commit()
    if ready and application.job.stage == PipelineStage.materials_generated.value and not kill_switch_engaged(db):
        approve_application(db, application.id)


def approve_material(db: Session, material_id: str, note: str = "") -> ApplicationMaterial:
    row = get_material(db, material_id)
    application = _application(db, row)
    _check_approvable(db, row)
    decide(db, application, row, APPROVED, note)
    _after_decision(db, application)
    db.refresh(row)
    return row


def reject_material(db: Session, material_id: str, note: str = "") -> ApplicationMaterial:
    row = get_material(db, material_id)
    application = _application(db, row)
    if row.status not in {DRAFT, APPROVED}:
        raise MaterialsError(409, f"this version is already {row.status}")
    decide(db, application, row, REJECTED, note)
    _after_decision(db, application)
    db.refresh(row)
    return row


def approve_pending(db: Session, application: Application) -> list[str]:
    """Approve every pending draft of an application (review-task approval). Returns approved ids."""
    rows = pending_drafts(db, application.id)
    for row in rows:
        _check_approvable(db, row)
    for row in rows:
        decide(db, application, row, APPROVED, "approved from the review queue")
    return [row.id for row in rows]
