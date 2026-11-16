"""Autofill Extension API router.

Exposes verified candidate facts, master answer vault policies, and pre-submission
field evaluation to the portable browser extension and in-page autofill tool.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answer_vault import FieldRequest, ResolutionStatus
from app.db import get_db
from app.form_engine import _normalize_option, _score_best_option
from app.greenhouse_form import shared_answer_key
from app.models import AnswerPolicy, Application, Job, PipelineStage
from app.profile import verified_profile
from app.question_catalog import classify_question as catalog_classify
from app.security import require_api_key
from app.vault_store import load_vault

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/autofill", tags=["autofill"], dependencies=[Depends(require_api_key)])
DbSession = Annotated[Session, Depends(get_db)]


class FieldDescriptorIn(BaseModel):
    id: str = ""
    name: str = ""
    label: str = ""
    control_type: str = "text"
    options: list[str] = Field(default_factory=list)


class EvaluateFormIn(BaseModel):
    url: str = ""
    platform: str = "generic"
    fields: list[FieldDescriptorIn] = Field(default_factory=list)


class EvaluatedFieldOut(BaseModel):
    field_id: str
    label: str
    control_type: str
    value: Any = None
    matched_option: str | None = None
    source: str = "unmatched"
    confidence: float = 0.0
    review_required: bool = False


class EvaluateFormOut(BaseModel):
    url: str
    platform: str
    fields_evaluated: int
    fields_matched: int
    answers: list[EvaluatedFieldOut]


class LogApplicationIn(BaseModel):
    title: str
    company: str
    url: str
    platform: str = "extension"
    location: str = ""
    fields_filled_count: int = 0
    notes: str = ""


@router.get("/bundle")
def get_autofill_bundle(db: DbSession) -> dict[str, Any]:
    """Return full verified profile, answer policies, and taxonomy in a single payload."""
    vp = verified_profile(db)
    policies = db.query(AnswerPolicy).all()

    profile_data = {
        "contact": vp.contact,
        "full_name": vp.full_name,
        "facts": [{"category": f.category, "data": f.data} for f in vp.facts],
    }

    vault_data = {
        p.key: {
            "value": p.value,
            "scope": p.scope,
            "confidence": p.confidence,
            "sensitive": p.sensitive,
            "note": p.note,
        }
        for p in policies
    }

    return {
        "profile": profile_data,
        "vault": vault_data,
        "version": "1.0.0",
        "timestamp": vp.sha256,
    }


@router.post("/evaluate", response_model=EvaluateFormOut)
def evaluate_form_fields(payload: EvaluateFormIn, db: DbSession) -> EvaluateFormOut:
    """Evaluate DOM fields extracted by the extension against verified profile & answer vault."""
    vp = verified_profile(db)
    vault = load_vault(db)
    contact = vp.contact or {}

    answers: list[EvaluatedFieldOut] = []
    matched_count = 0

    for f in payload.fields:
        label = (f.label or f.name or f.id or "").strip()
        canonical_key = shared_answer_key(label)
        if not canonical_key:
            cat = catalog_classify(label)
            if cat:
                canonical_key = cat.get("canonical_key")

        # 1. Contact field check
        low = label.lower()
        val = None
        source = "unmatched"
        conf = 0.0
        opt = None

        if "first name" in low or low == "first":
            val = contact.get("first_name", "Mohamed")
            source = "verified_profile:first_name"
            conf = 1.0
        elif "last name" in low or low == "last":
            val = contact.get("last_name", "Alem")
            source = "verified_profile:last_name"
            conf = 1.0
        elif "email" in low:
            val = contact.get("email", "mohamed@melato.ca")
            source = "verified_profile:email"
            conf = 1.0
        elif "phone" in low or "mobile" in low:
            val = contact.get("phone", "(343) 324-7677")
            source = "verified_profile:phone"
            conf = 1.0
        elif canonical_key:
            req = FieldRequest(key=canonical_key)
            resolved = vault.resolve(req)
            if resolved.status == ResolutionStatus.RESOLVED:
                val = resolved.value
                source = f"answer_vault:{canonical_key}"
                conf = 1.0

        # Handle multiple choice options matching
        if val is not None and f.options:
            exact = next((o for o in f.options if _normalize_option(o) == _normalize_option(val)), None)
            if exact:
                opt = exact
            else:
                best = _score_best_option(val, f.options)
                if best:
                    opt = best
                    conf = 0.95

        if val is not None:
            matched_count += 1

        answers.append(
            EvaluatedFieldOut(
                field_id=f.id or f.name,
                label=label,
                control_type=f.control_type,
                value=val,
                matched_option=opt,
                source=source,
                confidence=conf,
                review_required=conf < 0.9,
            )
        )

    return EvaluateFormOut(
        url=payload.url,
        platform=payload.platform,
        fields_evaluated=len(payload.fields),
        fields_matched=matched_count,
        answers=answers,
    )


@router.post("/log-application")
def log_external_application(payload: LogApplicationIn, db: DbSession) -> dict[str, Any]:
    """Log an application completed through the extension into HunterXJob SQLite DB."""
    parsed = urlparse(payload.url)
    host = parsed.netloc or "extension"
    path = parsed.path.strip("/") or "applied"
    external_id = f"ext_{host}_{path[:40]}"

    # Check if job already exists
    job = db.execute(
        select(Job).where(Job.source == "extension", Job.external_id == external_id)
    ).scalar_one_or_none()

    if not job:
        job = Job(
            source="extension",
            external_id=external_id,
            title=payload.title,
            company=payload.company,
            location=payload.location or "Ottawa, ON",
            url=payload.url,
            platform=payload.platform,
            stage=PipelineStage.confirmed.value,
        )
        db.add(job)
        db.flush()

    app_row = db.execute(select(Application).where(Application.job_id == job.id)).scalar_one_or_none()
    if not app_row:
        app_row = Application(
            job_id=job.id,
            stage=PipelineStage.confirmed.value,
            mode="extension_autofill",
            confirmation_json=f'{{"logged_by": "hunterx_extension", "fields_filled": {payload.fields_filled_count}}}',
        )
        db.add(app_row)

    db.commit()
    return {"status": "logged", "job_id": job.id, "application_id": app_row.id}
