from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AnswerSource(str, Enum):
    USER = "user"
    POLICY = "policy"
    RESUME = "resume"
    IMPORTED = "imported"
    GENERATED = "generated"


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    REVIEW = "review"
    MISSING = "missing"


SENSITIVE_KEYS = frozenset({
    "work_authorization",
    "sponsorship",
    "sponsorship_required",
    "sponsorship_future_required",
    "citizenship",
    "citizenship_residency",
    "security_clearance",
    "salary_expectation",
    "hourly_rate_expectation",
    "criminal_record",
    "criminal_history",
    "drug_screening_consent",
    "credit_check_consent",
    "driving_record_check_consent",
    "termination_history",
    "rehire_eligibility",
    "disability",
    "disability_status",
    "veteran_status",
    "gender",
    "gender_identity",
    "race_ethnicity",
    "hispanic_ethnicity",
    "hispanic_latino",
    "pronouns",
    "sexual_orientation",
    "voluntary_self_identification",
})

_KEY_ALIASES = {
    # Work authorization
    "workauthorization": "work_authorization",
    "work-authorization": "work_authorization",
    "work_auth": "work_authorization",
    "authorized_to_work": "work_authorization",
    "eligible_to_work": "work_authorization",
    "right_to_work": "work_authorization",
    "canada_work_eligibility": "work_authorization",

    # Sponsorship
    "sponsorshiprequired": "sponsorship",
    "sponsorship-required": "sponsorship",
    "sponsorship_required": "sponsorship",
    "sponsorship_future_required": "sponsorship",
    "visa_sponsorship": "sponsorship",
    "require_sponsorship": "sponsorship",
    "need_sponsorship": "sponsorship",

    # Citizenship & Residency
    "citizenshipstatus": "citizenship",
    "citizenship_residency": "citizenship",
    "country_of_citizenship": "citizenship",

    # Security Clearance
    "securityclearance": "security_clearance",
    "clearance_level": "security_clearance",

    # Salary & Compensation
    "desired_salary": "salary_expectation",
    "desired-salary": "salary_expectation",
    "desiredsalary": "salary_expectation",
    "expected_salary": "salary_expectation",
    "salary_range": "salary_expectation",
    "salary": "salary_expectation",
    "compensation_expectation": "salary_expectation",
    "desired_compensation": "salary_expectation",
    "base_salary_expectation": "salary_expectation",
    "hourly_rate": "hourly_rate_expectation",
    "hourly_rate_expectation": "hourly_rate_expectation",

    # Relocation & Commute
    "relocation": "willing_to_relocate",
    "relocate": "willing_to_relocate",
    "open_to_relocation": "willing_to_relocate",
    "commute_ability": "commute_ability",
    "commute": "commute_ability",
    "ottawa_commute": "commute_ability",

    # Availability & Notice period
    "availability_date": "start_date",
    "available_start_date": "start_date",
    "earliest_start": "start_date",
    "earliest_start_date": "start_date",
    "notice_period": "start_date",
    "when_can_you_start": "start_date",

    # Background checks & Consents
    "criminalrecord": "criminal_record",
    "criminal_history": "criminal_record",
    "background_check": "background_check_consent",
    "criminal_check_consent": "background_check_consent",
    "screening_consent": "background_check_consent",
    "recording_consent": "interview_recording_consent",

    # Remote Work
    "home_office": "remote_workspace",
    "dedicated_workspace": "remote_workspace",
    "remote_work_environment": "remote_workspace",

    # Languages
    "bilingual": "language_french",
    "french_proficiency": "language_french",
    "bilingual_french": "language_french",
    "language_proficiency": "language_french",

    # Legal declarations
    "disclaimer_and_consent": "terms_consent",
    "terms_agreement": "terms_consent",

    # Social & Web Links
    "linkedin": "linkedin_url",
    "social_linkedin": "linkedin_url",
    "linkedin_profile": "linkedin_url",
    "linkedin_url": "linkedin_url",
    "github": "github_url",
    "social_github": "github_url",
    "github_profile": "github_url",
    "github_url": "github_url",
    "portfolio": "website_url",
    "website": "website_url",
    "portfolio_url": "website_url",
    "personal_website": "website_url",

    # Demographics
    "veteranstatus": "veteran_status",
    "veteran-status": "veteran_status",
    "veteran": "veteran_status",
    "raceethnicity": "race_ethnicity",
    "race-ethnicity": "race_ethnicity",
    "race": "race_ethnicity",
    "ethnicity": "race_ethnicity",
    "disability_status": "disability",
    "disabilitystatus": "disability",
    "gender_identity": "gender",
    "hispanic_latino": "hispanic_ethnicity",
    "hispanic_ethnicity": "hispanic_ethnicity",

    # Time zone
    "timezone": "time_zone",
}


def _canonical_key(key: str) -> str:
    normalized = key.strip().lower().replace(" ", "_")
    return _KEY_ALIASES.get(normalized, normalized)


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    key: str
    value: Any
    source: AnswerSource
    confidence: float = 1.0
    note: str = ""


@dataclass(frozen=True, slots=True)
class FieldRequest:
    key: str
    required: bool = True
    sensitive: bool | None = None
    min_length: int = 0
    max_length: int | None = None


@dataclass(frozen=True, slots=True)
class ResolvedAnswer:
    key: str
    status: ResolutionStatus
    value: Any = None
    source: AnswerSource | None = None
    reason: str = ""


class AnswerVault:
    """Resolve application answers while preserving provenance.

    Sensitive facts are never inferred from resume text or generated by an LLM.
    This makes the automation fail closed instead of inventing consequential
    answers to get past an application form.
    """

    def __init__(self, records: Iterable[AnswerRecord] = ()) -> None:
        self._records: dict[str, AnswerRecord] = {}
        for record in records:
            self.put(record)

    def put(self, record: AnswerRecord) -> None:
        if not 0.0 <= record.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self._records[_canonical_key(record.key)] = record

    def has(self, key: str) -> bool:
        return _canonical_key(key) in self._records

    def resolve(self, request: FieldRequest) -> ResolvedAnswer:
        key = _canonical_key(request.key)
        record = self._records.get(key)
        is_sensitive = request.sensitive if request.sensitive is not None else key in SENSITIVE_KEYS
        if record is None:
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.MISSING if request.required else ResolutionStatus.REVIEW,
                reason="no answer stored",
            )

        if record.value is None or (isinstance(record.value, str) and not record.value.strip()):
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.MISSING if request.required else ResolutionStatus.REVIEW,
                source=record.source,
                reason="required answer is empty" if request.required else "stored answer is empty",
            )

        if is_sensitive and record.source not in {AnswerSource.USER, AnswerSource.POLICY}:
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.REVIEW,
                source=record.source,
                reason="sensitive answer requires explicit user or policy provenance",
            )

        minimum_confidence = 1.0 if is_sensitive else 0.8
        if record.confidence < minimum_confidence:
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.REVIEW,
                source=record.source,
                reason=f"confidence {record.confidence:.2f} is below {minimum_confidence:.2f}",
            )

        if record.source is AnswerSource.GENERATED and request.required:
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.REVIEW,
                source=record.source,
                reason="required fields cannot rely on generated facts",
            )

        if request.min_length > 0 and isinstance(record.value, str) and len(record.value.strip()) < request.min_length:
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.REVIEW,
                source=record.source,
                reason=f"answer length {len(record.value.strip())} is below minimum requirement {request.min_length}",
            )

        if request.max_length is not None and isinstance(record.value, str) and len(record.value.strip()) > request.max_length:
            return ResolvedAnswer(
                key=key,
                status=ResolutionStatus.REVIEW,
                source=record.source,
                reason=f"answer length {len(record.value.strip())} exceeds maximum limit {request.max_length}",
            )

        return ResolvedAnswer(
            key=key,
            status=ResolutionStatus.RESOLVED,
            value=record.value,
            source=record.source,
            reason="answer accepted",
        )

    def resolve_many(self, requests: Iterable[FieldRequest]) -> dict[str, ResolvedAnswer]:
        return {_canonical_key(request.key): self.resolve(request) for request in requests}
