"""Ledger import tests. Every record here is made up; none is a real application."""

from __future__ import annotations

import json
from pathlib import Path

from app.ledger_import import (
    DOCUMENTED_UNVERIFIED,
    PRODUCTION_EVIDENCED,
    SCHEMA_VERSION,
    import_lines,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ledger" / "sample_export.jsonl"


def _record(**overrides):
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": "fake-x",
        "source_system": "jobtomatik",
        "company": "Example Co",
        "job_title": "Analyst",
        "job_url": "https://jobs.example.test/x",
        "submitted_at": "2026-10-01T10:00:00-04:00",
        "outcome": "confirmed_submission",
        "evidence": [{"type": "receipt_id", "source": "browser_dom", "ref": "evidence/fake-x/receipt.txt"}],
    }
    record.update(overrides)
    return json.dumps(record)


def test_fixture_labels_and_rejections():
    report = import_lines(FIXTURE.read_text(encoding="utf-8").splitlines())
    labels = {r.record_id: r.label for r in report.accepted}
    assert labels == {
        "fake-001": PRODUCTION_EVIDENCED,
        "fake-002": DOCUMENTED_UNVERIFIED,  # weak evidence (redirect only)
        "fake-003": DOCUMENTED_UNVERIFIED,  # probable, not confirmed
    }
    assert [r.record_id for r in report.rejected] == ["fake-004"]
    assert "personal data" in report.rejected[0].errors[0]
    summary = report.summary()
    assert summary["labels"] == {PRODUCTION_EVIDENCED: 1, DOCUMENTED_UNVERIFIED: 2}


def test_confirmed_without_evidence_pointer_is_unverified():
    report = import_lines([_record(evidence=[])])
    assert report.accepted[0].label == DOCUMENTED_UNVERIFIED


def test_dry_run_entry_is_not_submission_evidence():
    evidence = [{"type": "dry_run_ledger", "source": "browser_dom", "ref": "x"}]
    report = import_lines([_record(evidence=evidence)])
    assert not report.accepted
    assert "dry-run" in report.rejected[0].errors[0]


def test_missing_ref_and_bad_hash_rejected():
    evidence = [
        {"type": "receipt_id", "source": "browser_dom"},
        {"type": "receipt_id", "source": "browser_dom", "ref": "r", "sha256": "abc"},
    ]
    errors = import_lines([_record(evidence=evidence)]).rejected[0].errors
    assert any("ref" in e for e in errors)
    assert any("sha256" in e for e in errors)


def test_schema_field_checks():
    bad = _record(
        schema_version="v0",
        job_url="ftp://x",
        submitted_at="2026-10-01T10:00:00",
        outcome="sent",
        extra="y",
    )
    errors = import_lines([bad]).rejected[0].errors
    joined = " | ".join(errors)
    for needle in ("schema_version", "job_url", "timezone", "outcome", "unknown fields"):
        assert needle in joined


def test_duplicates_invalid_json_and_blank_lines():
    report = import_lines([_record(), "", "{not json", _record()])
    assert len(report.accepted) == 1
    assert [r.line for r in report.rejected] == [3, 4]
    assert "duplicate" in report.rejected[1].errors[0]


def test_missing_required_fields():
    errors = import_lines([json.dumps({"schema_version": SCHEMA_VERSION})]).rejected[0].errors
    assert errors[0].startswith("missing fields:")
