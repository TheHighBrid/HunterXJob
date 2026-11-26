"""Country and CA/US targeting checks for answer-policy readiness.

US targeting is OFF by default (master policy ``enabled_countries: [CA]`` /
``disabled_countries: [US]``). These helpers are a pure, deterministic
pre-flight over the verified profile, answer vault, and compiled policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.profile import VerifiedProfile

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


def normalize_country_code(value: str | None) -> str:
    """Return CA, US, or UNKNOWN. An omitted value defaults to CA."""
    if value is None or str(value).strip() == "":
        return "CA"
    code = str(value).strip().upper()
    if code in COUNTRY_READINESS_PROFILES:
        return code
    return "UNKNOWN"


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


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


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


def _profile_policy_polarity(fact: dict[str, Any] | None, kind: str) -> bool | None:
    if fact is None:
        return None
    if kind == "work_authorization":
        authorized = fact.get("authorized")
        return authorized if isinstance(authorized, bool) else _polarity(authorized)
    sponsorship = fact.get("requires_sponsorship")
    if sponsorship is None:
        return None
    if isinstance(sponsorship, bool):
        return sponsorship
    return _polarity(sponsorship)


def _vault_policy_state(
    record: AnswerRecord | None,
    canonical: str,
) -> tuple[list[str], bool | None]:
    vault_codes = _record_blockers(record, canonical_key=canonical, required=False)
    vault_polarity = _polarity(record.value) if record is not None and not vault_codes else None
    if record is not None and not vault_codes and vault_polarity is None:
        vault_codes = ["policy_answer_missing"]
    return vault_codes, vault_polarity


def _country_policy_codes(
    *,
    kind: str,
    required: bool,
    profile_polarity: bool | None,
    vault_polarity: bool | None,
    vault_codes: list[str],
    record: AnswerRecord | None,
    target: tuple[str, str, str | None],
) -> tuple[list[str], dict[str, Any] | None]:
    canonical, country, matched_key = target
    if profile_polarity is not None and vault_polarity is not None and profile_polarity != vault_polarity:
        conflict_code = (
            "work_authorization_inconsistent" if kind == "work_authorization" else "sponsorship_inconsistent"
        )
        conflict = {
            "canonical_key": canonical,
            "country": country,
            "policy_ids": [matched_key] if matched_key else [],
        }
        return [conflict_code, "policy_conflict"], conflict
    if required and profile_polarity is None and vault_polarity is None:
        codes = ["sponsorship_unknown", "policy_missing"] if kind == "sponsorship" else ["policy_missing"]
        if record is not None:
            codes = list(dict.fromkeys([*vault_codes, *codes]))
        return codes, None
    # A stored vault answer that cannot be used still blocks when required,
    # even if the profile already has a usable fact — fail closed on conflict
    # between a bad vault record and a good profile fact.
    if record is not None and vault_codes and (
        required or any(code.endswith("_inconsistent") for code in vault_codes)
    ):
        return vault_codes, None
    return [], None


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
    profile_polarity = _profile_policy_polarity(_profile_country_fact(profile, country), kind)
    vault_codes, vault_polarity = _vault_policy_state(record, canonical)
    codes, conflict = _country_policy_codes(
        kind=kind,
        required=required,
        profile_polarity=profile_polarity,
        vault_polarity=vault_polarity,
        vault_codes=vault_codes,
        record=record,
        target=(canonical, country, matched_key),
    )
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
