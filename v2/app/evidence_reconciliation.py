"""Native Evidence Intelligence, Outcome Reconciliation, and Auditable Receipts.

Observational evidence reconciliation layer for HunterXJob application attempts:
- Categorizes evidence across multiple sources (Browser DOM, Inbound Email, Network, Manual).
- Evaluates multi-signal confidence (0.0 to 1.0) and assigns outcome classifications:
  CONFIRMED_SUBMISSION, PROBABLE_SUBMISSION, UNCERTAIN_OUTCOME, CONFIRMED_NON_SUBMISSION.
- Detects delayed confirmation receipts (e.g. follow-up confirmation emails matching via inbox_matcher).
- Detects conflicting or incomplete evidence.
- Preserves cryptographic snapshot hashes (SHA-256) for audit trails.
- Provides owner-reviewed resolution for ambiguous outcomes.

SAFETY & ARCHITECTURAL INVARIANTS:
1. Purely observational: Never replays an application, resubmits, or restarts execution.
2. Does NOT mutate the application execution state machine or pipeline stage.
3. Telemetry and reconciliation errors fail-safe and never block application execution.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence import CONFIRMATION_MARKERS
from app.models import Application, Job, PipelineEvent, PipelineStage, SubmissionEvidence

logger = logging.getLogger(__name__)


class EvidenceType(str, Enum):
    CONFIRMATION_PAGE = "confirmation_page"
    CONFIRMATION_EMAIL = "confirmation_email"
    RECEIPT_ID = "receipt_id"
    SCREENSHOT_PROOF = "screenshot_proof"
    HTTP_REDIRECT = "http_redirect"
    DRY_RUN_LEDGER = "dry_run_ledger"
    VALIDATION_ERROR = "validation_error"


class EvidenceSource(str, Enum):
    BROWSER_DOM = "browser_dom"
    INBOUND_EMAIL = "inbound_email"
    NETWORK_RESPONSE = "network_response"
    MANUAL_REVIEW = "manual_review"


class OutcomeClassification(str, Enum):
    CONFIRMED_SUBMISSION = "confirmed_submission"
    PROBABLE_SUBMISSION = "probable_submission"
    UNCERTAIN_OUTCOME = "uncertain_outcome"
    CONFIRMED_NON_SUBMISSION = "confirmed_non_submission"


# Reliability weights by evidence source and type
RELIABILITY_WEIGHTS: dict[tuple[EvidenceType, EvidenceSource], float] = {
    (EvidenceType.RECEIPT_ID, EvidenceSource.BROWSER_DOM): 0.95,
    (EvidenceType.CONFIRMATION_EMAIL, EvidenceSource.INBOUND_EMAIL): 0.95,
    (EvidenceType.CONFIRMATION_PAGE, EvidenceSource.BROWSER_DOM): 0.85,
    (EvidenceType.SCREENSHOT_PROOF, EvidenceSource.BROWSER_DOM): 0.80,
    (EvidenceType.HTTP_REDIRECT, EvidenceSource.BROWSER_DOM): 0.70,
    (EvidenceType.HTTP_REDIRECT, EvidenceSource.NETWORK_RESPONSE): 0.70,
    (EvidenceType.DRY_RUN_LEDGER, EvidenceSource.BROWSER_DOM): 0.90,
    (EvidenceType.VALIDATION_ERROR, EvidenceSource.BROWSER_DOM): 0.90,
}


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def compute_snapshot_hash(snapshot_data: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 digest of canonical evidence snapshot."""
    canonical = _canonical_json(snapshot_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClassifiedEvidence:
    id: str
    evidence_type: EvidenceType
    source: EvidenceSource
    reliability: float
    is_sufficient: bool
    summary: str
    raw_signal: str
    receipt_reference: str | None
    captured_at: str
    snapshot_hash: str


@dataclass
class ReconciliationReport:
    application_id: str
    job_id: str
    company: str
    title: str
    classification: OutcomeClassification
    confidence: float
    evidence_count: int
    has_conflicts: bool
    conflict_notes: list[str]
    evidence_items: list[ClassifiedEvidence]
    reconciliation_timestamp: str
    owner_resolution: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["classification"] = self.classification.value
        data["evidence_items"] = [asdict(e) for e in self.evidence_items]
        for e in data["evidence_items"]:
            e["evidence_type"] = e["evidence_type"].value
            e["source"] = e["source"].value
        return data


def classify_single_evidence(evidence: SubmissionEvidence) -> list[ClassifiedEvidence]:
    """Extract and classify individual evidence signals from a SubmissionEvidence record."""
    items: list[ClassifiedEvidence] = []
    created_iso = evidence.created_at.isoformat() if evidence.created_at else datetime.now(UTC).isoformat()

    # 1. Candidate ID / External Reference
    if evidence.candidate_id and evidence.candidate_id.strip():
        payload = {
            "evidence_id": evidence.id,
            "application_id": evidence.application_id,
            "type": EvidenceType.RECEIPT_ID.value,
            "value": evidence.candidate_id.strip(),
            "created_at": created_iso,
        }
        items.append(ClassifiedEvidence(
            id=f"{evidence.id}:receipt",
            evidence_type=EvidenceType.RECEIPT_ID,
            source=EvidenceSource.BROWSER_DOM,
            reliability=RELIABILITY_WEIGHTS[(EvidenceType.RECEIPT_ID, EvidenceSource.BROWSER_DOM)],
            is_sufficient=True,
            summary=f"Employer receipt ID: {evidence.candidate_id.strip()}",
            raw_signal=evidence.candidate_id.strip(),
            receipt_reference=evidence.candidate_id.strip(),
            captured_at=created_iso,
            snapshot_hash=compute_snapshot_hash(payload),
        ))

    # 2. Confirmation page text
    if evidence.confirmation_text and evidence.confirmation_text.strip():
        text_lower = evidence.confirmation_text.lower()
        has_marker = any(m in text_lower for m in CONFIRMATION_MARKERS)
        payload = {
            "evidence_id": evidence.id,
            "application_id": evidence.application_id,
            "type": EvidenceType.CONFIRMATION_PAGE.value,
            "text": evidence.confirmation_text[:200],
            "created_at": created_iso,
        }
        items.append(ClassifiedEvidence(
            id=f"{evidence.id}:page_text",
            evidence_type=EvidenceType.CONFIRMATION_PAGE,
            source=EvidenceSource.BROWSER_DOM,
            reliability=RELIABILITY_WEIGHTS[(EvidenceType.CONFIRMATION_PAGE, EvidenceSource.BROWSER_DOM)] if has_marker else 0.40,
            is_sufficient=has_marker,
            summary="Observed post-submit confirmation banner" if has_marker else "Page text observed",
            raw_signal=evidence.confirmation_text[:300],
            receipt_reference=None,
            captured_at=created_iso,
            snapshot_hash=compute_snapshot_hash(payload),
        ))

    # 3. HTTP redirect / confirmation URL
    if evidence.final_url and any(token in evidence.final_url.lower() for token in ("confirmation", "thank", "success", "submitted")):
        payload = {
            "evidence_id": evidence.id,
            "application_id": evidence.application_id,
            "type": EvidenceType.HTTP_REDIRECT.value,
            "final_url": evidence.final_url,
            "created_at": created_iso,
        }
        items.append(ClassifiedEvidence(
            id=f"{evidence.id}:redirect",
            evidence_type=EvidenceType.HTTP_REDIRECT,
            source=EvidenceSource.BROWSER_DOM,
            reliability=RELIABILITY_WEIGHTS[(EvidenceType.HTTP_REDIRECT, EvidenceSource.BROWSER_DOM)],
            is_sufficient=True,
            summary=f"Redirected to confirmation destination: {evidence.final_url}",
            raw_signal=evidence.final_url,
            receipt_reference=None,
            captured_at=created_iso,
            snapshot_hash=compute_snapshot_hash(payload),
        ))

    # 4. Screenshot proof
    if evidence.screenshot_path and evidence.payload_hash:
        payload = {
            "evidence_id": evidence.id,
            "application_id": evidence.application_id,
            "type": EvidenceType.SCREENSHOT_PROOF.value,
            "payload_hash": evidence.payload_hash,
            "created_at": created_iso,
        }
        items.append(ClassifiedEvidence(
            id=f"{evidence.id}:screenshot",
            evidence_type=EvidenceType.SCREENSHOT_PROOF,
            source=EvidenceSource.BROWSER_DOM,
            reliability=RELIABILITY_WEIGHTS[(EvidenceType.SCREENSHOT_PROOF, EvidenceSource.BROWSER_DOM)],
            is_sufficient=bool(evidence.sufficient),
            summary="Cryptographic post-submit screenshot proof retained",
            raw_signal=evidence.payload_hash,
            receipt_reference=None,
            captured_at=created_iso,
            snapshot_hash=compute_snapshot_hash(payload),
        ))

    return items


def find_delayed_inbound_confirmations(db: Session, job: Job) -> list[ClassifiedEvidence]:
    """Find delayed confirmation receipts from inbound email events matching this job."""
    items: list[ClassifiedEvidence] = []
    events = db.execute(
        select(PipelineEvent).where(
            PipelineEvent.job_id == job.id,
            PipelineEvent.message.like("%confirmation%"),
        )
    ).scalars().all()

    for ev in events:
        created_iso = ev.created_at.isoformat() if ev.created_at else datetime.now(UTC).isoformat()
        payload = {
            "event_id": ev.id,
            "job_id": job.id,
            "type": EvidenceType.CONFIRMATION_EMAIL.value,
            "message": ev.message,
            "created_at": created_iso,
        }
        items.append(ClassifiedEvidence(
            id=f"email:{ev.id}",
            evidence_type=EvidenceType.CONFIRMATION_EMAIL,
            source=EvidenceSource.INBOUND_EMAIL,
            reliability=RELIABILITY_WEIGHTS[(EvidenceType.CONFIRMATION_EMAIL, EvidenceSource.INBOUND_EMAIL)],
            is_sufficient=True,
            summary=f"Delayed inbound confirmation email: {ev.message}",
            raw_signal=ev.payload_json or ev.message,
            receipt_reference=None,
            captured_at=created_iso,
            snapshot_hash=compute_snapshot_hash(payload),
        ))
    return items


def reconcile_application_evidence(db: Session, application: Application) -> ReconciliationReport:
    """Analyze all captured and delayed evidence for an application without mutating state.

    Calculates:
    - Evidence items classification and individual reliability
    - Multi-signal cumulative confidence score
    - Conflicting evidence detection (e.g. fatal validation error vs confirmation)
    - OutcomeClassification
    """
    job = db.get(Job, application.job_id)
    company = job.company if job else "Unknown Company"
    title = job.title if job else "Unknown Role"

    classified_items: list[ClassifiedEvidence] = []

    # 1. Query recorded submission evidence
    records = db.execute(
        select(SubmissionEvidence)
        .where(SubmissionEvidence.application_id == application.id)
        .order_by(SubmissionEvidence.created_at.asc())
    ).scalars().all()

    for record in records:
        classified_items.extend(classify_single_evidence(record))

    # 2. Check for delayed email confirmations
    if job:
        delayed_emails = find_delayed_inbound_confirmations(db, job)
        classified_items.extend(delayed_emails)

    # 3. Detect conflicts
    conflicts: list[str] = []
    has_validation_failure = False
    has_positive_confirmation = any(item.is_sufficient and item.reliability >= 0.70 for item in classified_items)

    if application.last_error and "validation" in application.last_error.lower():
        has_validation_failure = True
        conflicts.append(f"Recorded validation error in last_error: {application.last_error[:120]}")

    if has_validation_failure and has_positive_confirmation:
        conflicts.append("Conflicting evidence: Recorded validation failure co-exists with positive confirmation.")

    # 4. Multi-signal confidence calculation
    confidence = 0.0
    if not classified_items:
        confidence = 0.0
        classification = OutcomeClassification.UNCERTAIN_OUTCOME
    else:
        # Sort by reliability descending
        sorted_items = sorted(classified_items, key=lambda x: x.reliability, reverse=True)
        # Probabilistic combination of independent signals: 1 - product(1 - weight_i)
        unconfidence = 1.0
        for item in sorted_items:
            if item.is_sufficient:
                unconfidence *= (1.0 - item.reliability)
        combined = 1.0 - unconfidence

        if conflicts:
            combined *= 0.60  # Penalize confidence if conflicting evidence exists

        confidence = round(max(0.0, min(1.0, combined)), 4)

        if confidence >= 0.85 and has_positive_confirmation and not conflicts:
            classification = OutcomeClassification.CONFIRMED_SUBMISSION
        elif confidence >= 0.60:
            classification = OutcomeClassification.PROBABLE_SUBMISSION
        elif has_validation_failure and not has_positive_confirmation:
            classification = OutcomeClassification.CONFIRMED_NON_SUBMISSION
        else:
            classification = OutcomeClassification.UNCERTAIN_OUTCOME

    # 5. Check for owner resolution in application confirmation_json
    owner_resolution = None
    if application.confirmation_json:
        try:
            parsed = json.loads(application.confirmation_json)
            if "owner_resolution" in parsed:
                owner_resolution = parsed["owner_resolution"]
        except Exception:
            pass

    return ReconciliationReport(
        application_id=application.id,
        job_id=application.job_id,
        company=company,
        title=title,
        classification=classification,
        confidence=confidence,
        evidence_count=len(classified_items),
        has_conflicts=bool(conflicts),
        conflict_notes=conflicts,
        evidence_items=classified_items,
        reconciliation_timestamp=datetime.now(UTC).isoformat(),
        owner_resolution=owner_resolution,
    )


def record_owner_resolution(
    db: Session,
    application: Application,
    *,
    decision: str,  # "confirmed", "non_submission", "dismissed"
    note: str = "",
) -> ReconciliationReport:
    """Record an owner-reviewed verdict on an uncertain application outcome.

    Maintains auditability without triggering automatic replay or execution.
    """
    if decision not in {"confirmed", "non_submission", "dismissed"}:
        raise ValueError(f"Invalid owner resolution decision: {decision}")

    resolution_data = {
        "decision": decision,
        "note": note,
        "resolved_at": datetime.now(UTC).isoformat(),
        "resolved_by": "owner",
    }

    current_conf: dict[str, Any] = {}
    if application.confirmation_json:
        try:
            current_conf = json.loads(application.confirmation_json)
        except Exception:
            current_conf = {}

    current_conf["owner_resolution"] = resolution_data
    application.confirmation_json = json.dumps(current_conf, sort_keys=True)
    db.add(application)
    db.commit()

    return reconcile_application_evidence(db, application)
