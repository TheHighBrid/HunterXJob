"""Recruiter CRM records: validation, dedup and interaction history.

Fake names and example.com addresses only."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.inbox_matcher import InboundIntent
from app.recruiter_crm import (
    CRMValidationError,
    RecruiterContact,
    RecruiterCRM,
    RecruiterInteraction,
    is_valid_email,
)

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def _contact(**kw) -> RecruiterContact:
    data = {"contact_id": "c-1", "company": "Example Corp", "full_name": "Pat Example", "email": "Pat@Example.com"}
    data.update(kw)
    return RecruiterContact(**data)


def test_email_validation():
    assert is_valid_email("pat@example.com")
    assert not is_valid_email("pat@")
    assert not is_valid_email("pat example@example.com")
    assert not is_valid_email("")


def test_contact_normalizes_email_and_strips_fields():
    contact = _contact(full_name="  Pat Example ", company=" Example Corp ")
    assert contact.email == "pat@example.com"
    assert contact.full_name == "Pat Example"
    assert contact.company == "Example Corp"
    assert contact.relationship_stage == "identified"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("company", "", "company"),
        ("full_name", " ", "full_name"),
        ("email", "broken", "email"),
        ("relationship_stage", "bff", "relationship_stage"),
        ("linkedin_url", "ftp://example.com/x", "linkedin_url"),
        ("contact_id", "", "contact_id"),
    ],
)
def test_contact_validation_errors(field, value, message):
    with pytest.raises(CRMValidationError, match=message):
        _contact(**{field: value})


def test_contact_email_is_optional():
    assert _contact(email="").email == ""


def _interaction(**kw) -> RecruiterInteraction:
    data = {
        "interaction_id": "i-1",
        "contact_id": "c-1",
        "direction": "inbound",
        "channel": "email",
        "summary": "Asked for availability",
        "occurred_at": T0,
    }
    data.update(kw)
    return RecruiterInteraction(**data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("direction", "sideways", "direction"),
        ("channel", "carrier_pigeon", "channel"),
        ("summary", "  ", "summary"),
        ("occurred_at", datetime(2026, 11, 1, 9, 0), "timezone"),  # noqa: DTZ001 - naive on purpose
        ("intent", "gossip", "intent"),
        ("contact_id", "", "contact_id"),
    ],
)
def test_interaction_validation_errors(field, value, message):
    with pytest.raises(CRMValidationError, match=message):
        _interaction(**{field: value})


def test_interaction_accepts_inbound_intent_enum():
    interaction = _interaction(intent=InboundIntent.INTERVIEW_REQUEST)
    assert interaction.intent == "interview_request"


def test_crm_dedupes_contacts_by_company_and_email():
    crm = RecruiterCRM()
    first = crm.add_contact(_contact())
    again = crm.add_contact(_contact(contact_id="c-2", email="pat@EXAMPLE.com", company="example corp"))
    assert again is first
    assert len(crm.contacts()) == 1


def test_crm_rejects_duplicate_contact_id_for_different_person():
    crm = RecruiterCRM()
    crm.add_contact(_contact())
    with pytest.raises(CRMValidationError, match="contact_id"):
        crm.add_contact(_contact(email="other@example.com"))


def test_interaction_requires_known_contact():
    crm = RecruiterCRM()
    with pytest.raises(CRMValidationError, match="unknown contact"):
        crm.record_interaction(_interaction())


def test_interaction_history_is_ordered_and_updates_last_contacted():
    crm = RecruiterCRM()
    crm.add_contact(_contact())
    crm.record_interaction(_interaction(interaction_id="i-2", occurred_at=T0 + timedelta(days=2)))
    crm.record_interaction(_interaction(interaction_id="i-1", occurred_at=T0))
    history = crm.interactions_for("c-1")
    assert [i.interaction_id for i in history] == ["i-1", "i-2"]
    assert crm.last_contacted_at("c-1") == T0 + timedelta(days=2)
    assert crm.last_contacted_at("missing") is None


def test_duplicate_interaction_id_rejected():
    crm = RecruiterCRM()
    crm.add_contact(_contact())
    crm.record_interaction(_interaction())
    with pytest.raises(CRMValidationError, match="interaction_id"):
        crm.record_interaction(_interaction())


def test_record_inbound_message_classifies_offline():
    crm = RecruiterCRM()
    crm.add_contact(_contact())
    interaction, classification = crm.record_inbound_message(
        interaction_id="i-9",
        contact_id="c-1",
        subject="Interview for Platform Engineer",
        body="We would like to schedule an interview with you next week.",
        occurred_at=T0,
        application_id="app-1",
    )
    assert classification.intent is InboundIntent.INTERVIEW_REQUEST
    assert interaction.intent == "interview_request"
    assert interaction.direction == "inbound"
    assert interaction.application_id == "app-1"
    assert crm.interactions_for("c-1") == [interaction]
