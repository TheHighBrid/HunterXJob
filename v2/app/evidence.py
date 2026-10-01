from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Application, PipelineStage, SubmissionEvidence

CONFIRMATION_MARKERS = (
    "thank you for applying",
    "thanks for applying",
    "application has been submitted",
    "successfully submitted",
    "we have received your application",
    "your application was submitted",
    "application received",
)


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evidence_is_sufficient(*, confirmation_text: str | None, candidate_id: str | None, final_url: str | None) -> bool:
    text = (confirmation_text or "").lower()
    if candidate_id:
        return True
    if any(marker in text for marker in CONFIRMATION_MARKERS):
        return True
    return bool(final_url) and any(token in final_url.lower() for token in ("confirmation", "thank", "success", "submitted"))


def record_evidence(
    db: Session,
    application: Application,
    *,
    kind: str,
    confirmation_text: str | None = None,
    candidate_id: str | None = None,
    final_url: str | None = None,
    screenshot_path: str | None = None,
    html_snapshot_path: str | None = None,
    payload: dict[str, Any] | None = None,
    adapter_name: str | None = None,
    adapter_version: str = "2.0.0",
    update_application: bool = True,
) -> SubmissionEvidence:
    # Only a submission can ever count as confirmed; dry-run and materials
    # entries are ledger records, never proof that anything was sent.
    sufficient = kind == "submission" and evidence_is_sufficient(
        confirmation_text=confirmation_text,
        candidate_id=candidate_id,
        final_url=final_url,
    )
    row = SubmissionEvidence(
        application_id=application.id,
        kind=kind,
        sufficient=sufficient,
        final_url=final_url,
        confirmation_text=confirmation_text,
        candidate_id=candidate_id,
        screenshot_path=screenshot_path,
        html_snapshot_path=html_snapshot_path,
        payload_hash=payload_hash(payload or {}),
        adapter_name=adapter_name or application.adapter_name,
        adapter_version=adapter_version,
    )
    db.add(row)
    if update_application:
        _update_application(application, kind, sufficient, final_url, confirmation_text, candidate_id)
        db.add(application)
    db.commit()
    db.refresh(row)
    return row


def _update_application(application: Application, kind: str, sufficient: bool, final_url: str | None,
                        confirmation_text: str | None, candidate_id: str | None) -> None:
    application.confirmation_json = json.dumps({
        "kind": kind,
        "sufficient": sufficient,
        "final_url": final_url,
        "confirmation_text": confirmation_text,
        "candidate_id": candidate_id,
    })
    if kind == "submission":
        application.stage = PipelineStage.submitted.value if sufficient else PipelineStage.submission_uncertain.value
