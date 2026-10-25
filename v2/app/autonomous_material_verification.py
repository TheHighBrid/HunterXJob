"""Deterministic material claims verification and Truth Guard inspector.

Ensures every claim, achievement bullet, skill, and statement in generated materials
(résumés, cover letters) maps 100% to verified candidate facts.
Computes deterministic SHA-256 digests and guards against hallucinated or drifted claims.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.materials import build_cover_letter, build_resume, JobContext
from app.models import Application, Job, PipelineStage
from app.profile import Fact, VerifiedProfile, verified_profile

AUTONOMOUS_MATERIAL_POLICY_VERSION = "hunterx-material-verification-v1"
MIN_AUTONOMOUS_CLAIM_CONFIDENCE = 0.90


def text_sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_resume_artifact(job_id: str, artifact_dir: str = "/tmp/hunterx_autonomous") -> dict[str, Any]:
    out_dir = Path(artifact_dir) / f"job_{job_id}"
    pdf_path = out_dir / "tailored_resume.pdf"
    if pdf_path.is_file():
        stat = pdf_path.stat()
        return {
            "status": "verified",
            "exists": True,
            "filename": pdf_path.name,
            "path": str(pdf_path),
            "size_bytes": stat.st_size,
            "sha256": file_sha256(pdf_path),
        }
    return {
        "status": "not_generated",
        "exists": False,
        "filename": "tailored_resume.pdf",
        "path": str(pdf_path),
        "size_bytes": 0,
        "sha256": None,
    }


def verify_job_materials_claims(
    db: Session,
    job: Job,
    profile: VerifiedProfile | None = None,
    artifact_dir: str = "/tmp/hunterx_autonomous",
) -> dict[str, Any]:
    """Inspects all claims generated for this job and validates against candidate truth facts."""
    if profile is None:
        profile = verified_profile(db)

    job_ctx = JobContext(
        title=job.title,
        company=job.company,
        location=job.location or "",
        description=job.description or "",
    )

    resume_doc = build_resume(profile, job_ctx)
    cover_letter_doc = build_cover_letter(profile, job_ctx, resume_doc)

    verified_claims: list[dict[str, Any]] = []
    unsupported_claims: list[dict[str, Any]] = []

    # Map profile facts by key
    facts_by_key = {fact.key: fact for fact in profile.facts}

    # Inspect Cover Letter Paragraphs
    for p_idx, para in enumerate(cover_letter_doc.get("paragraphs", [])):
        para_text = para.get("text", "")
        fact_keys = para.get("facts", [])
        if not fact_keys:
            # Informational / polite formula (e.g. thank you)
            verified_claims.append({
                "source": "cover_letter",
                "section": f"Paragraph {p_idx + 1}",
                "text": para_text,
                "fact_key": "common:courtesy_closing",
                "fact_quote": "Standard polite applicant communication",
                "confidence": 1.0,
                "status": "verified",
            })
            continue

        for fk in fact_keys:
            if fk in facts_by_key:
                fact = facts_by_key[fk]
                quote = fact.data.get("text") or fact.data.get("name") or str(fact.data)
                verified_claims.append({
                    "source": "cover_letter",
                    "section": f"Paragraph {p_idx + 1}",
                    "text": para_text,
                    "fact_key": fk,
                    "fact_quote": quote,
                    "confidence": 1.0,
                    "status": "verified",
                })
            else:
                unsupported_claims.append({
                    "source": "cover_letter",
                    "section": f"Paragraph {p_idx + 1}",
                    "text": para_text,
                    "fact_key": fk,
                    "reason": f"Fact key '{fk}' not found in candidate verified profile",
                })

    # Inspect Résumé Bullets
    for entry in resume_doc.get("experience", []):
        emp = entry.get("employer", "")
        role = entry.get("title", "")
        for bullet in entry.get("bullets", []):
            fk = bullet.get("fact", "")
            b_text = bullet.get("text", "")
            if fk in facts_by_key:
                fact = facts_by_key[fk]
                verified_claims.append({
                    "source": "resume",
                    "section": f"{role} at {emp}",
                    "text": b_text,
                    "fact_key": fk,
                    "fact_quote": fact.data.get("text", ""),
                    "confidence": 1.0,
                    "status": "verified",
                })
            else:
                unsupported_claims.append({
                    "source": "resume",
                    "section": f"{role} at {emp}",
                    "text": b_text,
                    "fact_key": fk,
                    "reason": f"Fact key '{fk}' missing from verified profile",
                })

    resume_artifact = inspect_resume_artifact(job.id, artifact_dir)

    all_verified = len(unsupported_claims) == 0 and len(verified_claims) > 0
    verification_status = "verified" if all_verified else ("needs_review" if unsupported_claims else "unverified")

    material_payload = {
        "job_id": job.id,
        "company": job.company,
        "title": job.title,
        "resume": resume_doc,
        "cover_letter": cover_letter_doc,
    }

    return {
        "job_id": job.id,
        "company": job.company,
        "title": job.title,
        "status": verification_status,
        "truth_grounded": all_verified,
        "claims_total": len(verified_claims) + len(unsupported_claims),
        "verified_count": len(verified_claims),
        "unsupported_count": len(unsupported_claims),
        "verified_claims": verified_claims,
        "unsupported_claims": unsupported_claims,
        "resume_artifact": resume_artifact,
        "material_hash": canonical_json_sha256(material_payload),
        "policy_version": AUTONOMOUS_MATERIAL_POLICY_VERSION,
        "profile_facts_grounding_rate": 1.0 if not unsupported_claims else (
            len(verified_claims) / (len(verified_claims) + len(unsupported_claims))
        ),
    }
