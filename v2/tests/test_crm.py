"""Unit tests for recruiter CRM and supervised interview intelligence."""
from __future__ import annotations

import pytest

from app.crm import (
    InterviewStage,
    draft_interview_response,
    process_inbound_recruiter_interaction,
)
from app.inbox_matcher import EmailIntent
from app.profile import Fact, VerifiedProfile


@pytest.fixture
def test_profile():
    return VerifiedProfile(
        facts=[
            Fact("c1", "contact", {"field": "first_name", "value": "Mohamed"}),
            Fact("c2", "contact", {"field": "last_name", "value": "Alem"}),
            Fact("c3", "contact", {"field": "email", "value": "mo@example.com"}),
            Fact("c4", "contact", {"field": "phone", "value": "613-555-0199"}),
            Fact("s1", "skill", {"name": "Python"}),
        ]
    )


def test_draft_interview_response(test_profile):
    draft = draft_interview_response(
        test_profile,
        company="Cohere",
        title="Forward Deployed Engineer",
        intent=EmailIntent.INTERVIEW_INVITATION,
        recruiter_name="Sarah",
    )
    assert "Hi Sarah," in draft
    assert "Forward Deployed Engineer" in draft
    assert "Cohere" in draft
    assert "Mohamed Alem" in draft
    assert "mo@example.com" in draft


def test_process_inbound_recruiter_invitation(test_profile):
    contact, record = process_inbound_recruiter_interaction(
        test_profile,
        sender_email="sarah.recruiter@cohere.com",
        sender_name="Sarah Jenkins",
        subject="Invitation to interview at Cohere",
        body="We loved your application and would like to schedule a phone screen call with our team.",
        company="Cohere",
        title="AI Engineer",
        application_id="app-10",
        job_id="job-10",
    )

    assert contact.name == "Sarah Jenkins"
    assert contact.email == "sarah.recruiter@cohere.com"
    assert contact.company == "Cohere"

    assert record.stage is InterviewStage.SCREENING_CALL
    assert "Cohere" in record.company
    assert len(record.recommended_preparation) >= 2
    assert record.draft_response is not None
    assert "Hi Sarah Jenkins," in record.draft_response


def test_process_inbound_assessment(test_profile):
    contact, record = process_inbound_recruiter_interaction(
        test_profile,
        sender_email="talent@wealthsimple.com",
        sender_name="Talent Team",
        subject="Next steps: Technical Assessment for Wealthsimple",
        body="Please complete the following take-home coding assessment within 48 hours.",
        company="Wealthsimple",
        title="Software Engineer",
        application_id="app-11",
        job_id="job-11",
    )

    assert record.stage is InterviewStage.TECHNICAL_INTERVIEW
    assert "assessment" in record.draft_response.lower()
