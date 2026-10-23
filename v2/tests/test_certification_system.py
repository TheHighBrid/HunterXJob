import json
from pathlib import Path

from app.certification_system import (
    DEFAULT_DB_PATH,
    EvidenceLedger,
    SubmissionEvidenceRecord,
    build_certification_dossier,
    evaluate_milestones,
    hash_payload,
)


def test_evidence_ledger_extracts_live_submissions():
    ledger = EvidenceLedger(DEFAULT_DB_PATH)
    records = ledger.extract_submitted_evidence()

    # Verify all 5 real submissions exist
    assert len(records) >= 5

    companies = {r.company.lower() for r in records}
    assert "wealthsimple" in companies
    assert "float" in companies
    assert "cohere" in companies

    for r in records:
        assert r.is_concrete_confirmation is True
        assert len(r.receipt_sha256) == 64
        assert len(r.evidence_snapshot_hash) == 64
        assert r.platform == "ashby"
        assert len(r.receipt_text) > 10


def test_milestone_evaluation_passes_all_gates():
    ledger = EvidenceLedger(DEFAULT_DB_PATH)
    records = ledger.extract_submitted_evidence()

    milestones = evaluate_milestones(records, min_live_submissions=5)

    # Gate 1: Form Engine & Vault
    assert milestones["gate1"].passed is True
    assert milestones["gate1"].score == 100.0
    assert len(milestones["gate1"].blockers) == 0

    # Gate 2: Supervised Live Submissions
    assert milestones["gate2"].passed is True
    assert milestones["gate2"].score == 100.0
    assert milestones["gate2"].metadata["credited_submissions"] >= 5
    assert milestones["gate2"].metadata["distinct_employers"] >= 3

    # Gate 3: Autonomous Safety Runtime
    assert milestones["gate3"].passed is True
    assert milestones["gate3"].score == 100.0
    assert len(milestones["gate3"].blockers) == 0


def test_build_certification_dossier_generates_valid_json(tmp_path):
    target = tmp_path / "hunterx-certification-dossier.json"
    dossier = build_certification_dossier(
        db_path=DEFAULT_DB_PATH,
        output_path=target,
        candidate_name="Mohamed Alem",
    )

    assert target.is_file()
    assert dossier["schema_version"] == "hunterx-certification-v2"
    assert dossier["overall_certification_status"] == "CERTIFIED"
    assert dossier["candidate"] == "Mohamed Alem"
    assert dossier["evidence_ledger"]["total_verified_submissions"] >= 5
    assert dossier["readiness"]["autonomous_scale_ready"] is True
    assert len(dossier["readiness"]["promotion_blockers"]) == 0

    # Verify JSON content on disk
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["overall_certification_status"] == "CERTIFIED"
    assert len(loaded["evidence_ledger"]["records"]) >= 5


def test_safety_invariants_fail_closed_on_tampered_evidence():
    # Inject a non-concrete submission without a valid confirmation keyword
    tampered_record = SubmissionEvidenceRecord(
        job_id="fake-id",
        company="fake-co",
        title="Software Engineer",
        platform="ashby",
        url="https://jobs.ashbyhq.com/fake/123",
        submitted_at="2026-10-23T00:00:00Z",
        receipt_text="Page loaded but no confirmation appeared",
        receipt_sha256="abc123" + "0" * 58,
        is_concrete_confirmation=False,  # Unconfirmed
        evidence_snapshot_hash="def456" + "0" * 58,
    )

    milestones = evaluate_milestones([tampered_record], min_live_submissions=1)

    # Gate 2 must fail closed
    assert milestones["gate2"].passed is False
    assert "concrete_confirmation_signals_present" in milestones["gate2"].blockers
