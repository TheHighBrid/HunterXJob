"""Supervised follow-up: approval binds a payload hash, drift blocks sending,
send keys are idempotent, and the send step is disabled by default.

No email or network send exists on this path; it only records an intent."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.follow_up import (
    FollowUpDraft,
    FollowUpSendLedger,
    SupervisedFollowUp,
    SupervisedFollowUpError,
    canonical_follow_up_payload,
    follow_up_payload_hash,
    follow_up_send_key,
)

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
RECIPIENT = "recruiter@example.com"


def _draft(**overrides) -> FollowUpDraft:
    base = FollowUpDraft(
        application_id="app-1",
        job_id="job-1",
        company="Example Corp",
        title="Platform Engineer",
        follow_up_number=1,
        subject="Following up on application for Platform Engineer - Test Candidate",
        body="Dear Example Corp Hiring Team,\n\nChecking in.\n\nTest Candidate",
    )
    return replace(base, **overrides)


def _approved(draft: FollowUpDraft | None = None) -> SupervisedFollowUp:
    followup = SupervisedFollowUp(draft=draft or _draft(), recipient=RECIPIENT)
    followup.approve("owner", followup.expected_acknowledgment(), now=NOW)
    return followup


def test_canonical_payload_is_stable_and_order_independent():
    payload = canonical_follow_up_payload(_draft(), RECIPIENT)
    assert payload == canonical_follow_up_payload(_draft(), " Recruiter@Example.com ")
    assert '"recipient":"recruiter@example.com"' in payload
    assert payload.index('"application_id"') < payload.index('"subject"')


def test_payload_hash_is_sha256_hex_and_changes_with_body():
    first = follow_up_payload_hash(_draft(), RECIPIENT)
    assert len(first) == 64
    assert int(first, 16) >= 0
    assert first == follow_up_payload_hash(_draft(), RECIPIENT)
    assert first != follow_up_payload_hash(_draft(body="Different body"), RECIPIENT)


def test_send_key_is_deterministic_and_scoped():
    digest = follow_up_payload_hash(_draft(), RECIPIENT)
    key = follow_up_send_key("app-1", 1, digest)
    assert key == follow_up_send_key("app-1", 1, digest)
    assert key != follow_up_send_key("app-1", 2, digest)
    assert key != follow_up_send_key("app-2", 1, digest)
    assert key.startswith("fu1-")


def test_send_key_rejects_bad_inputs():
    with pytest.raises(SupervisedFollowUpError):
        follow_up_send_key("", 1, "a" * 64)
    with pytest.raises(SupervisedFollowUpError):
        follow_up_send_key("app-1", 0, "a" * 64)
    with pytest.raises(SupervisedFollowUpError):
        follow_up_send_key("app-1", 1, "not-a-hash")


def test_unapproved_follow_up_is_blocked():
    followup = SupervisedFollowUp(draft=_draft(), recipient=RECIPIENT)
    assert "follow_up_not_approved" in followup.preflight(now=NOW)


def test_approval_requires_exact_acknowledgment_and_approver():
    followup = SupervisedFollowUp(draft=_draft(), recipient=RECIPIENT)
    with pytest.raises(SupervisedFollowUpError, match="acknowledgment"):
        followup.approve("owner", "yes", now=NOW)
    with pytest.raises(SupervisedFollowUpError, match="approver"):
        followup.approve("  ", followup.expected_acknowledgment(), now=NOW)
    assert followup.approval_status == "unapproved"


def test_approval_refused_for_invalid_recipient_or_empty_body():
    bad_recipient = SupervisedFollowUp(draft=_draft(), recipient="not-an-email")
    with pytest.raises(SupervisedFollowUpError, match="recipient"):
        bad_recipient.approve("owner", bad_recipient.expected_acknowledgment(), now=NOW)
    empty = SupervisedFollowUp(draft=_draft(body="   "), recipient=RECIPIENT)
    with pytest.raises(SupervisedFollowUpError, match="empty"):
        empty.approve("owner", empty.expected_acknowledgment(), now=NOW)


def test_approved_follow_up_passes_preflight():
    followup = _approved()
    assert followup.preflight(now=NOW) == []
    assert followup.approved_payload_hash == follow_up_payload_hash(_draft(), RECIPIENT)


def test_payload_drift_after_approval_blocks_send():
    followup = _approved()
    followup.draft = replace(followup.draft, body="Edited after approval")
    assert "follow_up_payload_drifted" in followup.preflight(now=NOW)
    ledger = FollowUpSendLedger()
    with pytest.raises(SupervisedFollowUpError, match="follow_up_payload_drifted"):
        ledger.record_send_intent(followup, enabled=True, now=NOW)
    assert ledger.records() == []


def test_recipient_change_after_approval_is_drift():
    followup = _approved()
    followup.recipient = "someone-else@example.com"
    assert "follow_up_payload_drifted" in followup.preflight(now=NOW)


def test_expired_approval_blocks_send():
    followup = _approved()
    later = NOW + timedelta(hours=73)
    assert "follow_up_approval_expired" in followup.preflight(now=later)


def test_revoked_approval_blocks_send():
    followup = _approved()
    followup.revoke()
    assert followup.approval_status == "revoked"
    assert "follow_up_not_approved" in followup.preflight(now=NOW)


def test_send_is_disabled_by_default():
    ledger = FollowUpSendLedger()
    with pytest.raises(SupervisedFollowUpError, match="follow_up_send_disabled"):
        ledger.record_send_intent(_approved(), now=NOW)
    assert ledger.records() == []


def test_enabled_send_records_intent_only_once():
    followup = _approved()
    ledger = FollowUpSendLedger()
    record = ledger.record_send_intent(followup, enabled=True, now=NOW)
    assert record.status == "send_intent_recorded"
    assert record.delivered is False
    assert record.approved_by == "owner"
    assert record.send_key == follow_up_send_key("app-1", 1, followup.approved_payload_hash)
    assert ledger.has(record.send_key)
    with pytest.raises(SupervisedFollowUpError, match="follow_up_already_recorded"):
        ledger.record_send_intent(followup, enabled=True, now=NOW)
    assert len(ledger.records()) == 1


def test_reapproving_identical_payload_keeps_same_send_key():
    ledger = FollowUpSendLedger()
    ledger.record_send_intent(_approved(), enabled=True, now=NOW)
    with pytest.raises(SupervisedFollowUpError, match="follow_up_already_recorded"):
        ledger.record_send_intent(_approved(), enabled=True, now=NOW)


def test_second_step_gets_its_own_key():
    ledger = FollowUpSendLedger()
    first = ledger.record_send_intent(_approved(), enabled=True, now=NOW)
    second = ledger.record_send_intent(_approved(_draft(follow_up_number=2)), enabled=True, now=NOW)
    assert first.send_key != second.send_key
    assert len(ledger.records()) == 2


def test_supervised_path_never_touches_smtp(monkeypatch):
    import smtplib

    def refuse(*_args, **_kwargs):  # pragma: no cover - only hit on a bug
        raise AssertionError("supervised follow-up must not open SMTP")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    FollowUpSendLedger().record_send_intent(_approved(), enabled=True, now=NOW)
