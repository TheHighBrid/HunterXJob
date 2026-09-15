from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapter_runtime import ADAPTER_CATALOG, GREENHOUSE_FIXTURE, detect_platform, plan_for_html
from app.ai import LocalAI
from app.config import Settings
from app.decisioning import Decision, DecisionContext, JobFacts, evaluate_job
from app.evidence import record_evidence
from app.flags import is_enabled, kill_switch_engaged
from app.models import AdapterMaturity, Application, Job, PipelineEvent, PipelineStage
from app.review_queue import open_task
from app.state_machine import assert_transition
from app.vault_store import load_vault


@dataclass(slots=True)
class EligibilityResult:
    eligible: bool
    reason: str
    score: float
    decision: Decision
    report: dict[str, object] | None = None


def deterministic_gate(job: Job, settings: Settings) -> EligibilityResult:
    report = evaluate_job(
        JobFacts(
            title=job.title or "",
            company=job.company or "",
            location=job.location or "",
            description=job.description or "",
            remote=bool(job.remote),
            url=job.url or "",
        ),
        DecisionContext(
            target_locations=tuple(settings.target_location_list),
            target_keywords=tuple(settings.target_keyword_list),
            excluded_locations=tuple(settings.excluded_location_list),
            excluded_titles=tuple(settings.excluded_title_list),
            blacklisted_companies=tuple(settings.blacklisted_company_list),
            shortlist_threshold=float(settings.min_match_score),
        ),
    )
    report_payload: dict[str, object] = {
        "decision": report.decision.value,
        "matched_keywords": list(report.matched_keywords),
        "review_flags": list(report.review_flags),
        "vetoes": list(report.vetoes),
        "dimensions": [
            {
                "name": dimension.name,
                "score": dimension.score,
                "weight": dimension.weight,
                "weighted_points": dimension.weighted_points,
                "evidence": list(dimension.evidence),
            }
            for dimension in report.dimensions
        ],
    }
    return EligibilityResult(
        eligible=report.decision is Decision.SHORTLIST,
        reason=report.reason,
        score=report.score,
        decision=report.decision,
        report=report_payload,
    )


def transition(db: Session, job: Job, to_stage: PipelineStage, message: str, payload: dict | None = None) -> None:
    previous = job.stage
    assert_transition(previous, to_stage.value)
    job.stage = to_stage.value
    db.add(PipelineEvent(
        job_id=job.id,
        from_stage=previous,
        to_stage=to_stage.value,
        message=message,
        payload_json=json.dumps(payload) if payload else None,
    ))
    db.add(job)
    db.commit()


def score_pending_jobs(db: Session, settings: Settings, resume_facts: str, use_ai: bool = True) -> int:
    jobs = list(db.execute(select(Job).where(Job.stage.in_([
        PipelineStage.discovered.value,
        PipelineStage.normalized.value,
    ]))).scalars())
    ai = LocalAI(settings)
    processed = 0

    for job in jobs:
        job.platform = job.platform or detect_platform(job.url)
        result = deterministic_gate(job, settings)
        job.eligible = result.eligible
        job.eligibility_reason = result.reason
        job.deterministic_score = result.score

        if result.decision is Decision.REJECT:
            job.final_score = result.score
            transition(db, job, PipelineStage.rejected, result.reason, result.report)
            processed += 1
            continue

        if result.decision is Decision.REVIEW:
            job.final_score = result.score
            transition(db, job, PipelineStage.review, result.reason, result.report)
            open_task(
                db,
                reason_code="decision_review",
                title=f"Review {job.title} at {job.company}",
                detail=result.reason,
                job=job,
                url=job.url,
            )
            processed += 1
            continue

        transition(db, job, PipelineStage.eligible, result.reason, result.report)
        ai_score = None
        evaluation: dict[str, object] = {}
        if use_ai:
            try:
                evaluation = ai.evaluate_job(resume_facts, f"{job.title}\n{job.company}\n{job.location}\n{job.description}")
                ai_score = float(evaluation.get("score", 0))
            except Exception as exc:
                evaluation = {"error": str(exc)}

        job.ai_score = ai_score
        job.final_score = round(result.score if ai_score is None else (result.score * 0.45 + ai_score * 0.55), 2)
        transition(db, job, PipelineStage.scored, "job scored", evaluation)

        if job.final_score >= settings.min_match_score:
            transition(db, job, PipelineStage.shortlisted, "score above threshold")
            existing = db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none()
            if existing is None:
                db.add(Application(
                    job_id=job.id,
                    mode=settings.application_mode,
                    adapter_name=job.platform,
                    adapter_maturity=(ADAPTER_CATALOG.get(job.platform or "generic").maturity.value if job.platform in ADAPTER_CATALOG else AdapterMaturity.dry_run.value),
                ))
                db.commit()
        else:
            transition(db, job, PipelineStage.rejected, "score below threshold")
        processed += 1
    return processed


def generate_materials(db: Session, settings: Settings, application_id: str, resume_facts: str) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise ValueError("application not found")
    job = application.job
    application.attempts += 1
    application.last_error = None
    try:
        if job.stage in {PipelineStage.shortlisted.value, PipelineStage.approved.value}:
            transition(db, job, PipelineStage.preparing, "preparing application materials")
        materials = LocalAI(settings).draft_materials(
            resume_facts,
            f"{job.title}\n{job.company}\n{job.location}\n{job.description}",
        )
        application.cover_letter_text = str(materials.get("cover_letter", ""))
        application.answers_json = json.dumps(materials.get("screening_answers", {}))
        application.stage = PipelineStage.materials_generated.value
        transition(db, job, PipelineStage.materials_generated, "application materials generated")
    except Exception as exc:
        application.last_error = str(exc)
        application.stage = PipelineStage.failed.value
        transition(db, job, PipelineStage.failed, "material generation failed", {"error": str(exc)})
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def approve_application(db: Session, application_id: str) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise ValueError("application not found")
    job = application.job
    if job.stage == PipelineStage.shortlisted.value:
        transition(db, job, PipelineStage.approved, "owner approved application")
    has_materials = bool(application.cover_letter_text or application.answers_json)
    if has_materials and job.stage in {
        PipelineStage.approved.value,
        PipelineStage.materials_generated.value,
        PipelineStage.materials_reviewed.value,
    }:
        if job.stage == PipelineStage.materials_generated.value:
            transition(db, job, PipelineStage.materials_reviewed, "materials accepted by owner")
        application.stage = PipelineStage.ready_to_apply.value
        if job.stage in {PipelineStage.approved.value, PipelineStage.materials_reviewed.value}:
            transition(db, job, PipelineStage.ready_to_apply, "ready for bounded apply cycle")
        db.add(application)
        db.commit()
    db.refresh(application)
    return application


def execute_apply(db: Session, settings: Settings, application_id: str, html: str | None = None) -> dict[str, object]:
    application = db.get(Application, application_id)
    if application is None:
        raise ValueError("application not found")
    job = application.job
    if kill_switch_engaged(db):
        raise RuntimeError("global kill switch is engaged")
    if not settings.automation_enabled:
        raise RuntimeError("automation is disabled")
    if job.stage not in {
        PipelineStage.ready_to_apply.value,
        PipelineStage.validated.value,
        PipelineStage.needs_review.value,
    }:
        raise RuntimeError(f"job stage {job.stage} is not ready to apply")

    platform = job.platform or detect_platform(job.url)
    info = ADAPTER_CATALOG.get(platform)
    if info is None:
        raise RuntimeError(f"no adapter catalog entry for {platform}")
    if not is_enabled(db, info.feature_flag):
        raise RuntimeError(f"adapter {platform} is feature-flagged off")

    snapshot = html if html is not None else GREENHOUSE_FIXTURE
    vault = load_vault(db)
    handoff, plan = plan_for_html(snapshot, vault)

    application.adapter_name = platform
    application.adapter_maturity = info.maturity.value
    application.attempts += 1
    transition(db, job, PipelineStage.applying, f"apply cycle started on {platform}")
    application.stage = PipelineStage.applying.value

    if handoff:
        open_task(
            db,
            reason_code=handoff,
            title=f"{handoff} on {job.title}",
            detail="Automatic apply stopped at a manual-handoff boundary.",
            application=application,
            job=job,
            url=job.url,
        )
        return {"status": "needs_review", "reason": handoff, "application_id": application.id}

    if not plan.ready:
        reason = plan.blockers[0] if plan.blockers else "ambiguous_question"
        open_task(
            db,
            reason_code=reason,
            title=f"Missing answers for {job.title}",
            detail=", ".join(plan.blockers),
            application=application,
            job=job,
            url=job.url,
        )
        return {"status": "needs_review", "reason": reason, "blockers": plan.blockers, "application_id": application.id}

    filled = {item.control.key: item.value for item in plan.items if item.status == "fill"}
    application.answers_json = json.dumps(filled)
    application.stage = PipelineStage.form_filled.value
    transition(db, job, PipelineStage.form_filled, "form fields resolved from vault")
    application.validation_json = json.dumps({"ok": True, "fields": list(filled)})
    application.stage = PipelineStage.validated.value
    transition(db, job, PipelineStage.validated, "dry-run validation passed")

    live_allowed = (
        settings.application_mode == "autonomous"
        and settings.allow_live_submission
        and info.maturity is AdapterMaturity.certified_autonomous
        and is_enabled(db, "allow_live_submission")
        and is_enabled(db, "unattended_mode")
    )
    if live_allowed:
        record_evidence(
            db,
            application,
            kind="submission",
            confirmation_text="live submission attempted without certification",
            final_url=job.url,
            payload=filled,
            adapter_name=platform,
        )
        return {"status": application.stage, "application_id": application.id}

    record_evidence(
        db,
        application,
        kind="dry_run",
        confirmation_text="dry-run complete; submit button was not clicked",
        final_url=job.url,
        payload=filled,
        adapter_name=platform,
    )
    application.stage = PipelineStage.validated.value
    db.add(application)
    db.commit()
    return {
        "status": "dry_run_complete",
        "application_id": application.id,
        "adapter": platform,
        "maturity": info.maturity.value,
        "filled": list(filled),
        "submitted": False,
    }
