"""Fail-closed pre-flight linter for answer-policy readiness.

Ported from JobTomatik's ``answer_policy_readiness`` completeness contract,
adapted to HunterXJob profile, answer vault, and compiled master-policy
shapes. This module is a pure, deterministic gate: it never launches a
browser, never submits an application, and never infers a missing answer.

US targeting is OFF by default (master policy ``enabled_countries: [CA]`` /
``disabled_countries: [US]``). US-only answers are therefore not required
unless the caller explicitly enables US targeting.

Consent answers must be explicitly recorded in the answer vault before
browser launch. Values inferred from verified profile defaults (for
example ``terms_consent`` set by ``profile_answers``) do not count.

Browser-launch callers (``browser_filler``, ``greenhouse_browser``,
submission paths) should invoke ``build_answer_policy_readiness`` — or the
Truth Guard hook ``preflight_answer_policy`` — and refuse to open a page
when ``ready_for_browser_launch`` is false. Those call sites are owned
outside this module; the function is the handoff.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from app.answer_policy_country import (
    _AFFIRMATIVE,
    _EXPLICIT_SOURCES,
    _blocker,
    _country_policy_status,
    _nonempty,
)
from app.answer_policy_country import (
    BLOCKER_MESSAGES as BLOCKER_MESSAGES,
)
from app.answer_policy_country import (
    COUNTRY_READINESS_PROFILES as COUNTRY_READINESS_PROFILES,
)
from app.answer_policy_country import (
    US_ONLY_POLICY_KEYS as US_ONLY_POLICY_KEYS,
)
from app.answer_policy_country import normalize_country_code as normalize_country_code
from app.answer_policy_country import us_targeting_is_enabled as us_targeting_is_enabled
from app.answer_vault import AnswerVault, FieldRequest, ResolutionStatus
from app.profile import VerifiedProfile

PROFILE_FIELD_REQUIREMENTS = [
    ("full_name", "Full legal or preferred application name"),
    ("email", "Applicant email"),
    ("phone", "Applicant phone number"),
    ("location", "Applicant location or mailing address"),
]

PLATFORM_READINESS_PROFILES = {
    "greenhouse": {
        "domains": ["greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"],
        "required": ["background_check_consent", "data_processing_consent", "terms_consent"],
    },
    "lever": {
        "domains": ["lever.co", "jobs.lever.co"],
        "required": ["background_check_consent", "terms_consent"],
    },
    "ashby": {
        "domains": ["ashbyhq.com", "jobs.ashbyhq.com"],
        "required": ["background_check_consent", "data_processing_consent", "terms_consent"],
    },
    "workday": {
        "domains": ["myworkdayjobs.com", "workday.com"],
        "required": ["background_check_consent", "data_processing_consent", "terms_consent"],
    },
    "smartrecruiters": {
        "domains": ["smartrecruiters.com", "jobs.smartrecruiters.com"],
        "required": ["background_check_consent", "terms_consent"],
    },
    "generic": {
        "domains": [],
        "required": ["background_check_consent", "data_processing_consent", "terms_consent"],
    },
}

# profile_answers() setdefaults these; they are not explicit recorded consents.
INFERRED_CONSENT_NOTES = frozenset({"verified profile fact"})

CONSENT_KEYS = frozenset({
    "background_check_consent",
    "data_processing_consent",
    "terms_consent",
    "disclaimer_and_consent",
    "agreement_consent",
    "general_consent",
})

GUARANTEES = [
    "No missing legal, sensitive, consent, or custom answer is inferred.",
    "Unknown, conflicting, or low-provenance answers stop browser launch.",
    "US targeting is off by default, so US-only answers are not required.",
    "Mandatory ATS consent must be explicitly recorded before browser launch.",
    "Country and platform profiles are minimum autonomy coverage, not claims about every employer form.",
]


def detect_platform(target_url: str = "", requested_platform: str = "") -> str:
    requested = str(requested_platform or "").strip().lower()
    if requested in PLATFORM_READINESS_PROFILES:
        return requested
    domain = (urlparse(target_url or "").hostname or "").lower()
    for name, profile in PLATFORM_READINESS_PROFILES.items():
        if name == "generic":
            continue
        if any(domain == item or domain.endswith("." + item) for item in profile["domains"]):
            return name
    return "generic"


def required_policy_keys(
    country_code: str,
    platform: str,
    *,
    us_targeting_enabled: bool = False,
) -> list[str]:
    """Canonical keys that must be ready for this country and platform."""
    output: list[str] = []
    if country_code in COUNTRY_READINESS_PROFILES:
        output.extend(COUNTRY_READINESS_PROFILES[country_code])
    platform_profile = PLATFORM_READINESS_PROFILES.get(platform) or PLATFORM_READINESS_PROFILES["generic"]
    for key in platform_profile["required"]:
        if key not in output:
            output.append(key)
    if not us_targeting_enabled:
        output = [key for key in output if key not in US_ONLY_POLICY_KEYS]
    return output


def build_answer_policy_readiness(
    *,
    profile: VerifiedProfile | None = None,
    vault: AnswerVault | None = None,
    policy: Any | None = None,
    country_code: str = "CA",
    platform: str = "",
    target_url: str = "",
    company: str = "",
    us_targeting_enabled: bool | None = None,
) -> dict[str, Any]:
    """Return a structured fail-closed readiness report.

    ``ready_for_browser_launch`` is true only when every required profile
    field, country policy, and ATS consent check is satisfied and consistent.
    Missing or unknown inputs fail closed.
    """
    country = normalize_country_code(country_code)
    detected_platform = detect_platform(target_url, platform)
    us_enabled = us_targeting_is_enabled(policy, us_targeting_enabled=us_targeting_enabled)
    vault = vault or AnswerVault()

    blockers = _country_gate_blockers(country, country_code, us_enabled)
    profile_statuses, profile_complete, profile_blockers = _profile_statuses(profile, vault)
    blockers.extend(profile_blockers)

    policy_country, required_keys = _required_keys_for_report(country, detected_platform, us_enabled)
    policy_statuses, policy_blockers, conflicts, policies_ready = _collect_required_policies(
        profile, vault, policy_country, required_keys,
    )
    blockers.extend(policy_blockers)

    us_blockers, us_conflicts = _cross_check_us_answers(profile, vault, us_enabled)
    blockers.extend(us_blockers)
    conflicts.extend(us_conflicts)

    return _assemble_readiness_report(
        country=country,
        platform=detected_platform,
        target_url=target_url,
        company=company,
        us_enabled=us_enabled,
        profile_statuses=profile_statuses,
        policy_statuses=policy_statuses,
        required_keys=required_keys,
        blockers=blockers,
        conflicts=conflicts,
        satisfied=profile_complete + policies_ready,
    )


def _country_gate_blockers(country: str, country_code: str, us_enabled: bool) -> list[dict[str, Any]]:
    if country == "UNKNOWN":
        return [_blocker("country_unsupported", field=str(country_code or "").strip() or None)]
    if country == "US" and not us_enabled:
        return [_blocker("us_targeting_disabled", field="US")]
    return []


def _required_keys_for_report(
    country: str,
    platform: str,
    us_enabled: bool,
) -> tuple[str, list[str]]:
    # Unknown country or US-with-targeting-off still require recorded ATS
    # consents, but not country work-authorization / sponsorship answers.
    # The country_unsupported / us_targeting_disabled blockers already fail
    # the report in those cases.
    require_country_policies = country in COUNTRY_READINESS_PROFILES and not (
        country == "US" and not us_enabled
    )
    policy_country = country if require_country_policies else "CA"
    required_keys = required_policy_keys(
        policy_country if require_country_policies else "CA",
        platform,
        us_targeting_enabled=us_enabled and country == "US",
    )
    if not require_country_policies:
        required_keys = list(
            (PLATFORM_READINESS_PROFILES.get(platform) or PLATFORM_READINESS_PROFILES["generic"])["required"]
        )
    return policy_country, required_keys


def _evaluate_required_policy(
    profile: VerifiedProfile | None,
    vault: AnswerVault,
    policy_country: str,
    key: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    if key in CONSENT_KEYS or key.endswith("_consent"):
        status, item_blockers = _consent_status(vault, key)
        return status, item_blockers, None
    if key == "work_authorization":
        return _country_policy_status(profile, vault, policy_country, kind="work_authorization")
    return _country_policy_status(profile, vault, policy_country, kind="sponsorship")


def _collect_required_policies(
    profile: VerifiedProfile | None,
    vault: AnswerVault,
    policy_country: str,
    required_keys: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    policy_statuses: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    satisfied = 0
    for key in required_keys:
        status, item_blockers, conflict = _evaluate_required_policy(profile, vault, policy_country, key)
        if conflict:
            conflicts.append(conflict)
        if status["satisfied"]:
            satisfied += 1
        blockers.extend(item_blockers)
        policy_statuses.append(status)
    return policy_statuses, blockers, conflicts, satisfied


def _cross_check_us_answers(
    profile: VerifiedProfile | None,
    vault: AnswerVault,
    us_enabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Cross-check any present US answers for consistency even when not required.
    blockers: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    if us_enabled:
        return blockers, conflicts
    for kind in ("work_authorization", "sponsorship"):
        _, us_blockers, us_conflict = _country_policy_status(
            profile, vault, "US", kind=kind, required=False,
        )
        if us_conflict:
            conflicts.append(us_conflict)
            blockers.extend(us_blockers)
        elif us_blockers and any(item["code"].endswith("_inconsistent") for item in us_blockers):
            blockers.extend(us_blockers)
    return blockers, conflicts


def _assemble_readiness_report(
    *,
    country: str,
    platform: str,
    target_url: str,
    company: str,
    us_enabled: bool,
    profile_statuses: list[dict[str, Any]],
    policy_statuses: list[dict[str, Any]],
    required_keys: list[str],
    blockers: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    satisfied: int,
) -> dict[str, Any]:
    deduped = _dedupe_blockers(blockers)
    total = len(PROFILE_FIELD_REQUIREMENTS) + len(required_keys)
    ready = not deduped
    return {
        "country_code": country,
        "platform": platform,
        "target_url": target_url or "",
        "company": company or "",
        "us_targeting_enabled": us_enabled,
        "ready_for_browser_launch": ready,
        "ready_for_unattended": ready,
        "completeness_score": round((satisfied / total) * 100, 1) if total else 100.0,
        "required_profile_fields": profile_statuses,
        "required_policies": policy_statuses,
        "blockers": deduped,
        "conflicts": conflicts,
        "summary": {
            "profile_fields_required": len(PROFILE_FIELD_REQUIREMENTS),
            "profile_fields_complete": sum(1 for item in profile_statuses if item["present"]),
            "policies_required": len(required_keys),
            "policies_ready": sum(1 for item in policy_statuses if item["satisfied"]),
            "blocker_count": len(deduped),
            "conflict_count": len(conflicts),
        },
        "guarantees": list(GUARANTEES),
    }


def _dedupe_blockers(blockers: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in blockers:
        identity = (
            item["code"],
            item.get("field"),
            item.get("canonical_key"),
            tuple(item.get("policy_ids") or []),
        )
        if identity not in seen:
            seen.add(identity)
            deduped.append(item)
    return deduped


def _profile_contact(profile: VerifiedProfile | None) -> dict[str, str]:
    if profile is None:
        return {}
    contact = dict(profile.contact)
    full_name = str(profile.full_name or "").strip()
    if not full_name:
        first = (contact.get("preferred_name") or contact.get("first_name") or "").strip()
        last = (contact.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first, last) if part)
    if full_name and (contact.get("last_name") or "").strip() and (
        (contact.get("first_name") or "").strip() or (contact.get("preferred_name") or "").strip()
    ):
        contact["full_name"] = full_name
    elif full_name and not contact.get("full_name"):
        # A single token is not a complete application name.
        contact.pop("full_name", None)
    return contact


def _vault_text(vault: AnswerVault, key: str) -> str | None:
    record = vault.get(key)
    if record is None or not _nonempty(record.value):
        return None
    return str(record.value).strip()


def _profile_statuses(
    profile: VerifiedProfile | None,
    vault: AnswerVault,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    contact = _profile_contact(profile)
    statuses: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    complete = 0
    for key, label in PROFILE_FIELD_REQUIREMENTS:
        value = contact.get(key) if _nonempty(contact.get(key)) else _vault_text(vault, key)
        # Address alias used by JobTomatik-style profiles.
        if key == "location" and not _nonempty(value):
            value = _vault_text(vault, "address")
        present = _nonempty(value)
        statuses.append({
            "key": key,
            "label": label,
            "source": f"profile.contact.{key}",
            "present": present,
            "blocking": not present,
        })
        if present:
            complete += 1
        else:
            blockers.append(_blocker("profile_field_missing", field=key))
    return statuses, complete, blockers


def _is_affirmative_consent(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return False
    if text in _AFFIRMATIVE:
        return True
    return text.startswith("yes") or text.startswith("i agree") or text.startswith("i consent")


def _consent_status(
    vault: AnswerVault,
    key: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request = FieldRequest(key=key, required=True, sensitive=True)
    resolved = vault.resolve(request)
    record = vault.get(key)
    codes: list[str] = []

    if record is None:
        codes = ["policy_consent_missing", "policy_missing"]
    elif record.note.strip().lower() in INFERRED_CONSENT_NOTES:
        codes = ["policy_consent_inferred"]
    elif record.source not in _EXPLICIT_SOURCES:
        codes = ["policy_provenance_unknown"]
    elif record.confidence < 1.0:
        codes = ["policy_confidence_low"]
    elif not _nonempty(record.value):
        codes = ["policy_answer_missing"]
    elif not _is_affirmative_consent(record.value):
        codes = ["policy_consent_not_affirmative"]
    elif resolved.status != ResolutionStatus.RESOLVED:
        if "provenance" in resolved.reason:
            codes = ["policy_provenance_unknown"]
        elif "confidence" in resolved.reason:
            codes = ["policy_confidence_low"]
        else:
            codes = ["policy_consent_missing"]

    status = {
        "canonical_key": key,
        "label": key,
        "satisfied": not codes,
        "selected_policy": key if record is not None else None,
        "blocker_codes": codes,
    }
    item_blockers = [_blocker(code, canonical_key=key, policy_ids=[key] if record is not None else []) for code in codes]
    return status, item_blockers


__all__ = [
    "BLOCKER_MESSAGES",
    "COUNTRY_READINESS_PROFILES",
    "PLATFORM_READINESS_PROFILES",
    "PROFILE_FIELD_REQUIREMENTS",
    "US_ONLY_POLICY_KEYS",
    "build_answer_policy_readiness",
    "detect_platform",
    "normalize_country_code",
    "required_policy_keys",
    "us_targeting_is_enabled",
]
