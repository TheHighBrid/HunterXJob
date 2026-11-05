"""Career Intelligence and Recruiter Relationship Management (CRM).

Supervised recruitment lifecycle intelligence for HunterXJob:
- Models recruiter and hiring contacts tied to employers and applications.
- Tracks interview stages (Screening, Technical, Behavioral, Final, Offer).
- Synthesizes polite, personalized follow-up and thank-you message drafts.
- Supervised invariant: Never sends outbound messages or makes commitments autonomously.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inbox_matcher import EmailIntent, classify_email_intent
from app.models import Application, Job, PipelineStage
from app.profile import VerifiedProfile

logger = logging.getLogger(__name__)


class InterviewStage(str, Enum):
    INITIAL_REVIEW = "initial_review"
    SCREENING_CALL = "screening_call"
    TECHNICAL_INTERVIEW = "technical_interview"
    BEHAVIORAL_INTERVIEW = "behavioral_interview"
    SYSTEM_DESIGN = "system_design"
    EXECUTIVE_FINAL = "executive_final"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


@dataclass
class RecruiterContact:
    company: str
    name: str
    email: str
    title: str = ""
    phone: str = ""
    notes: str = ""
    first_contacted_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InterviewRecord:
    application_id: str
    job_id: str
    company: str
    title: str
    stage: InterviewStage
    scheduled_at: str | None = None
    interviewer_name: str | None = None
    notes: str = ""
    recommended_preparation: list[str] = field(default_factory=list)
    draft_response: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stage"] = self.stage.value
        return data


def draft_interview_response(
    profile: VerifiedProfile,
    company: str,
    title: str,
    intent: EmailIntent,
    recruiter_name: str | None = None,
) -> str:
    """Draft a polite, supervised response template for incoming recruiter communications."""
    candidate_name = profile.full_name or profile.contact.get("first_name", "Mohamed Alem")
    email = profile.contact.get("email", "")
    phone = profile.contact.get("phone", "")
    greeting = f"Hi {recruiter_name}," if recruiter_name else "Dear Hiring Team,"

    if intent == EmailIntent.INTERVIEW_INVITATION:
        return (
            f"{greeting}\n\n"
            f"Thank you very much for inviting me to interview for the {title} position at {company}. "
            f"I am very excited about the opportunity to speak with the team.\n\n"
            f"I would be glad to coordinate a time that works best for your schedule. "
            f"Please let me know your preferred dates or if there is a scheduling link I should use.\n\n"
            f"Looking forward to speaking with you.\n\n"
            f"Best regards,\n"
            f"{candidate_name}\n"
            f"{email}\n"
            f"{phone}"
        )
    elif intent == EmailIntent.ASSESSMENT:
        return (
            f"{greeting}\n\n"
            f"Thank you for sharing the assessment details for the {title} role at {company}. "
            f"I have received the instructions and will complete the exercise within the requested timeframe.\n\n"
            f"Please let me know if you need any additional information from my side in the interim.\n\n"
            f"Best regards,\n"
            f"{candidate_name}\n"
            f"{email}"
        )
    else:
        return (
            f"{greeting}\n\n"
            f"Thank you for the update regarding the {title} position at {company}.\n\n"
            f"Sincerely,\n"
            f"{candidate_name}\n"
            f"{email}"
        )


def process_inbound_recruiter_interaction(
    profile: VerifiedProfile,
    sender_email: str,
    sender_name: str,
    subject: str,
    body: str,
    company: str,
    title: str,
    application_id: str,
    job_id: str,
) -> tuple[RecruiterContact, InterviewRecord]:
    """Analyze incoming recruiter interaction and generate supervised drafts."""
    intent = classify_email_intent(subject, body)

    contact = RecruiterContact(
        company=company,
        name=sender_name or sender_email.split("@")[0].replace(".", " ").title(),
        email=sender_email,
        title="Recruiter / Talent Acquisition",
        notes=f"Inbound subject: {subject}",
    )

    if intent == EmailIntent.INTERVIEW_INVITATION:
        stage = InterviewStage.SCREENING_CALL
        prep = [
            f"Review {company} mission and recent public engineering announcements",
            f"Prepare examples of key skills for {title}",
            "Review verified facts in Candidate Knowledge Book",
        ]
    elif intent == EmailIntent.ASSESSMENT:
        stage = InterviewStage.TECHNICAL_INTERVIEW
        prep = ["Review technical problem solving and algorithm complexity"]
    elif intent == EmailIntent.REJECTION:
        stage = InterviewStage.REJECTED
        prep = []
    else:
        stage = InterviewStage.INITIAL_REVIEW
        prep = []

    draft = draft_interview_response(profile, company, title, intent, contact.name)

    record = InterviewRecord(
        application_id=application_id,
        job_id=job_id,
        company=company,
        title=title,
        stage=stage,
        interviewer_name=contact.name,
        notes=f"Detected intent: {intent.value}. Subject: {subject}",
        recommended_preparation=prep,
        draft_response=draft,
    )

    return contact, record
