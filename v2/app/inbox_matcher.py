"""Inbox Confirmation Matching and Recruiter Communication Classifier.

Analyzes incoming email messages (via IMAP, webhook, or polling feeds) and:
1. Matches emails to existing applications by company name, title, reference IDs, and ATS domain.
2. Classifies recruiter intent (Confirmation, Interview Invitation, Assessment, Rejection).
3. Updates the application lifecycle state and logs verifiable audit events.
4. Alerts the user or schedules next actions accordingly.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dedup import normalize_company, normalize_title
from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask

logger = logging.getLogger(__name__)


class EmailIntent(str, Enum):
    CONFIRMATION = "confirmation"
    INTERVIEW_INVITATION = "interview_invitation"
    ASSESSMENT = "assessment"
    REJECTION = "rejection"
    GENERAL = "general"


CONFIRMATION_PATTERNS = [
    re.compile(r"thank\s+you\s+for\s+applying", re.IGNORECASE),
    re.compile(r"thanks\s+for\s+applying", re.IGNORECASE),
    re.compile(r"application\s+(?:has\s+been\s+)?received", re.IGNORECASE),
    re.compile(r"received\s+your\s+application", re.IGNORECASE),
    re.compile(r"application\s+submitted\s+successfully", re.IGNORECASE),
]

INTERVIEW_PATTERNS = [
    re.compile(r"invite\s+you\s+to\s+interview", re.IGNORECASE),
    re.compile(r"like\s+to\s+schedule\s+a\s+(?:call|phone\s+screen|chat|meeting|interview)", re.IGNORECASE),
    re.compile(r"invitation\s+to\s+interview", re.IGNORECASE),
    re.compile(r"next\s+step\s+in\s+our\s+interview\s+process", re.IGNORECASE),
    re.compile(r"calendly\.com|goodtime\.io|chilipiper\.com", re.IGNORECASE),
]

REJECTION_PATTERNS = [
    re.compile(r"not\s+moving\s+forward", re.IGNORECASE),
    re.compile(r"pursue\s+other\s+candidates", re.IGNORECASE),
    re.compile(r"decided\s+to\s+move\s+forward\s+with\s+other", re.IGNORECASE),
    re.compile(r"unfortunately.*(?:cannot|not|unable)", re.IGNORECASE),
    re.compile(r"will\s+not\s+be\s+moving\s+forward", re.IGNORECASE),
]

ASSESSMENT_PATTERNS = [
    re.compile(r"complete\s+the\s+following\s+.*assessment", re.IGNORECASE),
    re.compile(r"online\s+assessment|technical\s+assessment|coding\s+assessment", re.IGNORECASE),
    re.compile(r"coding\s+challenge", re.IGNORECASE),
    re.compile(r"take-home\s+(?:exercise|project|assignment|assessment|challenge)", re.IGNORECASE),
    re.compile(r"hackerrank\.com|codesignal\.com|codility\.com", re.IGNORECASE),
]

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


@dataclass
class EmailMessage:
    sender: str
    recipient: str
    subject: str
    body: str
    date: datetime | None = None
    message_id: str | None = None


@dataclass
class EmailMatchResult:
    matched: bool
    application_id: str | None = None
    job_id: str | None = None
    intent: EmailIntent = EmailIntent.GENERAL
    confidence: float = 0.0
    extracted_reference_id: str | None = None
    extracted_links: list[str] = field(default_factory=list)
    summary: str = ""


def classify_email_intent(subject: str, body: str) -> EmailIntent:
    """Classify the primary intent of an email message."""
    content = f"{subject} \n {body}"

    # Priority 1: Interview Invitation
    if any(p.search(content) for p in INTERVIEW_PATTERNS):
        return EmailIntent.INTERVIEW_INVITATION

    # Priority 2: Technical Assessment
    if any(p.search(content) for p in ASSESSMENT_PATTERNS):
        return EmailIntent.ASSESSMENT

    # Priority 3: Rejection
    if any(p.search(content) for p in REJECTION_PATTERNS):
        return EmailIntent.REJECTION

    # Priority 4: Application Confirmation
    if any(p.search(content) for p in CONFIRMATION_PATTERNS):
        return EmailIntent.CONFIRMATION

    return EmailIntent.GENERAL


def match_email_to_application(db: Session, email: EmailMessage) -> EmailMatchResult:
    """Find the best matching application for an incoming email."""
    intent = classify_email_intent(email.subject, email.body)
    content = f"{email.subject} \n {email.body}".lower()

    # Extract any scheduling or assessment URLs
    all_urls = URL_PATTERN.findall(email.body)
    priority_links = [
        u for u in all_urls
        if any(token in u.lower() for token in ("calendly", "goodtime", "interview", "schedule", "hackerrank", "codesignal"))
    ]

    # Query all active or submitted applications
    stmt = select(Application, Job).join(Job, Job.id == Application.job_id)
    candidates = db.execute(stmt).all()

    best_app = None
    best_job = None
    best_score = 0.0
    matched_ref = None

    for app, job in candidates:
        score = 0.0

        # 1. Company match
        norm_job_co = normalize_company(job.company or "")
        norm_email_co = normalize_company(email.subject)
        if norm_job_co and (norm_job_co in norm_email_co or norm_job_co in normalize_company(email.body)):
            score += 0.50
        elif job.company and job.company.lower() in content:
            score += 0.40

        # 2. Title match
        norm_title = normalize_title(job.title or "")
        if norm_title:
            title_words = set(norm_title.split())
            body_words = set(content.split())
            overlap = title_words & body_words
            if overlap:
                score += 0.30 * (len(overlap) / max(len(title_words), 1))

        # 3. Exact Candidate/Reference ID match if stored on application
        if app.confirmation_json:
            try:
                conf = json.loads(app.confirmation_json)
                cid = conf.get("candidate_id")
                if cid and cid.lower() in content:
                    score += 0.50
                    matched_ref = cid
            except (ValueError, TypeError, AttributeError):
                logger.debug("Ignoring unreadable confirmation_json on application %s", app.id)

        # 4. ATS domain match (Greenhouse, Lever, Ashby, etc.)
        if job.platform and job.platform in email.sender.lower():
            score += 0.15

        if score > best_score:
            best_score = score
            best_app = app
            best_job = job

    # Threshold for a positive match
    if best_app and best_job and best_score >= 0.50:
        summary = f"Matched to {best_job.company} - {best_job.title} (score={round(best_score, 2)}, intent={intent.value})"
        return EmailMatchResult(
            matched=True,
            application_id=best_app.id,
            job_id=best_job.id,
            intent=intent,
            confidence=round(best_score, 2),
            extracted_reference_id=matched_ref,
            extracted_links=priority_links,
            summary=summary,
        )

    return EmailMatchResult(
        matched=False,
        intent=intent,
        confidence=round(best_score, 2),
        summary=f"No matching application found for email subject '{email.subject}'",
    )


def process_inbox_email(db: Session, email: EmailMessage) -> EmailMatchResult:
    """Match email and execute lifecycle state transitions and review tasks."""
    result = match_email_to_application(db, email)
    if not result.matched or not result.application_id:
        return result

    app = db.get(Application, result.application_id)
    job = db.get(Job, result.job_id)
    if not app or not job:
        return result

    # 1. Confirmation: update confirmation record
    if result.intent == EmailIntent.CONFIRMATION:
        db.add(PipelineEvent(
            job_id=job.id,
            from_stage=job.stage,
            to_stage=job.stage,
            message=f"Email receipt confirmed from {email.sender}: {email.subject[:100]}",
            payload_json=json.dumps({"sender": email.sender, "subject": email.subject, "intent": "confirmation"}),
        ))
        db.commit()

    # 2. Interview Invitation: open high-priority review task
    elif result.intent == EmailIntent.INTERVIEW_INVITATION:
        db.add(ReviewTask(
            application_id=app.id,
            job_id=job.id,
            reason_code="interview_invitation",
            title=f"Interview Invitation: {job.company} - {job.title}",
            detail=f"From: {email.sender}\nSubject: {email.subject}\nLinks: {', '.join(result.extracted_links)}\n\n{email.body[:500]}",
            url=result.extracted_links[0] if result.extracted_links else None,
        ))
        db.add(PipelineEvent(
            job_id=job.id,
            from_stage=job.stage,
            to_stage=job.stage,
            message=f"Interview invitation received from {job.company}",
            payload_json=json.dumps({"sender": email.sender, "links": result.extracted_links}),
        ))
        db.commit()

    # 3. Technical Assessment
    elif result.intent == EmailIntent.ASSESSMENT:
        db.add(ReviewTask(
            application_id=app.id,
            job_id=job.id,
            reason_code="assessment_requested",
            title=f"Assessment Requested: {job.company} - {job.title}",
            detail=f"From: {email.sender}\nSubject: {email.subject}\nLinks: {', '.join(result.extracted_links)}",
            url=result.extracted_links[0] if result.extracted_links else None,
        ))
        db.add(PipelineEvent(
            job_id=job.id,
            from_stage=job.stage,
            to_stage=job.stage,
            message=f"Assessment requested by {job.company}",
        ))
        db.commit()

    # 4. Rejection
    elif result.intent == EmailIntent.REJECTION:
        old_stage = job.stage
        app.stage = PipelineStage.rejected.value
        job.stage = PipelineStage.rejected.value
        db.add(PipelineEvent(
            job_id=job.id,
            from_stage=old_stage,
            to_stage=PipelineStage.rejected.value,
            message=f"Rejection email received from {job.company}",
        ))
        db.commit()

    return result


# ---------------------------------------------------------------------------
# Offline inbound-message intent classifier (Wave 3).
#
# Deterministic and rule-based: regex rules only, no LLM call, no network and
# no new dependency. Weak intents (auto_ack, info_request) give way to a single
# strong intent; two or more strong intents are ambiguous and go to review as
# ``unknown`` instead of guessing.
# ---------------------------------------------------------------------------


class InboundIntent(str, Enum):
    REJECTION = "rejection"
    INTERVIEW_REQUEST = "interview_request"
    ASSESSMENT = "assessment"
    OFFER = "offer"
    INFO_REQUEST = "info_request"
    AUTO_ACK = "auto_ack"
    UNKNOWN = "unknown"


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_INBOUND_RULES: tuple[tuple[InboundIntent, tuple[re.Pattern[str], ...]], ...] = (
    (InboundIntent.REJECTION, _rx(
        r"\bnot\s+(?:to\s+)?(?:be\s+)?mov(?:e|ing)\s+forward\b",
        r"\bpursu(?:e|ing)\s+other\s+candidates\b",
        r"\bmove\s+forward\s+with\s+other\s+(?:candidates|applicants)\b",
        r"\bposition\s+has\s+been\s+filled\b",
        r"\bnot\s+(?:been\s+)?selected\b",
        r"\bunable\s+to\s+offer\s+you\b",
    )),
    (InboundIntent.INTERVIEW_REQUEST, _rx(
        r"\b(?:schedule|book|arrange|set\s+up)\s+(?:an?\s+|your\s+)?(?:interview|phone\s+screen|call|chat)\b",
        r"\binvite\s+you\s+to\s+(?:an?\s+)?interview\b",
        r"\binterview\s+invitation\b|\binvitation\s+to\s+interview\b",
        r"\bavailability\s+for\s+an?\s+(?:interview|call|\d+\s*(?:-\s*)?min)",
        r"calendly\.com|goodtime\.io|chilipiper\.com",
    )),
    (InboundIntent.ASSESSMENT, _rx(
        r"\b(?:online|technical|coding|skills?)\s+(?:assessment|test)\b",
        r"\bcoding\s+(?:challenge|exercise)\b",
        r"\btake[-\s]home\s+(?:exercise|project|assignment|assessment|challenge)\b",
        r"hackerrank\.com|codesignal\.com|codility\.com|testgorilla\.com",
    )),
    (InboundIntent.OFFER, _rx(
        r"\boffer\s+of\s+employment\b",
        r"\boffer\s+letter\b",
        r"\b(?:extend|make|making|extending)\s+(?:you\s+)?an?\s+(?:formal\s+|verbal\s+)?offer\b",
        r"\bpleased\s+to\s+offer\s+you\b",
    )),
    (InboundIntent.INFO_REQUEST, _rx(
        r"\bcould\s+you\s+(?:please\s+)?(?:send|provide|share|confirm|clarify)\b",
        r"\bplease\s+(?:send|provide|confirm|clarify)\b",
        r"\b(?:need|require)\s+(?:some\s+)?(?:additional|more|further)\s+information\b",
    )),
    (InboundIntent.AUTO_ACK, _rx(
        r"\bthank(?:s|\s+you)\s+for\s+(?:applying|your\s+application)\b",
        r"\b(?:have\s+)?received\s+your\s+application\b",
        r"\bapplication\s+(?:has\s+been\s+)?received\b",
        r"\bautomated\s+(?:message|response|email|reply)\b",
        r"\b(?:do\s+not|don't)\s+reply\b",
    )),
)

_WEAK_INTENTS = frozenset({InboundIntent.INFO_REQUEST, InboundIntent.AUTO_ACK})
_NEGATED_OFFER = re.compile(r"\bnot\s+(?:an?\s+|a\s+formal\s+)?offer\b", re.IGNORECASE)


@dataclass(frozen=True)
class InboundClassification:
    intent: InboundIntent
    needs_review: bool
    matched_intents: tuple[str, ...] = ()
    reason: str = ""


def _matched_inbound_intents(content: str) -> list[InboundIntent]:
    hits = [intent for intent, patterns in _INBOUND_RULES if any(p.search(content) for p in patterns)]
    if InboundIntent.OFFER in hits and _NEGATED_OFFER.search(content):
        hits.remove(InboundIntent.OFFER)
    return hits


def _resolve_inbound_intent(hits: list[InboundIntent]) -> tuple[InboundIntent, bool, str]:
    if not hits:
        return InboundIntent.UNKNOWN, True, "no rule matched"
    strong = [h for h in hits if h not in _WEAK_INTENTS]
    if len(strong) > 1:
        return InboundIntent.UNKNOWN, True, "ambiguous: conflicting intents " + ", ".join(h.value for h in strong)
    if strong:
        return strong[0], False, f"rule match: {strong[0].value}"
    if len(hits) > 1:
        # info_request takes priority over a templated acknowledgment footer.
        return InboundIntent.INFO_REQUEST, False, "rule match: info_request over auto_ack"
    return hits[0], False, f"rule match: {hits[0].value}"


def classify_inbound_message(subject: str, body: str) -> InboundClassification:
    """Classify an inbound recruiter message offline and deterministically.

    Never calls a model or the network. Ambiguous or unmatched messages come
    back as ``unknown`` with ``needs_review=True`` for a human to triage.
    """
    content = f"{subject or ''}\n{body or ''}"
    hits = _matched_inbound_intents(content)
    intent, needs_review, reason = _resolve_inbound_intent(hits)
    return InboundClassification(
        intent=intent,
        needs_review=needs_review,
        matched_intents=tuple(h.value for h in hits),
        reason=reason,
    )
