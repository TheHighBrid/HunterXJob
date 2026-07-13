from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import LocalAI
from app.config import Settings
from app.models import Application, Job, PipelineEvent, PipelineStage


@dataclass(slots=True)
class EligibilityResult:
    eligible: bool
    reason: str
    score: float


def _contains_any(text: str, values: list[str]) -> bool:
    haystack = text.lower()
    return any(value.lower() in haystack for value in values)


def deterministic_gate(job: Job, settings: Settings) -> EligibilityResult:
    title = job.title or ""
    location = job.location or ""
    company = job.company or ""
    combined = f"{title}\n{location}\n{job.description or ''}"

    if _contains_any(company, settings.blacklisted_company_list):
        return EligibilityResult(False, "blacklisted company", 0)
    if _contains_any(title, settings.excluded_title_list):
        return EligibilityResult(False, "excluded title", 0)
    if _contains_any(location, settings.excluded_location_list) and not _contains_any(location, settings.target_location_list):
        return EligibilityResult(False, "excluded location", 0)

    location_score = 20 if _contains_any(location, settings.target_location_list) or job.remote else 0
    keyword_hits = sum(1 for keyword in settings.target_keyword_list if keyword.lower() in combined.lower())
    keyword_score = min(50, keyword_hits * 8)
    banking_score = 15 if _contains_any(combined, ["bank", "financial", "fintech", "payments", "credit"]) else 0
    bilingual_score = 10 if _contains_any(combined, ["bilingual", "french", "français"]) else 0
    score = min(100, location_score + keyword_score + banking_score + bilingual_score)

    if location_score == 0:
        return EligibilityResult(False, "location not eligible", score)
    if keyword_hits == 0:
        return EligibilityResult(False, "no target-role overlap", score)
    return EligibilityResult(True, "passed deterministic eligibility", score)


def transition(db: Session, job: Job, to_stage: PipelineStage, message: str, payload: dict | None = None) -> None:
    previous = job.stage
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
        result = deterministic_gate(job, settings)
        job.eligible = result.eligible
        job.eligibility_reason = result.reason
        job.deterministic_score = result.score

        if not result.eligible:
            job.final_score = result.score
            transition(db, job, PipelineStage.rejected, result.reason)
            processed += 1
            continue

        transition(db, job, PipelineStage.eligible, result.reason)
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
                db.add(Application(job_id=job.id, mode=settings.application_mode))
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
