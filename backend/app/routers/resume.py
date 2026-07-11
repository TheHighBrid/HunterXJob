"""POST /api/resume — upload/update structured resume JSON (+ render to PDF).
GET /api/resume — fetch resume version(s).
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.db import get_db
from app.models import JobPosting, ResumeVersion
from app.schemas import ResumeUploadRequest, ResumeVersionOut
from app.services.matching import score_job_fit
from app.services.resume_render import render_resume_pdf

router = APIRouter(prefix="/api/resume", tags=["resume"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=ResumeVersionOut, status_code=201)
def upload_resume(
    payload: ResumeUploadRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ResumeVersion:
    match_score = None
    job_posting = None
    if payload.tailored_for_job_id:
        job_posting = db.get(JobPosting, payload.tailored_for_job_id)
        if job_posting is None:
            raise HTTPException(status_code=404, detail="tailored_for_job_id does not exist")

    if payload.is_base:
        # unset previous base flag(s)
        existing_base = db.execute(
            select(ResumeVersion).where(ResumeVersion.is_base.is_(True))
        ).scalars().all()
        for rv in existing_base:
            rv.is_base = False
            db.add(rv)

    resume = ResumeVersion(
        resume_json=json.dumps(payload.resume_json),
        tailored_for_job_id=payload.tailored_for_job_id,
        is_base=payload.is_base,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    if job_posting is not None:
        resume_text = _resume_json_to_text(payload.resume_json)
        match_score = score_job_fit(resume_text, job_posting.description)
        resume.match_score = match_score

    output_path = str(Path(settings.RENDERED_DOCS_DIR) / f"resume_{resume.id}.pdf")
    try:
        render_resume_pdf(payload.resume_json, output_path)
        resume.pdf_path = output_path
    except RuntimeError:
        # Playwright/Chromium not installed in this environment - leave
        # pdf_path unset rather than failing the whole upload; the resume
        # JSON is still saved and can be re-rendered later.
        resume.pdf_path = None

    db.add(resume)
    db.commit()
    db.refresh(resume)

    out = ResumeVersionOut.model_validate(resume)
    out.resume_json = payload.resume_json
    return out


@router.get("", response_model=list[ResumeVersionOut])
def list_resumes(
    db: Session = Depends(get_db),
    is_base: bool | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
) -> list[ResumeVersionOut]:
    stmt = select(ResumeVersion)
    if is_base is not None:
        stmt = stmt.where(ResumeVersion.is_base.is_(is_base))
    stmt = stmt.order_by(ResumeVersion.created_at.desc()).limit(limit)
    rows = db.execute(stmt).scalars().all()

    results = []
    for row in rows:
        out = ResumeVersionOut.model_validate(row)
        out.resume_json = json.loads(row.resume_json)
        results.append(out)
    return results


def _resume_json_to_text(resume_json: dict) -> str:
    parts = [resume_json.get("summary", "")]
    parts.extend(resume_json.get("skills", []) or [])
    for job in resume_json.get("experience", []) or []:
        parts.append(job.get("title", ""))
        parts.extend(job.get("bullets", []) or [])
    return "\n".join(str(p) for p in parts if p)
