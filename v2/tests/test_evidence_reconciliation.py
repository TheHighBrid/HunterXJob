"""Unit tests for native evidence intelligence and outcome reconciliation."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.evidence_reconciliation import (
    EvidenceSource,
    EvidenceType,
    OutcomeClassification,
    classify_single_evidence,
    compute_snapshot_hash,
    reconcile_application_evidence,
    record_owner_resolution,
)
from app.models import Application, Job, PipelineEvent, PipelineStage, SubmissionEvidence


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_compute_snapshot_hash_deterministic():
    payload = {"company": "Acme", "url": "https://example.com/thanks", "id": 123}
    h1 = compute_snapshot_hash(payload)
    h2 = compute_snapshot_hash({"url": "https://example.com/thanks", "id": 123, "company": "Acme"})
    assert h1 == h2
    assert len(h1) == 64


def test_classify_single_evidence_signals(db_session):
    job = Job(id="job-1", title="Engineer", company="Cohere", source="ashby", external_id="123", url="https://example.com/job/123")
    app = Application(id="app-1", job_id=job.id, stage=PipelineStage.submitted.value)
    db_session.add_all([job, app])
    db_session.commit()

    ev = SubmissionEvidence(
        id="ev-1",
        application_id=app.id,
        kind="submission",
        sufficient=True,
        candidate_id="COHERE-998822",
        confirmation_text="Application Success — Your application has been submitted!",
        final_url="https://jobs.ashbyhq.com/cohere/thanks",
        screenshot_path="/data/screenshots/post_submit.png",
        payload_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    db_session.add(ev)
    db_session.commit()

    signals = classify_single_evidence(ev)
    assert len(signals) == 4
    types = {s.evidence_type for s in signals}
    assert EvidenceType.RECEIPT_ID in types
    assert EvidenceType.CONFIRMATION_PAGE in types
    assert EvidenceType.HTTP_REDIRECT in types
    assert EvidenceType.SCREENSHOT_PROOF in types

    receipt = next(s for s in signals if s.evidence_type is EvidenceType.RECEIPT_ID)
    assert receipt.receipt_reference == "COHERE-998822"
    assert receipt.reliability >= 0.90
    assert len(receipt.snapshot_hash) == 64


def test_multi_signal_reconciliation_confirmed(db_session):
    job = Job(id="job-2", title="Staff Eng", company="Wealthsimple", source="ashby", external_id="ws-1", url="https://example.com/job/ws-1")
    app = Application(id="app-2", job_id=job.id, stage=PipelineStage.submitted.value)
    db_session.add_all([job, app])
    db_session.commit()

    # Record strong submission evidence
    ev = SubmissionEvidence(
        id="ev-2",
        application_id=app.id,
        kind="submission",
        sufficient=True,
        candidate_id="WS-REC-4411",
        confirmation_text="Overview Application Success — Thanks for applying to Wealthsimple 🙌",
        final_url="https://jobs.ashbyhq.com/wealthsimple/success",
        screenshot_path="/data/screenshots/ws.png",
        payload_hash="3bab1d56f8049f87a9f4ba714a1756404a56252fb48574f34867f701853c1742",
    )
    db_session.add(ev)
    db_session.commit()

    report = reconcile_application_evidence(db_session, app)
    assert report.classification is OutcomeClassification.CONFIRMED_SUBMISSION
    assert report.confidence >= 0.95
    assert report.has_conflicts is False
    assert report.evidence_count >= 3


def test_single_signal_probable_outcome(db_session):
    job = Job(id="job-3", title="Operations Lead", company="Float", source="lever", external_id="fl-1", url="https://example.com/job/fl-1")
    app = Application(id="app-3", job_id=job.id, stage=PipelineStage.submitted.value)
    db_session.add_all([job, app])
    db_session.commit()

    # Only redirect URL without receipt ID or concrete text
    ev = SubmissionEvidence(
        id="ev-3",
        application_id=app.id,
        kind="submission",
        sufficient=True,
        final_url="https://jobs.lever.co/float/confirmation",
    )
    db_session.add(ev)
    db_session.commit()

    report = reconcile_application_evidence(db_session, app)
    assert report.classification is OutcomeClassification.PROBABLE_SUBMISSION
    assert 0.60 <= report.confidence < 0.85


def test_conflict_detection_reduces_confidence(db_session):
    job = Job(id="job-4", title="Analyst", company="1Password", source="ashby", external_id="1p-1", url="https://example.com/job/1p-1")
    app = Application(
        id="app-4",
        job_id=job.id,
        stage=PipelineStage.submission_uncertain.value,
        last_error="Form validation error: required field missing",
    )
    db_session.add_all([job, app])
    db_session.commit()

    # Conflicting evidence: validation error recorded, but redirect URL observed
    ev = SubmissionEvidence(
        id="ev-4",
        application_id=app.id,
        kind="submission",
        sufficient=True,
        final_url="https://jobs.ashbyhq.com/1password/success",
    )
    db_session.add(ev)
    db_session.commit()

    report = reconcile_application_evidence(db_session, app)
    assert report.has_conflicts is True
    assert any("Conflicting evidence" in note for note in report.conflict_notes)
    # Confidence is penalized
    assert report.confidence < 0.60
    assert report.classification is OutcomeClassification.UNCERTAIN_OUTCOME


def test_delayed_inbound_confirmation_matching(db_session):
    job = Job(id="job-5", title="Developer", company="Jobber", source="ashby", external_id="jb-1", url="https://example.com/job/jb-1")
    app = Application(id="app-5", job_id=job.id, stage=PipelineStage.submitted.value)
    db_session.add_all([job, app])
    db_session.commit()

    # Initial weak evidence
    ev = SubmissionEvidence(
        id="ev-5",
        application_id=app.id,
        kind="submission",
        sufficient=False,
        final_url="https://jobs.ashbyhq.com/jobber/post",
    )
    # Delayed inbound email event matching
    event = PipelineEvent(
        id="evnt-1",
        job_id=job.id,
        from_stage=PipelineStage.ready_to_apply.value,
        to_stage=PipelineStage.submitted.value,
        message="Inbound email confirmation received from Jobber: Thanks for your application",
        payload_json=json.dumps({"intent": "confirmation", "subject": "Application Received"}),
    )
    db_session.add_all([ev, event])
    db_session.commit()

    report = reconcile_application_evidence(db_session, app)
    # Email signal is integrated
    email_items = [i for i in report.evidence_items if i.evidence_type is EvidenceType.CONFIRMATION_EMAIL]
    assert len(email_items) == 1
    assert email_items[0].source is EvidenceSource.INBOUND_EMAIL
    assert report.confidence >= 0.85
    assert report.classification is OutcomeClassification.CONFIRMED_SUBMISSION


def test_owner_resolution_records_verdict_without_state_mutation(db_session):
    job = Job(id="job-6", title="Architect", company="CloudCorp", source="greenhouse", external_id="cc-1", url="https://example.com/job/cc-1")
    app = Application(id="app-6", job_id=job.id, stage=PipelineStage.submission_uncertain.value)
    db_session.add_all([job, app])
    db_session.commit()

    initial_stage = app.stage

    report = record_owner_resolution(
        db_session,
        app,
        decision="confirmed",
        note="Verified confirmation manually via candidate portal",
    )

    # Invariant: Does NOT mutate application stage
    assert app.stage == initial_stage
    assert report.owner_resolution is not None
    assert report.owner_resolution["decision"] == "confirmed"
    assert report.owner_resolution["resolved_by"] == "owner"
    assert "Verified confirmation manually" in report.owner_resolution["note"]

    # Invariant: Rejects invalid decisions
    with pytest.raises(ValueError):
        record_owner_resolution(db_session, app, decision="auto_retry")
