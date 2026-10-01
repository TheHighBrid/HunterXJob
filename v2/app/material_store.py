"""Versioned storage of generated application materials.

Each generation creates a new *draft* version of the résumé and the cover
letter for one application, with SHA-256 hashes of the text and of the
rendered files recorded in the evidence ledger. Only an *approved* version
is ever offered to a form (``vault_records``) or named in dry-run evidence
(``manifest``); drafts and rejected versions never are.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.answer_vault import AnswerRecord, AnswerSource
from app.config import Settings
from app.evidence import record_evidence
from app.models import Application, ApplicationMaterial, ProfileFact, iso_utc
from app.render import RenderUnavailable, render_docx_bytes, render_pdf_bytes, write_file

KINDS = ("resume", "cover_letter")
DRAFT, APPROVED, REJECTED, SUPERSEDED = "draft", "approved", "rejected", "superseded"
# Greenhouse field names (and generic equivalents) that take a material.
VAULT_KEYS = {
    "resume": (("resume", "file"), ("resume_text", "text")),
    "cover_letter": (("cover_letter", "file"), ("cover_letter_text", "text")),
}


class MaterialsError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    return sha256_bytes(Path(path).read_bytes())


def content_hash(text: str, document: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps({"text": text, "document": document}, sort_keys=True).encode("utf-8"))


def next_version(db: Session, application_id: str, kind: str) -> int:
    current = db.execute(select(func.max(ApplicationMaterial.version)).where(
        ApplicationMaterial.application_id == application_id, ApplicationMaterial.kind == kind)).scalar_one()
    return int(current or 0) + 1


def _render(settings: Settings, application_id: str, kind: str, version: int, document: dict[str, Any]) -> dict[str, Any]:
    folder = Path(settings.materials_dir).expanduser().resolve() / application_id
    out: dict[str, Any] = {"warnings": []}
    try:
        pdf = render_pdf_bytes(document)
    except RenderUnavailable as exc:
        out["warnings"].append(str(exc))
    else:
        path = folder / f"{kind}-v{version}.pdf"
        write_file(path, pdf)
        out.update(pdf_path=str(path), pdf_sha256=sha256_bytes(pdf))
    docx = render_docx_bytes(document)
    path = folder / f"{kind}-v{version}.docx"
    write_file(path, docx)
    out.update(docx_path=str(path), docx_sha256=sha256_bytes(docx))
    return out


def manifest_entry(row: ApplicationMaterial) -> dict[str, Any]:
    return {
        "material_id": row.id, "kind": row.kind, "version": row.version, "status": row.status,
        "content_sha256": row.content_sha256, "pdf_sha256": row.pdf_sha256, "docx_sha256": row.docx_sha256,
        "profile_sha256": row.profile_sha256,
    }


def create_draft(db: Session, settings: Settings, application: Application, *, kind: str, document: dict[str, Any],
                 text: str, meta: dict[str, Any], profile_sha256: str) -> ApplicationMaterial:
    version = next_version(db, application.id, kind)
    files = _render(settings, application.id, kind, version, document)
    row = ApplicationMaterial(
        application_id=application.id, job_id=application.job_id, kind=kind, version=version, status=DRAFT,
        generator=meta.get("generator", "template"),
        content_json=json.dumps({"document": document, **meta, "render_warnings": files.pop("warnings")}, sort_keys=True),
        text=text, content_sha256=content_hash(text, document), profile_sha256=profile_sha256, **files,
    )
    db.add(row)
    db.flush()
    record_evidence(
        db, application, kind="materials_draft", payload=manifest_entry(row), update_application=False,
        confirmation_text=f"{kind} v{version} drafted from verified profile facts; not approved; sha256 {row.content_sha256}",
    )
    return row


def supersede_drafts(db: Session, application_id: str) -> int:
    rows = db.execute(select(ApplicationMaterial).where(
        ApplicationMaterial.application_id == application_id, ApplicationMaterial.status == DRAFT)).scalars().all()
    for row in rows:
        row.status = SUPERSEDED
        row.decided_at = datetime.now(UTC)
    return len(rows)


def get_material(db: Session, material_id: str) -> ApplicationMaterial:
    row = db.get(ApplicationMaterial, material_id)
    if row is None:
        raise MaterialsError(404, "material not found")
    return row


def materials_for(db: Session, application_id: str) -> list[ApplicationMaterial]:
    return list(db.execute(select(ApplicationMaterial).where(ApplicationMaterial.application_id == application_id)
                           .order_by(ApplicationMaterial.kind, ApplicationMaterial.version.desc())).scalars())


def approved(db: Session, application_id: str) -> dict[str, ApplicationMaterial]:
    rows = db.execute(select(ApplicationMaterial).where(
        ApplicationMaterial.application_id == application_id, ApplicationMaterial.status == APPROVED)
        .order_by(ApplicationMaterial.version)).scalars()
    return {row.kind: row for row in rows}


def pending_drafts(db: Session, application_id: str) -> list[ApplicationMaterial]:
    return list(db.execute(select(ApplicationMaterial).where(
        ApplicationMaterial.application_id == application_id, ApplicationMaterial.status == DRAFT)).scalars())


def facts_used(row: ApplicationMaterial) -> list[str]:
    return list(json.loads(row.content_json).get("facts_used", []))


def stale_facts(db: Session, row: ApplicationMaterial) -> list[str]:
    """Facts the material relies on that are no longer verified (or were deleted/edited)."""
    used = facts_used(row)
    if not used:
        return []
    rows = db.execute(select(ProfileFact).where(ProfileFact.key.in_(used))).scalars()
    verified = {fact.key for fact in rows if fact.verified}
    return sorted(set(used) - verified)


def integrity_problem(row: ApplicationMaterial) -> str | None:
    document = json.loads(row.content_json).get("document", {})
    if content_hash(row.text, document) != row.content_sha256:
        return "stored text does not match its hash"
    for label, path, expected in (("PDF", row.pdf_path, row.pdf_sha256), ("DOCX", row.docx_path, row.docx_sha256)):
        if expected and sha256_file(path) != expected:
            return f"{label} file is missing or was modified after generation"
    return None


def decide(db: Session, application: Application, row: ApplicationMaterial, status: str, note: str = "") -> ApplicationMaterial:
    """Approve or reject one material version (status bookkeeping + evidence)."""
    now = datetime.now(UTC)
    if status == APPROVED:
        for other in approved(db, application.id).values():
            if other.kind == row.kind and other.id != row.id:
                other.status = SUPERSEDED
                other.decided_at = now
    row.status = status
    row.decided_at = now
    row.decision_note = note[:500]
    _sync_application(db, application)
    db.flush()
    if status == APPROVED:
        record_evidence(
            db, application, kind="materials_approved", payload=manifest_entry(row), update_application=False,
            confirmation_text=f"owner approved {row.kind} v{row.version}; sha256 {row.content_sha256}",
        )
    db.commit()
    return row


def _sync_application(db: Session, application: Application) -> None:
    """Legacy columns only ever point at approved materials."""
    current = approved(db, application.id)
    resume, letter = current.get("resume"), current.get("cover_letter")
    application.tailored_resume_path = resume.pdf_path if resume else None
    application.cover_letter_path = letter.pdf_path if letter else None
    application.cover_letter_text = letter.text if letter else None
    db.add(application)


def materials_ready(db: Session, application: Application) -> bool:
    """An approved, intact résumé exists whose facts are all still verified."""
    resume = approved(db, application.id).get("resume")
    return resume is not None and integrity_problem(resume) is None and not stale_facts(db, resume)


def usable_approved(db: Session, application: Application) -> tuple[dict[str, ApplicationMaterial], list[str]]:
    usable: dict[str, ApplicationMaterial] = {}
    problems: list[str] = []
    for kind, row in approved(db, application.id).items():
        problem = integrity_problem(row) or (f"relies on facts no longer verified: {', '.join(stale_facts(db, row))}"
                                             if stale_facts(db, row) else None)
        if problem:
            problems.append(f"approved {kind} v{row.version} not used: {problem}")
        else:
            usable[kind] = row
    return usable, problems


def manifest(db: Session, application: Application) -> dict[str, Any]:
    """What a dry-run records: the approved versions that would be attached (never drafts)."""
    usable, problems = usable_approved(db, application)
    return {"attached": [manifest_entry(row) for row in usable.values()], "problems": problems}


def vault_records(db: Session, application: Application) -> list[AnswerRecord]:
    """Answer-vault entries for the approved materials (file path + text). Drafts are never offered."""
    usable, _ = usable_approved(db, application)
    records: list[AnswerRecord] = []
    for kind, row in usable.items():
        for key, form in VAULT_KEYS[kind]:
            value = row.pdf_path if form == "file" else row.text
            if value:
                records.append(AnswerRecord(key=key, value=value, source=AnswerSource.USER,
                                            note=f"approved {kind} v{row.version} sha256 {row.content_sha256}"))
    return records


def material_view(row: ApplicationMaterial, *, detail: bool = False) -> dict[str, Any]:
    meta = json.loads(row.content_json)
    view: dict[str, Any] = {
        "id": row.id, "application_id": row.application_id, "job_id": row.job_id, "kind": row.kind,
        "version": row.version, "status": row.status, "generator": row.generator,
        "content_sha256": row.content_sha256, "pdf_sha256": row.pdf_sha256, "docx_sha256": row.docx_sha256,
        "profile_sha256": row.profile_sha256, "has_pdf": bool(row.pdf_path), "has_docx": bool(row.docx_path),
        "llm": meta.get("llm", {}).get("status", "disabled"), "decision_note": row.decision_note,
        "created_at": iso_utc(row.created_at), "decided_at": iso_utc(row.decided_at),
    }
    if detail:
        view.update(text=row.text, facts_used=meta.get("facts_used", []), llm_report=meta.get("llm", {}),
                    guard=meta.get("guard", {}), render_warnings=meta.get("render_warnings", []))
    return view
