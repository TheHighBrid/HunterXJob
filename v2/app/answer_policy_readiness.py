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

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

from app.answer_vault import (
    AnswerRecord,
    AnswerSource,
    AnswerVault,
    FieldRequest,
    ResolutionStatus,
)
from app.profile import VerifiedProfile

PROFILE_FIELD_REQUIREMENTS = [
    ("full_name", "Full legal or preferred application name"),
    ("email", "Applicant email"),
    ("phone", "Applicant phone number"),
    ("location", "Applicant location or mailing address"),
]

# Country policy keys required when that country is an active target.
COUNTRY_READINESS_PROFILES = {
    "CA": ["work_authorization", "sponsorship_required"],
    "US": ["work_authorization", "sponsorship_required"],
}

# US-only vault / policy keys that must not be required while US targeting is off.
US_ONLY_POLICY_KEYS = frozenset({
    "us_work_authorization",
    "work_authorization_US",
    "work_authorization_us",
    "sponsorship_required_US",
    "sponsorship_US",
    "sponsorship_us",
    "us_citizen",
    "immigration_status_US",
})

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

_AFFIRMATIVE = frozenset({
    "yes", "true", "1", "y", "authorized", "eligible", "i agree", "i consent",
    "agreed", "consent",
})
_NEGATIVE = frozenset({
    "no", "false", "0", "n", "not authorized", "unauthorized", "ineligible",
})

_EXPLICIT_SOURCES = frozenset({AnswerSource.USER, AnswerSource.POLICY})

BLOCKER_MESSAGES = {
    "profile_field_missing": "A required applicant profile field is missing.",
    "policy_missing": "No matching answer policy exists.",
    "policy_conflict": "Conflicting answers exist for the same required policy.",
    "policy_answer_missing": "The policy has no usable answer value.",
    "policy_provenance_unknown": "The answer provenance is unknown or is not user/policy.",
    "policy_confidence_low": "The answer confidence is below the automatic-use threshold.",
    "policy_consent_missing": "Mandatory ATS consent has not been recorded.",
    "policy_consent_inferred": "Consent inferred from profile defaults is not an explicit record.",
    "policy_consent_not_affirmative": "Recorded consent is missing or is not an affirmative agreement.",
    "us_targeting_disabled": "US targeting is off; US applications are not ready for browser launch.",
    "country_unsupported": "The requested country is unknown or is not an enabled target.",
    "work_authorization_inconsistent": "Work-authorization answers conflict with the verified profile.",
    "sponsorship_inconsistent": "Sponsorship answers conflict with the verified profile.",
    "sponsorship_unknown": "Sponsorship requirement is unknown and cannot be inferred.",
}

GUARANTEES = [
    "No missing legal, sensitive, consent, or custom answer is inferred.",
    "Unknown, conflicting, or low-provenance answers stop browser launch.",
    "US targeting is off by default, so US-only answers are not required.",
    "Mandatory ATS consent must be explicitly recorded before browser launch.",
    "Country and platform profiles are minimum autonomy coverage, not claims about every employer form.",
]


def normalize_country_code(value: str | None) -> str:
    """Return CA, US, or UNKNOWN. An omitted value defaults to CA."""
    if value is None or str(value).strip() == "":
        return "CA"
    code = str(value).strip().upper()
    if code in COUNTRY_READINESS_PROFILES:
        return code
    return "UNKNOWN"


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


def us_targeting_is_enabled(
    policy: Any | None = None,
    *,
    us_targeting_enabled: bool | None = None,
) -> bool:
    """US targeting is OFF unless an explicit opt-in or enabled-country list says otherwise."""
    if us_targeting_enabled is not None:
        return bool(us_targeting_enabled)
    targeting = _targeting_map(policy)
    if not targeting:
        return False
    enabled = {_country_token(item) for item in targeting.get("enabled_countries") or []}
    disabled = {_country_token(item) for item in targeting.get("disabled_countries") or []}
    if "US" in disabled:
        return False
    if targeting.get("us_authorized_but_search_disabled"):
        return False
    if enabled:
        return "US" in enabled
    return False


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

    blockers: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    satisfied = 0

    if country == "UNKNOWN":
        blockers.append(_blocker("country_unsupported", field=str(country_code or "").strip() or None))
    elif country == "US" and not us_enabled:
        blockers.append(_blocker("us_targeting_disabled", field="US"))

    profile_statuses, profile_complete, profile_blockers = _profile_statuses(profile, vault)
    blockers.extend(profile_blockers)
    satisfied += profile_complete

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
        detected_platform,
        us_targeting_enabled=us_enabled and country == "US",
    )
    if not require_country_policies:
        required_keys = list(
            (PLATFORM_READINESS_PROFILES.get(detected_platform) or PLATFORM_READINESS_PROFILES["generic"])["required"]
        )

    policy_statuses: list[dict[str, Any]] = []
    for key in required_keys:
        if key in CONSENT_KEYS or key.endswith("_consent"):
            status, item_blockers = _consent_status(vault, key)
            conflict = None
        elif key == "work_authorization":
            status, item_blockers, conflict = _country_policy_status(
                profile, vault, policy_country, kind="work_authorization",
            )
        else:
            status, item_blockers, conflict = _country_policy_status(
                profile, vault, policy_country, kind="sponsorship",
            )
        if conflict:
            conflicts.append(conflict)
        if status["satisfied"]:
            satisfied += 1
        blockers.extend(item_blockers)
        policy_statuses.append(status)

    # Cross-check any present US answers for consistency even when not required.
    if not us_enabled:
        for kind in ("work_authorization", "sponsorship"):
            _, us_blockers, us_conflict = _country_policy_status(
                profile, vault, "US", kind=kind, required=False,
            )
            if us_conflict:
                conflicts.append(us_conflict)
                blockers.extend(us_blockers)
            elif us_blockers and any(item["code"].endswith("_inconsistent") for item in us_blockers):
                blockers.extend(us_blockers)

    deduped = _dedupe_blockers(blockers)
    total = len(PROFILE_FIELD_REQUIREMENTS) + len(required_keys)
    ready = not deduped
    return {
        "country_code": country,
        "platform": detected_platform,
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


def _targeting_map(policy: Any | None) -> dict[str, Any]:
    if policy is None:
        return {}
    targeting = getattr(policy, "targeting", None)
    if targeting is None and isinstance(policy, Mapping):
        targeting = policy.get("targeting")
    return dict(targeting or {})


def _country_token(value: Any) -> str:
    return str(value or "").strip().upper()


def _blocker(
    code: str,
    *,
    field: str | None = None,
    canonical_key: str | None = None,
    policy_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": BLOCKER_MESSAGES[code],
        "field": field,
        "canonical_key": canonical_key,
        "policy_ids": policy_ids or [],
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


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


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


def _polarity(value: Any) -> bool | None:
    """Return True/False for a yes/no answer, or None when the value is unknown."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _AFFIRMATIVE:
        return True
    if text in _NEGATIVE:
        return False
    if text.startswith("yes"):
        return True
    if text.startswith("no"):
        return False
    return None


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


def _record_blockers(record: AnswerRecord | None, *, canonical_key: str, required: bool) -> list[str]:
    if record is None:
        return ["policy_missing"] if required else []
    if not _nonempty(record.value):
        return ["policy_answer_missing"]
    if record.source not in _EXPLICIT_SOURCES:
        return ["policy_provenance_unknown"]
    if record.confidence < 1.0:
        return ["policy_confidence_low"]
    return []


def _vault_record(vault: AnswerVault, keys: Iterable[str]) -> tuple[str | None, AnswerRecord | None]:
    for key in keys:
        record = vault.get(key)
        if record is not None:
            return key, record
    return None, None


def _work_auth_keys(country: str) -> list[str]:
    country = country.upper()
    keys = [f"work_authorization_{country}", f"work_authorization_{country.lower()}"]
    if country == "CA":
        keys.extend(["canadian_work_authorization", "work_authorization_ca", "work_authorization"])
    elif country == "US":
        keys.extend(["us_work_authorization"])
    return keys


def _sponsorship_keys(country: str) -> list[str]:
    country = country.upper()
    keys = [
        f"sponsorship_required_{country}",
        f"sponsorship_{country}",
        f"sponsorship_{country.lower()}",
    ]
    if country == "CA":
        keys.extend(["sponsorship_required", "sponsorship"])
    return keys


def _profile_country_fact(profile: VerifiedProfile | None, country: str) -> dict[str, Any] | None:
    if profile is None:
        return None
    for fact in profile.of("work_authorization"):
        if _country_token(fact.data.get("country")) == country.upper():
            return dict(fact.data)
    return None


def _country_policy_status(
    profile: VerifiedProfile | None,
    vault: AnswerVault,
    country: str,
    *,
    kind: str,
    required: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    canonical = "work_authorization" if kind == "work_authorization" else "sponsorship_required"
    keys = _work_auth_keys(country) if kind == "work_authorization" else _sponsorship_keys(country)
    matched_key, record = _vault_record(vault, keys)
    fact = _profile_country_fact(profile, country)

    profile_polarity: bool | None = None
    if fact is not None:
        if kind == "work_authorization":
            authorized = fact.get("authorized")
            profile_polarity = authorized if isinstance(authorized, bool) else _polarity(authorized)
        else:
            sponsorship = fact.get("requires_sponsorship")
            if sponsorship is None:
                profile_polarity = None
            elif isinstance(sponsorship, bool):
                profile_polarity = sponsorship
            else:
                profile_polarity = _polarity(sponsorship)

    vault_codes = _record_blockers(record, canonical_key=canonical, required=False)
    vault_polarity = _polarity(record.value) if record is not None and not vault_codes else None
    if record is not None and not vault_codes and vault_polarity is None:
        vault_codes = ["policy_answer_missing"]

    conflict: dict[str, Any] | None = None
    codes: list[str] = []
    if profile_polarity is not None and vault_polarity is not None and profile_polarity != vault_polarity:
        conflict_code = (
            "work_authorization_inconsistent" if kind == "work_authorization" else "sponsorship_inconsistent"
        )
        codes = [conflict_code, "policy_conflict"]
        conflict = {
            "canonical_key": canonical,
            "country": country,
            "policy_ids": [matched_key] if matched_key else [],
        }
    elif required and profile_polarity is None and vault_polarity is None:
        codes = ["sponsorship_unknown", "policy_missing"] if kind == "sponsorship" else ["policy_missing"]
        if record is not None:
            codes = list(dict.fromkeys([*vault_codes, *codes]))
    elif record is not None and vault_codes:
        # A stored vault answer that cannot be used still blocks when required,
        # even if the profile already has a usable fact — fail closed on conflict
        # between a bad vault record and a good profile fact.
        if required or any(code.endswith("_inconsistent") for code in vault_codes):
            codes = vault_codes

    satisfied = required and not codes and (profile_polarity is not None or vault_polarity is not None)
    if not required:
        satisfied = False

    status = {
        "canonical_key": canonical,
        "country": country,
        "label": canonical,
        "satisfied": satisfied,
        "selected_policy": matched_key,
        "blocker_codes": codes,
    }
    item_blockers = [
        _blocker(code, canonical_key=canonical, policy_ids=[matched_key] if matched_key else [])
        for code in codes
    ]
    return status, item_blockers, conflict


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
