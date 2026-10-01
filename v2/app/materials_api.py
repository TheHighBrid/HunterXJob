"""Phone API for the verified profile and per-job application materials.

All routes need the API key (router dependency). Nothing here submits
anything: approving a material only marks that version usable; at most the
application becomes eligible for the next *dry-run*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api_schemas import (
    FactCreateIn,
    FactEditIn,
    FactRemovedOut,
    FactVerifyIn,
    JobMaterialsOut,
    MaterialActionOut,
    MaterialDecisionIn,
    MaterialDetailOut,
    ProfileFactOut,
    ProfileImportIn,
    ProfileImportOut,
    ProfileOut,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.material_store import MaterialsError, approved, get_material, material_view, materials_for, materials_ready
from app.material_workflow import GENERATABLE_STAGES, approve_material, generate_for_application, reject_material
from app.models import Job
from app.profile import (
    CATEGORIES,
    ProfileError,
    add_fact,
    edit_fact,
    fact_view,
    import_profile_document,
    list_facts,
    parse_profile_text,
    readiness,
    remove_fact,
    set_verified,
    store_drafts,
    verified_profile,
)
from app.resume_import import parse_resume_text
from app.security import require_api_key

router = APIRouter(dependencies=[Depends(require_api_key)])
DbSession = Annotated[Session, Depends(get_db)]
CurrentSettings = Annotated[Settings, Depends(get_settings)]


def _http(exc: MaterialsError | ProfileError | LookupError) -> HTTPException:
    if isinstance(exc, MaterialsError):
        return HTTPException(exc.status_code, exc.detail)
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc).strip("'\""))
    return HTTPException(422, str(exc))


# ----------------------------------------------------------------------- profile


@router.get("/api/profile", response_model=ProfileOut, tags=["profile"])
def profile(db: DbSession) -> dict[str, object]:
    facts = [fact_view(row) for row in list_facts(db)]
    verified = sum(1 for fact in facts if fact["verified"])
    missing = readiness(verified_profile(db))
    return {"facts": facts, "total": len(facts), "verified": verified, "unverified": len(facts) - verified,
            "ready": not missing, "missing": missing, "categories": list(CATEGORIES)}


@router.post("/api/profile/import", response_model=ProfileImportOut, tags=["profile"])
def import_profile(payload: ProfileImportIn, db: DbSession) -> dict[str, object]:
    """Import a YAML/JSON profile, or pasted résumé text (always imported UNVERIFIED)."""
    origin = payload.filename or f"pasted {payload.format}"
    try:
        if payload.format == "resume_text":
            drafts, warnings = parse_resume_text(payload.content, origin=origin)
            result = store_drafts(db, drafts, "resume_text")
            result.warnings.extend(warnings)
        else:
            result = import_profile_document(db, parse_profile_text(payload.content, payload.format), origin=origin)
    except ProfileError as exc:
        raise _http(exc) from exc
    return result.as_dict()


@router.post("/api/profile/facts", response_model=ProfileFactOut, tags=["profile"])
def create_fact(payload: FactCreateIn, db: DbSession) -> dict[str, object]:
    try:
        return fact_view(add_fact(db, payload.category, payload.data, verified=payload.verified))
    except ProfileError as exc:
        raise _http(exc) from exc


@router.put("/api/profile/facts/{fact_id}", response_model=ProfileFactOut, tags=["profile"])
def update_fact(fact_id: str, payload: FactEditIn, db: DbSession) -> dict[str, object]:
    """Replace a fact's content. It becomes unverified unless ``verified`` is true in the same request."""
    try:
        return fact_view(edit_fact(db, fact_id, payload.data, verified=payload.verified))
    except (ProfileError, LookupError) as exc:
        raise _http(exc) from exc


@router.post("/api/profile/facts/{fact_id}/verify", response_model=ProfileFactOut, tags=["profile"])
def verify_fact(fact_id: str, payload: FactVerifyIn, db: DbSession) -> dict[str, object]:
    try:
        return fact_view(set_verified(db, fact_id, payload.verified))
    except LookupError as exc:
        raise _http(exc) from exc


@router.post("/api/profile/facts/{fact_id}/remove", response_model=FactRemovedOut, tags=["profile"])
def delete_fact(fact_id: str, db: DbSession) -> dict[str, object]:
    try:
        remove_fact(db, fact_id)
    except LookupError as exc:
        raise _http(exc) from exc
    return {"removed": fact_id}


# --------------------------------------------------------------------- materials


def _job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


def _job_materials(db: Session, settings: Settings, job: Job) -> dict[str, object]:
    application = job.application
    blockers = list(readiness(verified_profile(db)))
    if application is None:
        blockers.insert(0, "the job has no application yet (shortlist or approve it first)")
    elif job.stage not in GENERATABLE_STAGES:
        blockers.insert(0, f"materials cannot be generated while the job is {job.stage}")
    rows = materials_for(db, application.id) if application else []
    current = approved(db, application.id) if application else {}
    return {
        "job_id": job.id, "application_id": application.id if application else None, "job_stage": job.stage,
        "can_generate": not blockers, "generate_blockers": blockers, "llm_enabled": settings.materials_llm_enabled,
        "approved": {kind: row.id for kind, row in current.items()},
        "items": [material_view(row) for row in rows], "live_submission_locked": True,
    }


@router.get("/api/jobs/{job_id}/materials", response_model=JobMaterialsOut, tags=["materials"])
def job_materials(job_id: str, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    return _job_materials(db, current, _job(db, job_id))


@router.post("/api/jobs/{job_id}/materials/generate", response_model=JobMaterialsOut, tags=["materials"])
def generate_job_materials(job_id: str, db: DbSession, current: CurrentSettings) -> dict[str, object]:
    """Create new DRAFT versions from verified facts. Approved versions are kept; nothing is attached or sent."""
    job = _job(db, job_id)
    if job.application is None:
        raise HTTPException(409, "the job has no application yet (shortlist or approve it first)")
    try:
        generate_for_application(db, current, job.application.id)
    except MaterialsError as exc:
        raise _http(exc) from exc
    db.refresh(job)
    return _job_materials(db, current, job)


@router.get("/api/materials/{material_id}", response_model=MaterialDetailOut, tags=["materials"])
def material(material_id: str, db: DbSession) -> dict[str, object]:
    try:
        return material_view(get_material(db, material_id), detail=True)
    except MaterialsError as exc:
        raise _http(exc) from exc


@router.get("/api/materials/{material_id}/file/{fmt}", tags=["materials"],
            response_class=FileResponse, responses={200: {"content": {"application/pdf": {}}}})
def material_file(material_id: str, fmt: Literal["pdf", "docx"], db: DbSession, current: CurrentSettings) -> FileResponse:
    try:
        row = get_material(db, material_id)
    except MaterialsError as exc:
        raise _http(exc) from exc
    raw = row.pdf_path if fmt == "pdf" else row.docx_path
    root = Path(current.materials_dir).expanduser().resolve()
    path = Path(raw).resolve() if raw else None
    if path is None or not path.is_file() or not path.is_relative_to(root):
        raise HTTPException(404, f"no {fmt} file for this material")
    media = "application/pdf" if fmt == "pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return FileResponse(path, media_type=media, filename=path.name)


def _action(db: Session, row_id: str, action: Literal["approve", "reject"], note: str) -> dict[str, object]:
    try:
        row = approve_material(db, row_id, note) if action == "approve" else reject_material(db, row_id, note)
    except MaterialsError as exc:
        raise _http(exc) from exc
    job = db.get(Job, row.job_id)
    application = job.application if job else None
    return {
        "material": material_view(row), "job_stage": job.stage if job else "",
        "application_stage": application.stage if application else "",
        "materials_ready": bool(application and materials_ready(db, application)), "submitted": False,
    }


@router.post("/api/materials/{material_id}/approve", response_model=MaterialActionOut, tags=["materials"])
def approve(material_id: str, db: DbSession, payload: MaterialDecisionIn | None = None) -> dict[str, object]:
    """Approve this draft version. Only approved versions can ever be attached to a form."""
    return _action(db, material_id, "approve", (payload or MaterialDecisionIn()).note)


@router.post("/api/materials/{material_id}/reject", response_model=MaterialActionOut, tags=["materials"])
def reject(material_id: str, db: DbSession, payload: MaterialDecisionIn | None = None) -> dict[str, object]:
    return _action(db, material_id, "reject", (payload or MaterialDecisionIn()).note)
