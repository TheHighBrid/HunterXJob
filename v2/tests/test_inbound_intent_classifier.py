"""Deterministic offline classifier for inbound recruiter messages."""
from __future__ import annotations

import pytest

from app.inbox_matcher import EmailIntent, InboundIntent, classify_email_intent, classify_inbound_message

CASES = [
    (
        "Update on your application",
        "Thank you for your interest. Unfortunately, we have decided not to move forward with your application.",
        InboundIntent.REJECTION,
    ),
    (
        "Your application",
        "After careful consideration we will be pursuing other candidates whose experience more closely matches.",
        InboundIntent.REJECTION,
    ),
    (
        "Next steps",
        "We would like to schedule an interview. Please share your availability for a 30 minute call.",
        InboundIntent.INTERVIEW_REQUEST,
    ),
    (
        "Interview invitation",
        "We'd love to invite you to interview with the team. Book a slot here: https://calendly.com/example/30min",
        InboundIntent.INTERVIEW_REQUEST,
    ),
    (
        "Coding exercise",
        "As a next step please complete the online assessment at https://www.hackerrank.com/test/example within 5 days.",
        InboundIntent.ASSESSMENT,
    ),
    (
        "Take-home",
        "Attached is a take-home assignment for the role.",
        InboundIntent.ASSESSMENT,
    ),
    (
        "Offer of employment",
        "We are delighted to extend an offer to you. Your offer letter is attached.",
        InboundIntent.OFFER,
    ),
    (
        "Quick question",
        "Could you please confirm your work authorization status and expected start date?",
        InboundIntent.INFO_REQUEST,
    ),
    (
        "We received your application",
        "Thank you for applying! This is an automated message, please do not reply.",
        InboundIntent.AUTO_ACK,
    ),
    (
        "Hello",
        "Hope your week is going well.",
        InboundIntent.UNKNOWN,
    ),
]


@pytest.mark.parametrize(("subject", "body", "expected"), CASES)
def test_classifies_expected_intent(subject, body, expected):
    result = classify_inbound_message(subject, body)
    assert result.intent is expected


@pytest.mark.parametrize(("subject", "body", "expected"), CASES)
def test_classification_is_deterministic(subject, body, expected):
    first = classify_inbound_message(subject, body)
    assert all(classify_inbound_message(subject, body) == first for _ in range(3))
    assert first.intent is expected


def test_intent_values_are_the_wave3_set():
    assert {i.value for i in InboundIntent} == {
        "rejection",
        "interview_request",
        "assessment",
        "offer",
        "info_request",
        "auto_ack",
        "unknown",
    }


def test_unknown_needs_review():
    result = classify_inbound_message("Hello", "Just saying hi.")
    assert result.intent is InboundIntent.UNKNOWN
    assert result.needs_review is True
    assert result.matched_intents == ()


def test_auto_ack_wrapped_rejection_is_rejection():
    result = classify_inbound_message(
        "Thank you for applying",
        "We have received your application. Unfortunately we will not be moving forward at this time.",
    )
    assert result.intent is InboundIntent.REJECTION
    assert result.needs_review is False


def test_info_request_inside_interview_request_is_interview():
    result = classify_inbound_message(
        "Interview",
        "We would like to schedule an interview. Could you please share your availability?",
    )
    assert result.intent is InboundIntent.INTERVIEW_REQUEST


def test_conflicting_strong_intents_go_to_review():
    result = classify_inbound_message(
        "Update",
        "Unfortunately we will not be moving forward. We would like to schedule an interview for another role.",
    )
    assert result.intent is InboundIntent.UNKNOWN
    assert result.needs_review is True
    assert set(result.matched_intents) == {"rejection", "interview_request"}
    assert "ambiguous" in result.reason


def test_negated_offer_is_not_an_offer():
    result = classify_inbound_message("Clarification", "Please note this message is not an offer of employment.")
    assert result.intent is not InboundIntent.OFFER


def test_empty_message_is_unknown():
    result = classify_inbound_message("", "")
    assert result.intent is InboundIntent.UNKNOWN
    assert result.needs_review is True


def test_existing_email_intent_classifier_unchanged():
    assert classify_email_intent("Thanks for applying", "We received your application.") is EmailIntent.CONFIRMATION
    assert classify_email_intent("Interview", "We invite you to interview next week") is EmailIntent.INTERVIEW_INVITATION
