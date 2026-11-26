"""Validated recruiter CRM records: contacts and their interactions (Wave 3).

Pure in-memory dataclasses and functions. Nothing here touches the database,
the network or any mailbox: persistence is deferred until a schema design is
approved by the owner. ``app.crm`` keeps its draft-oriented helpers; this
module adds validated records and an interaction history on top of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.inbox_matcher import InboundClassification, InboundIntent, classify_inbound_message

RELATIONSHIP_STAGES = frozenset({"identified", "contacted", "engaged", "interviewing", "closed"})
DIRECTIONS = frozenset({"inbound", "outbound"})
CHANNELS = frozenset({"email", "phone", "linkedin", "ats_portal", "video", "other"})
INTENT_VALUES = frozenset(i.value for i in InboundIntent)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class CRMValidationError(ValueError):
    """A recruiter CRM record failed validation."""


def is_valid_email(value: str) -> bool:
    return bool(value) and bool(_EMAIL_RE.match(value))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CRMValidationError(message)


@dataclass
class RecruiterContact:
    contact_id: str
    company: str
    full_name: str
    email: str = ""
    title: str = ""
    linkedin_url: str = ""
    relationship_stage: str = "identified"
    notes: str = ""

    def __post_init__(self) -> None:
        self.contact_id = (self.contact_id or "").strip()
        self.company = (self.company or "").strip()
        self.full_name = (self.full_name or "").strip()
        self.email = (self.email or "").strip().lower()
        self.linkedin_url = (self.linkedin_url or "").strip()
        self.validate()

    def validate(self) -> None:
        _require(bool(self.contact_id), "contact_id is required")
        _require(bool(self.company), "company is required")
        _require(bool(self.full_name), "full_name is required")
        _require(not self.email or is_valid_email(self.email), f"email is invalid: {self.email!r}")
        _require(self.relationship_stage in RELATIONSHIP_STAGES, f"relationship_stage is invalid: {self.relationship_stage!r}")
        _require(not self.linkedin_url or self.linkedin_url.startswith("https://"), "linkedin_url must be https")

    def dedup_key(self) -> tuple[str, str]:
        return (self.company.casefold(), self.email or self.full_name.casefold())


@dataclass
class RecruiterInteraction:
    interaction_id: str
    contact_id: str
    direction: str
    channel: str
    summary: str
    occurred_at: datetime
    application_id: str | None = None
    intent: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.intent, InboundIntent):
            self.intent = self.intent.value
        self.summary = (self.summary or "").strip()
        self.validate()

    def validate(self) -> None:
        _require(bool((self.interaction_id or "").strip()), "interaction_id is required")
        _require(bool((self.contact_id or "").strip()), "contact_id is required")
        _require(self.direction in DIRECTIONS, f"direction is invalid: {self.direction!r}")
        _require(self.channel in CHANNELS, f"channel is invalid: {self.channel!r}")
        _require(bool(self.summary), "summary is required")
        _require(isinstance(self.occurred_at, datetime), "occurred_at must be a datetime")
        _require(self.occurred_at.tzinfo is not None, "occurred_at must carry a timezone")
        _require(self.intent is None or self.intent in INTENT_VALUES, f"intent is invalid: {self.intent!r}")


class RecruiterCRM:
    """In-memory registry of recruiter contacts and their interaction history."""

    def __init__(self) -> None:
        self._contacts: dict[str, RecruiterContact] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._interactions: dict[str, RecruiterInteraction] = {}

    def add_contact(self, contact: RecruiterContact) -> RecruiterContact:
        """Add a contact, returning the existing record for a duplicate person."""
        existing_id = self._by_key.get(contact.dedup_key())
        if existing_id is not None:
            return self._contacts[existing_id]
        _require(contact.contact_id not in self._contacts, f"contact_id already used: {contact.contact_id}")
        self._contacts[contact.contact_id] = contact
        self._by_key[contact.dedup_key()] = contact.contact_id
        return contact

    def get_contact(self, contact_id: str) -> RecruiterContact | None:
        return self._contacts.get(contact_id)

    def contacts(self) -> list[RecruiterContact]:
        return list(self._contacts.values())

    def record_interaction(self, interaction: RecruiterInteraction) -> RecruiterInteraction:
        _require(interaction.contact_id in self._contacts, f"unknown contact: {interaction.contact_id}")
        _require(interaction.interaction_id not in self._interactions,
                 f"interaction_id already recorded: {interaction.interaction_id}")
        self._interactions[interaction.interaction_id] = interaction
        return interaction

    def interactions_for(self, contact_id: str) -> list[RecruiterInteraction]:
        rows = [i for i in self._interactions.values() if i.contact_id == contact_id]
        return sorted(rows, key=lambda i: (i.occurred_at, i.interaction_id))

    def last_contacted_at(self, contact_id: str) -> datetime | None:
        history = self.interactions_for(contact_id)
        return history[-1].occurred_at if history else None

    def record_inbound_message(
        self,
        *,
        interaction_id: str,
        contact_id: str,
        subject: str,
        body: str,
        occurred_at: datetime,
        application_id: str | None = None,
    ) -> tuple[RecruiterInteraction, InboundClassification]:
        """Classify an inbound message offline and record it. Never replies."""
        classification = classify_inbound_message(subject, body)
        interaction = RecruiterInteraction(
            interaction_id=interaction_id,
            contact_id=contact_id,
            direction="inbound",
            channel="email",
            summary=(subject or "").strip() or "(no subject)",
            occurred_at=occurred_at,
            application_id=application_id,
            intent=classification.intent.value,
            metadata={"needs_review": str(classification.needs_review).lower(), "reason": classification.reason},
        )
        return self.record_interaction(interaction), classification
