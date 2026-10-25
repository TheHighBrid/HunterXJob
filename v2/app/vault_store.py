from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.models import AnswerPolicy
from app.profile import verified_profile
from app.profile_vault import profile_records

# Stored policy keys that also populate classifier vault keys when those keys
# were not set more specifically.
_POLICY_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    # Start date & notice period
    "start_date": ("availability_date", "available_start_date", "earliest_start", "earliest_start_date", "notice_period", "when_can_you_start"),
    "availability_date": ("start_date", "available_start_date", "earliest_start", "notice_period"),
    "available_start_date": ("start_date", "availability_date", "notice_period"),
    "earliest_start": ("start_date", "availability_date", "notice_period"),
    "notice_period": ("start_date", "availability_date", "available_start_date"),

    # Work authorization & residency
    "work_authorization": ("work_authorization_ca", "authorized_to_work", "work_auth", "canada_work_eligibility", "eligible_to_work"),
    "work_authorization_ca": ("work_authorization", "authorized_to_work", "canada_work_eligibility", "eligible_to_work"),
    "authorized_to_work": ("work_authorization", "work_authorization_ca"),
    "canada_work_eligibility": ("work_authorization_ca", "work_authorization"),
    "citizenship_ca": ("citizenship", "citizenship_residency", "country_of_citizenship"),
    "citizenship": ("citizenship_ca", "citizenship_residency"),

    # Sponsorship
    "sponsorship_required": ("sponsorship", "sponsorship_ca", "visa_sponsorship", "require_sponsorship", "sponsorship_future_required"),
    "sponsorship": ("sponsorship_required", "sponsorship_ca", "visa_sponsorship", "require_sponsorship"),
    "sponsorship_ca": ("sponsorship_required", "sponsorship", "visa_sponsorship"),
    "visa_sponsorship": ("sponsorship_required", "sponsorship"),

    # Salary & Compensation
    "salary_expectation": ("desired_salary", "expected_salary", "salary_range", "compensation_expectation", "desired_compensation", "base_salary_expectation"),
    "desired_salary": ("salary_expectation", "expected_salary", "salary_range"),
    "expected_salary": ("salary_expectation", "desired_salary", "salary_range"),
    "salary_range": ("salary_expectation", "desired_salary"),

    # Relocation & Commute
    "willing_to_relocate": ("relocation", "open_to_relocation", "relocate"),
    "relocation": ("willing_to_relocate", "open_to_relocation"),
    "commute_ability": ("ottawa_commute", "hybrid_availability"),
    "ottawa_commute": ("commute_ability",),

    # Language proficiency
    "language_french": ("french_proficiency", "bilingual_french", "language_proficiency"),
    "language_proficiency": ("language_french", "french_proficiency", "bilingual"),
    "french_proficiency": ("language_french", "language_proficiency"),

    # Remote workspace
    "remote_workspace": ("home_office", "dedicated_workspace", "remote_setup", "high_speed_internet"),
    "home_office": ("remote_workspace",),

    # Consents & Screenings
    "background_check_consent": ("background_check", "criminal_check_consent", "screening_consent"),
    "criminal_check_consent": ("background_check_consent", "background_check"),
    "interview_recording_consent": ("recording_consent",),
    "recording_consent": ("interview_recording_consent",),
    "terms_consent": ("disclaimer_and_consent", "terms_agreement", "declaration_consent"),
    "disclaimer_and_consent": ("terms_consent", "terms_agreement"),

    # Referral & Source
    "referral_source": ("how_heard", "source", "source_channel"),
    "how_heard": ("referral_source", "source"),

    # Social links
    "linkedin_url": ("linkedin", "social_linkedin", "linkedin_profile"),
    "linkedin": ("linkedin_url", "social_linkedin"),
    "github_url": ("github", "social_github"),
    "github": ("github_url",),
    "website_url": ("portfolio", "website", "portfolio_url"),
    "portfolio": ("website_url", "portfolio_url"),

    # Demographics
    "gender_identity": ("gender", "gender_select"),
    "gender": ("gender_identity",),
    "race_ethnicity": ("race", "ethnicity", "race_select"),
    "race": ("race_ethnicity",),
    "veteran_status": ("veteran", "military_status"),
    "veteran": ("veteran_status",),
    "disability_status": ("disability",),
    "disability": ("disability_status",),

    # Time zone
    "time_zone": ("timezone",),
    "timezone": ("time_zone",),
}


def load_vault(db: Session, scope: str | Iterable[str] = "global", *, profile: bool = True) -> AnswerVault:
    """Load answers for ``global`` plus the given scope(s).

    Verified profile facts (contact, current role, work authorization by
    country, employment/education history, bilingual language proficiency)
    come first; stored answers then override them. Scopes are applied from
    least to most specific, so an employer- or job-scoped answer overrides a
    global answer with the same key.

    Policy aliases (e.g. ``availability_date`` → ``start_date``) fill classifier
    keys only when that key is still unset after the primary records load.
    Sensitive consents such as ``background_check_consent`` are never inferred
    from the profile; they must be stored explicitly as user or policy answers.
    """
    scopes = [scope] if isinstance(scope, str) else list(scope)
    ordered = ["global", *[item for item in scopes if item != "global"]]
    rows = list(db.execute(select(AnswerPolicy).where(AnswerPolicy.scope.in_(ordered))).scalars())
    rank = {name: index for index, name in enumerate(ordered)}
    rows.sort(key=lambda row: rank.get(row.scope, 0))
    records: list[AnswerRecord] = []
    if profile:
        records.extend(profile_records(verified_profile(db)))
    aliases: list[AnswerRecord] = []
    for row in rows:
        try:
            source = AnswerSource(row.source)
        except ValueError:
            source = AnswerSource.IMPORTED
        record = AnswerRecord(key=row.key, value=row.value, source=source, confidence=row.confidence, note=row.note)
        records.append(record)
        for alias in _POLICY_KEY_ALIASES.get(row.key, ()):
            aliases.append(AnswerRecord(
                key=alias, value=row.value, source=source, confidence=row.confidence,
                note=row.note or f"fallback from {row.key}",
            ))
    # Primary keys (profile + explicit policy) win; aliases fill gaps only.
    vault = AnswerVault(records)
    for alias in aliases:
        if not vault.has(alias.key):
            vault.put(alias)
    return vault


def upsert_answer(
    db: Session,
    *,
    key: str,
    value: str,
    source: str = "user",
    scope: str = "global",
    confidence: float = 1.0,
    sensitive: bool = False,
    note: str = "",
) -> AnswerPolicy:
    row = db.execute(
        select(AnswerPolicy).where(AnswerPolicy.key == key, AnswerPolicy.scope == scope)
    ).scalar_one_or_none()
    if row is None:
        row = AnswerPolicy(key=key, value=value, source=source, scope=scope, confidence=confidence, sensitive=sensitive, note=note)
        db.add(row)
    else:
        row.value = value
        row.source = source
        row.confidence = confidence
        row.sensitive = sensitive
        row.note = note
    db.commit()
    db.refresh(row)
    return row
