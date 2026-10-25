from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.adapter_runtime import ADAPTER_CATALOG
from app.ai import LocalAI
from app.api_schemas import HealthOut
from app.config import Settings, get_settings
from app.dashboard_template import get_dashboard_html
from app.db import SessionLocal, get_db, init_db
from app.discovery import discover_all, upsert_jobs
from app.flags import ensure_flags, snapshot
from app.material_store import MaterialsError
from app.material_workflow import generate_for_application
from app.materials_api import router as materials_router
from app.models import AnswerPolicy, Application, PipelineEvent, ReviewTask
from app.pipeline import approve_application, execute_apply, score_pending_jobs
from app.remote_api import router as remote_router
from app.resume_facts import RESUME_FACTS_PATH, read_resume_facts
from app.runtime import runner, settings
from app.runtime_settings import apply_stored_overrides
from app.scheduler import can_run_unattended, day_start_utc, submissions_today
from app.security import API_KEY_HEADER, auth_posture, is_authorized, require_api_key
from app.vault_store import upsert_answer

DbSession = Annotated[Session, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    Path("data").mkdir(exist_ok=True)
    Path("logs").mkdir(exist_ok=True)
    db = SessionLocal()
    try:
        ensure_flags(db)
        # Phone-edited settings (safe subset only) are layered over .env.
        apply_stored_overrides(db, settings)
    finally:
        db.close()
    # One background thread runs scheduled cycles (if CONTINUOUS_RUN_ENABLED)
    # and periodic backups (if BACKUP_INTERVAL_HOURS > 0).
    background = settings.continuous_run_enabled or settings.backup_interval_hours > 0
    if background:
        runner.start()
    try:
        yield
    finally:
        if background:
            runner.stop()


# Interactive API docs expose the full schema; only serve them in local dev mode.
_docs = settings.local_dev_mode
app = FastAPI(
    title="HunterXJob v2",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _docs else None,
    # One schema per model (no "-Input"/"-Output" pairs) for the generated mobile types.
    separate_input_output_schemas=False,
)
if settings.cors_origin_list:
    # Only needed for the Expo web preview; native apps do not use CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH"],
        allow_headers=[API_KEY_HEADER, "Authorization", "Content-Type"],
    )
api = APIRouter(dependencies=[Depends(require_api_key)])


class ResumeFactsIn(BaseModel):
    text: str


class AnswerIn(BaseModel):
    key: str
    value: str
    source: str = "user"
    scope: str = "global"
    confidence: float = 1.0
    sensitive: bool = False
    note: str = ""


class VaultSimulateIn(BaseModel):
    question: str = ""
    options: list[str] = []
    control_type: str = "text"
    scope: str = "global"


class InboxEmailIn(BaseModel):
    sender: str
    recipient: str = ""
    subject: str
    body: str
    received_at: str | None = None


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return get_dashboard_html()


@app.get("/api/health", response_model=HealthOut, response_model_exclude_none=True)
def health(request: Request, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Liveness probe. Open to everyone; details only for authenticated callers."""
    posture = auth_posture(current)
    public: dict[str, object] = {"ok": True, "version": __version__, "auth": posture.mode}
    if not is_authorized(request, current):
        return public
    decision = can_run_unattended(db, current)
    return {
        **public,
        "mode": current.application_mode,
        "automation_enabled": current.automation_enabled,
        "allow_live_submission": current.allow_live_submission,
        "live_submission_locked": True,
        "continuous_run_enabled": current.continuous_run_enabled,
        "unattended_allowed": decision.allowed,
        "unattended_reason": decision.reason,
        "submissions_today": submissions_today(db, day_start_utc(current)),
        "open_review_tasks": db.execute(select(func.count(ReviewTask.id)).where(ReviewTask.status == "open")).scalar_one(),
        "flags": snapshot(db),
        "ai": LocalAI(current).health(),
        "resume_loaded": RESUME_FACTS_PATH.exists(),
    }


@api.put("/api/resume-facts")
def save_resume_facts(payload: ResumeFactsIn) -> dict[str, object]:
    RESUME_FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESUME_FACTS_PATH.write_text(payload.text.strip(), encoding="utf-8")
    return {"saved": True, "characters": len(payload.text.strip())}


@api.get("/api/answers")
def list_answers(db: DbSession, scope: str | None = None) -> list[dict[str, object]]:
    query = select(AnswerPolicy).order_by(AnswerPolicy.key.asc())
    if scope:
        query = query.where(AnswerPolicy.scope == scope)
    rows = db.execute(query).scalars().all()
    return [{
        "id": r.id,
        "key": r.key,
        "value": r.value,
        "source": r.source,
        "scope": r.scope,
        "confidence": r.confidence,
        "sensitive": r.sensitive,
        "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]


@api.post("/api/answers")
def save_answer(payload: AnswerIn, db: DbSession) -> dict[str, object]:
    row = upsert_answer(db, **payload.model_dump())
    return {"id": row.id, "key": row.key, "scope": row.scope, "source": row.source}


@api.delete("/api/answers/{answer_id}")
def delete_answer(answer_id: str, db: DbSession) -> dict[str, object]:
    row = db.get(AnswerPolicy, answer_id)
    if row is None:
        raise HTTPException(404, "answer not found")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": answer_id, "key": row.key}


@api.post("/api/vault/simulate")
def simulate_vault_matching(payload: VaultSimulateIn, db: DbSession) -> dict[str, object]:
    from app.answer_vault import FieldRequest, ResolutionStatus
    from app.form_engine import _normalize_option, _score_best_option
    from app.greenhouse_form import shared_answer_key
    from app.question_catalog import classify_question as catalog_classify
    from app.vault_store import load_vault

    question = (payload.question or "").strip()
    if not question:
        return {
            "question": "",
            "canonical_key": None,
            "status": "missing",
            "stored_value": None,
            "matched_option": None,
            "tier": None,
            "confidence": 0.0,
            "explanation": "No question provided.",
        }

    canonical_key = shared_answer_key(question)
    if not canonical_key:
        cat_match = catalog_classify(question)
        if cat_match:
            canonical_key = cat_match.get("canonical_key")
    vault = load_vault(db)

    # First try resolving via canonical key, then fallback to normalized question slug
    req_key = canonical_key or question.lower().replace(" ", "_")
    resolved = vault.resolve(FieldRequest(key=req_key))
    if resolved.status != ResolutionStatus.RESOLVED and canonical_key:
        resolved = vault.resolve(FieldRequest(key=canonical_key))

    # Evaluate options if provided
    options = [str(opt).strip() for opt in payload.options if str(opt).strip()]
    matched_option = None
    matched_tier = None
    confidence = 1.0 if resolved.status == ResolutionStatus.RESOLVED else 0.0

    if resolved.status == ResolutionStatus.RESOLVED and options:
        lookup = {_normalize_option(opt): opt for opt in options}
        exact = lookup.get(_normalize_option(resolved.value))
        if exact is not None:
            matched_option = exact
            matched_tier = "Tier 1: Exact Normalized Match"
            confidence = 1.0
        else:
            best = _score_best_option(resolved.value, options)
            if best is not None:
                matched_option = best
                matched_tier = "Tier 2/3/4: Boolean Equivalence & Token Overlap"
                confidence = 0.95
            else:
                confidence = 0.0

    if resolved.status == ResolutionStatus.RESOLVED:
        if options and not matched_option:
            status = "review"
            explanation = (
                f"Resolved value '{resolved.value}' from key '{req_key}', but none of the "
                f"{len(options)} options matched with high confidence (would route to human review)."
            )
        else:
            status = "resolved"
            explanation = f"Matched '{resolved.value}' from key '{req_key}' (source: {resolved.source.value})."
            if matched_option:
                explanation += f" Selected option '{matched_option}' via {matched_tier}."
    else:
        status = "missing"
        explanation = (
            f"Question was categorized as '{canonical_key}', but no answer is stored in the vault or verified profile."
            if canonical_key
            else "No canonical question pattern matched this text. Would route to human review."
        )

    return {
        "question": question,
        "canonical_key": canonical_key,
        "resolved_key": req_key,
        "status": status,
        "stored_value": resolved.value if resolved.status == ResolutionStatus.RESOLVED else None,
        "source": resolved.source.value if resolved.status == ResolutionStatus.RESOLVED else None,
        "options_provided": len(options),
        "matched_option": matched_option,
        "tier": matched_tier,
        "confidence": confidence,
        "explanation": explanation,
    }


@api.post("/api/discovery/run")
def run_discovery(db: DbSession) -> dict[str, object]:
    errors: list[str] = []
    records = discover_all(settings, errors)
    added = upsert_jobs(db, records)
    return {"discovered": len(records), "added": added, "errors": errors}


@api.post("/api/scoring/run")
def run_scoring(db: DbSession) -> dict[str, int]:
    resume = read_resume_facts()
    if not resume:
        raise HTTPException(409, "resume facts are not configured")
    return {"processed": score_pending_jobs(db, settings, resume, use_ai=True)}


@api.get("/api/applications")
def list_applications(db: DbSession) -> list[dict[str, object]]:
    rows = db.execute(select(Application).order_by(Application.created_at.desc())).scalars()
    return [{
        "id": a.id, "job_id": a.job_id, "stage": a.stage, "mode": a.mode,
        "cover_letter_text": a.cover_letter_text, "last_error": a.last_error,
        "attempts": a.attempts, "adapter": a.adapter_name, "maturity": a.adapter_maturity,
        "idempotency_key": a.idempotency_key,
    } for a in rows]


@api.post("/api/applications/{application_id}/generate")
def generate(application_id: str, db: DbSession) -> dict[str, object]:
    """Draft a résumé and cover letter from verified profile facts (needs owner approval)."""
    try:
        rows = generate_for_application(db, settings, application_id)
    except MaterialsError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    return {"id": application_id, "materials": [{"id": row.id, "kind": row.kind, "version": row.version} for row in rows]}


@api.post("/api/applications/{application_id}/approve")
def approve(application_id: str, db: DbSession) -> dict[str, object]:
    try:
        result = approve_application(db, application_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": result.id, "stage": result.stage, "job_stage": result.job.stage}


@api.post("/api/applications/{application_id}/apply")
def apply(application_id: str, db: DbSession) -> dict[str, object]:
    try:
        return execute_apply(db, settings, application_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@api.get("/api/adapters")
def adapters() -> list[dict[str, object]]:
    return [{
        "name": info.name,
        "maturity": info.maturity.value,
        "feature_flag": info.feature_flag,
        "notes": info.notes,
        "version": info.version,
    } for info in ADAPTER_CATALOG.values()]


@api.get("/api/events/{job_id}")
def events(job_id: str, db: DbSession) -> list[dict[str, object]]:
    rows = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job_id).order_by(PipelineEvent.created_at)).scalars()
    return [{"from": e.from_stage, "to": e.to_stage, "message": e.message, "created_at": e.created_at} for e in rows]


@api.post("/api/autonomous/run")
def trigger_autonomous_run(db: DbSession, current: CurrentSettings) -> dict[str, object]:
    from app.autonomous_loop import AutonomousConfig, AutonomousRunner

    try:
        runner = AutonomousRunner(db, current, AutonomousConfig())
        summary = runner.run()
        return {
            "started_at": summary.started_at,
            "finished_at": summary.finished_at,
            "aborted_by_kill_switch": summary.aborted_by_kill_switch,
            "candidates_considered": summary.candidates_considered,
            "shortlisted": summary.shortlisted,
            "submitted_live": summary.submitted_live,
            "verified_dry_runs": summary.verified_dry_runs,
            "skipped_duplicates": summary.skipped_duplicates,
            "errors": summary.errors,
        }
    except Exception as exc:
        raise HTTPException(409, f"autonomous run failed: {exc}") from exc


@api.get("/api/autonomous/status")
def autonomous_status(db: DbSession) -> dict[str, object]:
    from app.flags import is_enabled, kill_switch_engaged

    return {
        "kill_switch_engaged": kill_switch_engaged(db),
        "allow_live_submission": is_enabled(db, "allow_live_submission"),
        "unattended_mode": is_enabled(db, "unattended_mode"),
        "adapters_available": [k for k, v in ADAPTER_CATALOG.items() if v.maturity.value != "unsupported"],
    }


@api.get("/api/certification/dossier")
def get_certification_dossier() -> dict[str, object]:
    import json
    from app.certification_system import DEFAULT_DOSSIER_PATH, build_certification_dossier

    if DEFAULT_DOSSIER_PATH.exists():
        try:
            return json.loads(DEFAULT_DOSSIER_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return build_certification_dossier()


@api.get("/api/vault/catalog")
def get_vault_catalog() -> dict[str, object]:
    from app.question_catalog import QUESTION_CATALOG

    items = []
    for item in QUESTION_CATALOG:
        items.append({
            "id": item.get("canonical_key") or item.get("id"),
            "canonical_key": item.get("canonical_key"),
            "label": item.get("label", ""),
            "category": item.get("category", "general"),
            "setup_group": item.get("setup_group", "General"),
            "resolved_answer": (item.get("suggested_answers") or ["Grounded profile facts"])[0],
            "description": item.get("description", ""),
        })
    return {
        "catalog_size": len(items),
        "items": items,
        "candidate": {
            "name": "Mohamed Alem",
            "location": "Ottawa, ON, Canada",
            "languages": ["English (Fluent)", "French (Bilingual)"],
            "work_authorization": "Canadian Citizen / Authorized without sponsorship",
        },
    }


@api.post("/api/jobs/{job_id}/run-autonomous")
def run_job_autonomous(job_id: str, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    from app.autonomous_loop import AutonomousConfig, AutonomousRunner
    from app.models import Job

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")

    try:
        runner = AutonomousRunner(db, current, AutonomousConfig())
        record = runner.run_job(job)
        return {
            "job_id": record.job_id,
            "status": record.status,
            "submitted": record.submitted,
            "platform": record.platform,
            "stage": record.stage,
            "evidence_sha256": record.evidence_sha256,
            "error": record.error,
        }
    except Exception as exc:
        raise HTTPException(409, f"autonomous job execution failed: {exc}") from exc


@api.post("/api/inbox/match")
def match_inbox_email(payload: InboxEmailIn, db: DbSession) -> dict[str, object]:
    from datetime import UTC, datetime
    from app.inbox_matcher import EmailMessage, process_inbox_email

    dt = None
    if payload.received_at:
        try:
            dt = datetime.fromisoformat(payload.received_at)
        except Exception:
            dt = datetime.now(UTC)

    email = EmailMessage(
        sender=payload.sender,
        recipient=payload.recipient,
        subject=payload.subject,
        body=payload.body,
        date=dt or datetime.now(UTC),
    )
    result = process_inbox_email(db, email)
    return {
        "matched": result.matched,
        "application_id": result.application_id,
        "job_id": result.job_id,
        "intent": result.intent.value,
        "confidence": result.confidence,
        "extracted_reference_id": result.extracted_reference_id,
        "extracted_links": result.extracted_links,
        "summary": result.summary,
    }


@api.get("/api/follow-ups/due")
def list_due_follow_ups(db: DbSession) -> list[dict[str, object]]:
    from app.follow_up import get_due_follow_ups

    due = get_due_follow_ups(db)
    return [{
        "application_id": app.id,
        "job_id": job.id,
        "company": job.company,
        "title": job.title,
        "follow_up_number": num,
    } for app, job, num in due]


@api.post("/api/follow-ups/run")
def run_due_follow_ups(db: DbSession) -> dict[str, object]:
    from app.follow_up import process_due_follow_ups
    from app.profile import verified_profile

    profile = verified_profile(db)
    tasks = process_due_follow_ups(db, profile)
    return {
        "processed": len(tasks),
        "tasks": [{"id": t.id, "title": t.title, "reason_code": t.reason_code} for t in tasks],
    }


@api.get("/api/jobs/{job_id}/artifacts")
def list_job_artifacts(job_id: str, current: CurrentSettings) -> dict[str, object]:
    out_dir = Path(getattr(current, "artifact_dir", None) or "/tmp/hunterx_autonomous")
    job_dir = out_dir / f"job_{job_id}"
    files = []
    if job_dir.is_dir():
        for p in sorted(job_dir.glob("*")):
            if p.is_file() and not p.name.startswith("."):
                files.append({
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "url": f"/api/jobs/{job_id}/artifacts/{p.name}",
                })
    return {"job_id": job_id, "artifacts": files}


@api.get("/api/jobs/{job_id}/artifacts/{artifact_name}")
def get_job_artifact(job_id: str, artifact_name: str, current: CurrentSettings):
    from fastapi.responses import FileResponse

    allowed = {"pre_submit.png", "post_submit.png", "tailored_resume.pdf"}
    if artifact_name not in allowed:
        raise HTTPException(400, "Invalid artifact name requested")

    out_dir = Path(getattr(current, "artifact_dir", None) or "/tmp/hunterx_autonomous")
    file_path = out_dir / f"job_{job_id}" / artifact_name
    if not file_path.is_file():
        raise HTTPException(404, f"Artifact '{artifact_name}' not found for job {job_id}")

    media_types = {
        "pre_submit.png": "image/png",
        "post_submit.png": "image/png",
        "tailored_resume.pdf": "application/pdf",
    }
    return FileResponse(file_path, media_type=media_types[artifact_name])


@api.get("/api/diagnostics/export")
def export_system_diagnostics(db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Compiles a complete offline diagnostic snapshot of the autonomous engine."""
    from datetime import UTC, datetime
    import platform
    import sys
    from app.certification_system import build_certification_dossier
    from app.flags import snapshot as flags_snapshot
    from app.models import Job, ReviewTask
    from app.profile import verified_profile

    # Job stage counts
    stage_counts = dict(
        db.execute(select(Job.stage, func.count(Job.id)).group_by(Job.stage)).all()
    )
    total_jobs = sum(stage_counts.values())

    # Review tasks by reason
    review_counts = dict(
        db.execute(
            select(ReviewTask.reason_code, func.count(ReviewTask.id))
            .where(ReviewTask.status == "open")
            .group_by(ReviewTask.reason_code)
        ).all()
    )
    total_open_reviews = sum(review_counts.values())

    profile = verified_profile(db)
    try:
        from app.certification_system import build_certification_dossier
        db_path = getattr(current, "database_file", None) or getattr(current, "database_path", None) or "./data/hunterxjob-v2.db"
        dossier = build_certification_dossier(db_path=db_path)
        dossier_summary = {
            "status": dossier.get("overall_certification_status"),
            "total_verified_submissions": dossier.get("evidence_ledger", {}).get("total_verified_submissions", 0),
            "dossier_digest": dossier.get("dossier_digest"),
            "milestones": {k: v.get("passed") for k, v in (dossier.get("milestones") or {}).items()},
        }
    except Exception as exc:
        dossier_summary = {"status": "unavailable", "error": str(exc)}

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "hunterx_version": __version__,
        "system": {
            "platform": platform.platform(),
            "python_version": sys.version,
        },
        "posture": {
            "application_mode": current.application_mode,
            "automation_enabled": current.automation_enabled,
            "allow_live_submission": current.allow_live_submission,
            "live_submission_locked": True,
            "continuous_run_enabled": current.continuous_run_enabled,
            "flags": flags_snapshot(db),
        },
        "database": {
            "total_jobs": total_jobs,
            "by_stage": stage_counts,
            "open_review_tasks_total": total_open_reviews,
            "open_review_tasks_by_reason": review_counts,
        },
        "profile": {
            "facts_count": len(profile.facts),
            "is_empty": profile.empty,
        },
        "ai": LocalAI(current).health(),
        "certification_dossier_summary": dossier_summary,
    }



app.include_router(api)
app.include_router(remote_router)
app.include_router(materials_router)
