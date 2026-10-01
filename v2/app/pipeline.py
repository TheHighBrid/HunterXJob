from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapter_runtime import ADAPTER_CATALOG, AdapterInfo, platform_for_job
from app.ai import AI_FAILURES, LocalAI
from app.answer_vault import AnswerVault
from app.config import Settings
from app.decisioning import Decision, DecisionContext, JobFacts, evaluate_job
from app.dedup import RELEASING_STAGES, duplicate_guard, release_duplicates
from app.evidence import record_evidence
from app.flags import is_enabled, kill_switch_engaged
from app.form_engine import FillPlan, FormControl, detect_handoff, parse_controls, plan_fill
from app.greenhouse_form import FormFetchError, RealForm
from app.job_liveness import ensure_live
from app.material_store import manifest, materials_ready, vault_records
from app.models import AdapterMaturity, Application, Job, PipelineEvent, PipelineStage
from app.review_queue import open_task
from app.state_machine import assert_transition
from app.vault_store import load_vault

if TYPE_CHECKING:
    from app.real_forms import FormProvider


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
    if to_stage.value in RELEASING_STAGES:
        release_duplicates(db, job, to_stage.value)


def _route_non_shortlist(db: Session, job: Job, result: EligibilityResult) -> bool:
    """Handle deterministic reject/review outcomes. Returns True if the job was routed."""
    if result.decision is Decision.REJECT:
        job.final_score = result.score
        transition(db, job, PipelineStage.rejected, result.reason, result.report)
        return True
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
        return True
    return False


def _ai_evaluation(ai: LocalAI | None, resume_facts: str, job: Job) -> tuple[float | None, dict[str, object]]:
    if ai is None:
        return None, {}
    try:
        evaluation = ai.evaluate_job(resume_facts, f"{job.title}\n{job.company}\n{job.location}\n{job.description}")
        return float(evaluation.get("score", 0)), evaluation
    except AI_FAILURES as exc:
        return None, {"error": str(exc)}


def shortlist_job(db: Session, settings: Settings, job: Job, message: str = "score above threshold") -> None:
    """Move a job to shortlisted and make sure it has a (dry-run mode) application."""
    transition(db, job, PipelineStage.shortlisted, message)
    existing = db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none()
    if existing is not None:
        return
    info = ADAPTER_CATALOG.get(job.platform or "")
    db.add(Application(
        job_id=job.id,
        mode=settings.application_mode,
        adapter_name=job.platform,
        adapter_maturity=info.maturity.value if info else AdapterMaturity.dry_run.value,
    ))
    db.commit()


def score_pending_jobs(db: Session, settings: Settings, resume_facts: str, use_ai: bool = True, limit: int | None = None) -> int:
    """Gate and score discovered jobs. ``limit`` caps how many are processed in one call."""
    query = select(Job).where(Job.stage.in_([
        PipelineStage.discovered.value,
        PipelineStage.normalized.value,
    ])).order_by(Job.discovered_at)
    if limit is not None:
        query = query.limit(max(limit, 0))
    jobs = list(db.execute(query).scalars())
    ai = LocalAI(settings) if use_ai else None
    processed = 0

    for job in jobs:
        processed += 1
        job.platform = platform_for_job(job)
        result = deterministic_gate(job, settings)
        job.eligible = result.eligible
        job.eligibility_reason = result.reason
        job.deterministic_score = result.score
        if _route_non_shortlist(db, job, result):
            continue

        transition(db, job, PipelineStage.eligible, result.reason, result.report)
        ai_score, evaluation = _ai_evaluation(ai, resume_facts, job)
        job.ai_score = ai_score
        job.final_score = round(result.score if ai_score is None else (result.score * 0.45 + ai_score * 0.55), 2)
        transition(db, job, PipelineStage.scored, "job scored", evaluation)

        if job.final_score >= settings.min_match_score:
            shortlist_job(db, settings, job)
        else:
            transition(db, job, PipelineStage.rejected, "score below threshold")
    return processed


def approve_application(db: Session, application_id: str) -> Application:
    application = db.get(Application, application_id)
    if application is None:
        raise ValueError("application not found")
    job = application.job
    if job.stage == PipelineStage.shortlisted.value:
        transition(db, job, PipelineStage.approved, "owner approved application")
    # Only an owner-approved résumé (intact, all facts still verified) counts.
    has_materials = materials_ready(db, application)
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


def _blocker_detail(plan: FillPlan) -> str:
    lines = []
    for item in plan.items:
        if item.status != "review":
            continue
        required = "required" if item.control.required else "optional"
        lines.append(f"- [{item.control.section}] {item.control.label or item.control.key} ({item.control.key}, {required}): {item.reason}")
    return "\n".join(lines)


def preview_form(db: Session, form: RealForm) -> tuple[FillPlan, dict[str, object]]:
    """Plan a real form against the vault without changing any state."""
    vault = load_vault(db, form.vault_scopes)
    plan = plan_fill(form.controls, vault)
    return plan, form.summary()


_APPLY_READY_STAGES = frozenset({
    PipelineStage.ready_to_apply.value,
    PipelineStage.validated.value,
    PipelineStage.needs_review.value,
})


@dataclass(slots=True)
class _LoadedForm:
    controls: list[FormControl]
    handoff: str | None
    vault: AnswerVault
    summary: dict[str, object]


def _apply_adapter(db: Session, settings: Settings, job: Job) -> tuple[str, AdapterInfo]:
    """Enforce every precondition for an apply cycle; return (platform, catalog entry)."""
    if kill_switch_engaged(db):
        raise RuntimeError("global kill switch is engaged")
    if not settings.automation_enabled:
        raise RuntimeError("automation is disabled")
    if job.stage not in _APPLY_READY_STAGES:
        raise RuntimeError(f"job stage {job.stage} is not ready to apply")
    platform = platform_for_job(job)
    info = ADAPTER_CATALOG.get(platform)
    if info is None:
        raise RuntimeError(f"no adapter catalog entry for {platform}")
    if not is_enabled(db, info.feature_flag):
        raise RuntimeError(f"adapter {platform} is feature-flagged off")
    duplicate = duplicate_guard(db, job, "dry_run")
    if duplicate:
        raise RuntimeError(duplicate)
    return platform, info


def _with_materials(db: Session, application: Application, vault: AnswerVault) -> AnswerVault:
    """Offer only owner-approved material versions to résumé/cover-letter fields."""
    for record in vault_records(db, application):
        vault.put(record)
    return vault


def _load_form(db: Session, settings: Settings, application: Application, html: str | None,
               form_provider: FormProvider | None) -> _LoadedForm:
    """Load the form to plan against. Raises FormFetchError if the real form is unavailable."""
    job = application.job
    if html is not None:
        controls = parse_controls(html)
        summary: dict[str, object] = {"source": "supplied_snapshot", "url": job.url, "fields_total": len(controls), "warnings": []}
        return _LoadedForm(controls, detect_handoff(html), _with_materials(db, application, load_vault(db)), summary)
    if form_provider is None:
        from app.real_forms import LiveFormProvider

        form_provider = LiveFormProvider(settings)
    form = form_provider.fetch(job)
    vault = _with_materials(db, application, load_vault(db, form.vault_scopes))
    return _LoadedForm(form.controls, form.handoff, vault, form.summary())


def _form_unavailable(db: Session, application: Application, job: Job, exc: FormFetchError) -> dict[str, object]:
    application.last_error = exc.detail
    db.add(application)
    db.commit()
    open_task(
        db,
        reason_code=exc.reason_code,
        title=f"Could not load the real application form for {job.title}",
        detail=f"{exc.detail}. The dry-run was not performed; no sample form was used.",
        application=application,
        job=job,
        url=job.url,
    )
    return {
        "status": "needs_review",
        "reason": exc.reason_code,
        "detail": exc.detail,
        "application_id": application.id,
        "form_source": None,
        "submitted": False,
    }


def _record_blockers(application: Application, form: _LoadedForm, blockers: list[str], plan: FillPlan | None) -> None:
    """Keep a structured copy of why the dry-run stopped (shown in the job detail)."""
    blocked = [] if plan is None else [{
        "key": item.control.key,
        "label": item.control.label,
        "section": item.control.section,
        "required": item.control.required,
        "reason": item.reason,
    } for item in plan.items if item.status == "review"]
    application.validation_json = json.dumps(
        {"ok": False, "form": form.summary, "blockers": blockers, "blocked_fields": blocked}, default=str
    )


def _stop_for_review(db: Session, application: Application, job: Job, plan: FillPlan, form: _LoadedForm) -> dict[str, object] | None:
    """Open a review task and return the result if the form cannot be completed automatically."""
    if form.handoff:
        _record_blockers(application, form, [form.handoff], None)
        open_task(
            db,
            reason_code=form.handoff,
            title=f"{form.handoff} on {job.title}",
            detail="Automatic apply stopped at a manual-handoff boundary.",
            application=application,
            job=job,
            url=job.url,
        )
        return {"status": "needs_review", "reason": form.handoff, "application_id": application.id, "form": form.summary, "submitted": False}
    if plan.ready:
        return None
    reason = plan.blockers[0] if plan.blockers else "ambiguous_question"
    _record_blockers(application, form, list(plan.blockers) or [reason], plan)
    open_task(
        db,
        reason_code=reason,
        title=f"Missing answers for {job.title}",
        detail=f"Blockers: {', '.join(plan.blockers)}\n{_blocker_detail(plan)}",
        application=application,
        job=job,
        url=job.url,
    )
    return {
        "status": "needs_review",
        "reason": reason,
        "blockers": plan.blockers,
        "blocked_fields": [item.control.key for item in plan.items if item.status == "review"],
        "application_id": application.id,
        "form": form.summary,
        "submitted": False,
    }


def _complete_dry_run(db: Session, application: Application, job: Job, plan: FillPlan, form_summary: dict[str, object],
                      platform: str) -> tuple[list[str], dict[str, object]]:
    filled = {item.control.key: item.value for item in plan.items if item.status == "fill"}
    # Which approved material versions would be attached (drafts never are).
    materials = manifest(db, application)
    application.answers_json = json.dumps(filled)
    application.stage = PipelineStage.form_filled.value
    transition(db, job, PipelineStage.form_filled, "form fields resolved from vault")
    application.validation_json = json.dumps(
        {"ok": True, "fields": list(filled), "form": form_summary, "materials": materials}, default=str
    )
    application.stage = PipelineStage.validated.value
    transition(db, job, PipelineStage.validated, "dry-run validation passed")

    # This pipeline only resolves and validates forms. It does not invoke a
    # platform adapter's submit operation, so it must never create submission
    # evidence merely because configuration flags say live submission is allowed.
    # A future certified adapter must perform the real submission and collect
    # confirmation evidence before the application can enter submitted/confirmed.
    record_evidence(
        db,
        application,
        kind="dry_run",
        confirmation_text=f"dry-run complete against {form_summary['source']} form; submit button was not clicked",
        final_url=job.url,
        payload={"filled": filled, "form_source": form_summary["source"], "materials": materials},
        adapter_name=platform,
    )
    application.stage = PipelineStage.validated.value
    db.add(application)
    db.commit()
    return list(filled), materials


def _liveness_refusal(db: Session, settings: Settings, application: Application) -> dict[str, object] | None:
    """Only a posting confirmed live is dry-run; suspect/unknown ones wait, confirmed-closed ones close.

    The cycle checks first with its own probe, so this normally reuses that fresh result.
    """
    job = application.job
    gate = ensure_live(db, settings, job, action="dry_run")
    if gate.allowed:
        return None
    return {
        "status": "closed" if job.stage == PipelineStage.closed.value else "deferred",
        "reason": f"liveness_{gate.status}",
        "detail": gate.reason,
        "application_id": application.id,
        "form_source": None,
        "submitted": False,
    }


def execute_apply(
    db: Session,
    settings: Settings,
    application_id: str,
    html: str | None = None,
    form_provider: FormProvider | None = None,
) -> dict[str, object]:
    """Run a dry-run apply cycle against the job's real application form.

    The form comes from ``form_provider`` (default: live, read-only fetch of
    the employer's posting). ``html`` lets a caller supply an explicit form
    snapshot instead (used by tests); it is labelled ``supplied_snapshot`` in
    the evidence. If the real form cannot be obtained the application goes to
    review. There is no fallback to a sample form, and nothing is submitted.
    A linked duplicate is refused, and the posting must be confirmed live
    first (suspect/unknown postings are deferred, confirmed-closed ones closed).
    """
    application = db.get(Application, application_id)
    if application is None:
        raise ValueError("application not found")
    job = application.job
    platform, info = _apply_adapter(db, settings, job)
    refusal = _liveness_refusal(db, settings, application)
    if refusal is not None:
        return refusal

    application.adapter_name = platform
    application.adapter_maturity = info.maturity.value
    application.attempts += 1

    try:
        form = _load_form(db, settings, application, html, form_provider)
    except FormFetchError as exc:
        return _form_unavailable(db, application, job, exc)

    plan = plan_fill(form.controls, form.vault)
    application.last_error = None
    application.validation_json = json.dumps({"ok": False, "form": form.summary}, default=str)
    transition(db, job, PipelineStage.applying, f"dry-run apply started on {platform} ({form.summary['source']})")
    application.stage = PipelineStage.applying.value

    review = _stop_for_review(db, application, job, plan, form)
    if review is not None:
        return review

    filled, materials = _complete_dry_run(db, application, job, plan, form.summary, platform)
    return {
        "status": "dry_run_complete",
        "application_id": application.id,
        "adapter": platform,
        "maturity": info.maturity.value,
        "form_source": form.summary["source"],
        "form": form.summary,
        "filled": filled,
        "materials": materials,
        "submitted": False,
    }
