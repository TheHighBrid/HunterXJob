"""Fail-closed tests for the Wave 2 answer-policy pre-flight readiness linter."""

from __future__ import annotations

from app.answer_policy_compiler import load_master_policy
from app.answer_policy_readiness import (
    build_answer_policy_readiness,
    detect_platform,
    normalize_country_code,
    required_policy_keys,
    us_targeting_is_enabled,
)
from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.profile import Fact, VerifiedProfile
from app.profile_vault import profile_records
from app.truth_guard import preflight_answer_policy


def _contact_facts(
    *,
    first_name: str = "Avery",
    last_name: str = "Quinn",
    email: str = "avery.quinn@example.com",
    phone: str = "+1 613 555 0142",
    location: str = "Ottawa, ON",
    preferred_name: str | None = None,
) -> list[Fact]:
    facts = [
        Fact("contact:first_name", "contact", {"field": "first_name", "value": first_name}),
        Fact("contact:last_name", "contact", {"field": "last_name", "value": last_name}),
        Fact("contact:email", "contact", {"field": "email", "value": email}),
        Fact("contact:phone", "contact", {"field": "phone", "value": phone}),
        Fact("contact:location", "contact", {"field": "location", "value": location}),
    ]
    if preferred_name:
        facts.append(Fact("contact:preferred_name", "contact", {"field": "preferred_name", "value": preferred_name}))
    return facts


def _work_auth_fact(
    country: str,
    *,
    authorized: bool | None = True,
    requires_sponsorship: bool | None = False,
) -> Fact:
    data: dict = {"country": country}
    if authorized is not None:
        data["authorized"] = authorized
    if requires_sponsorship is not None:
        data["requires_sponsorship"] = requires_sponsorship
    return Fact(f"work_authorization:{country}", "work_authorization", data)


def _complete_profile(**overrides: object) -> VerifiedProfile:
    facts = _contact_facts()
    facts.append(_work_auth_fact("CA", authorized=True, requires_sponsorship=False))
    extra = overrides.get("extra_facts")
    if isinstance(extra, list):
        facts.extend(extra)
    return VerifiedProfile(facts)


def _explicit(key: str, value: str, *, source: AnswerSource = AnswerSource.USER, confidence: float = 1.0, note: str = "owner confirmed") -> AnswerRecord:
    return AnswerRecord(key=key, value=value, source=source, confidence=confidence, note=note)


def _consent_vault(*extra: AnswerRecord, platform: str = "generic") -> AnswerVault:
    records = [
        _explicit("background_check_consent", "Yes"),
        _explicit("data_processing_consent", "I agree"),
        _explicit("terms_consent", "I consent"),
        *extra,
    ]
    return AnswerVault(records)


def _codes(report: dict) -> set[str]:
    return {item["code"] for item in report["blockers"]}


def test_empty_inputs_fail_closed_for_every_profile_field():
    report = build_answer_policy_readiness()
    assert report["ready_for_browser_launch"] is False
    assert report["ready_for_unattended"] is False
    missing = {item["field"] for item in report["blockers"] if item["code"] == "profile_field_missing"}
    assert missing == {"full_name", "email", "phone", "location"}
    assert "policy_missing" in _codes(report) or "policy_consent_missing" in _codes(report)


def test_each_missing_profile_field_fails_closed():
    complete = {
        "first_name": "Avery",
        "last_name": "Quinn",
        "email": "avery.quinn@example.com",
        "phone": "+1 613 555 0142",
        "location": "Ottawa, ON",
    }
    expected = {
        "first_name": "full_name",
        "last_name": "full_name",
        "email": "email",
        "phone": "phone",
        "location": "location",
    }
    for omitted, field in expected.items():
        kwargs = dict(complete)
        kwargs[omitted] = ""
        profile = VerifiedProfile([*_contact_facts(**kwargs), _work_auth_fact("CA")])
        report = build_answer_policy_readiness(profile=profile, vault=_consent_vault())
        assert report["ready_for_browser_launch"] is False, omitted
        assert field in {item["field"] for item in report["blockers"] if item["code"] == "profile_field_missing"}


def test_blank_profile_fields_fail_closed():
    profile = VerifiedProfile(_contact_facts(first_name="   ", last_name="   ", email="  ", phone="", location=""))
    report = build_answer_policy_readiness(profile=profile)
    missing = {item["field"] for item in report["blockers"] if item["code"] == "profile_field_missing"}
    assert missing == {"full_name", "email", "phone", "location"}


def test_missing_canadian_work_authorization_fails_closed():
    profile = VerifiedProfile(_contact_facts())
    report = build_answer_policy_readiness(profile=profile, vault=_consent_vault())
    assert report["ready_for_browser_launch"] is False
    work = next(item for item in report["required_policies"] if item["canonical_key"] == "work_authorization")
    assert work["satisfied"] is False
    assert "policy_missing" in work["blocker_codes"]


def test_unknown_sponsorship_fails_closed():
    profile = VerifiedProfile([*_contact_facts(), _work_auth_fact("CA", authorized=True, requires_sponsorship=None)])
    report = build_answer_policy_readiness(profile=profile, vault=_consent_vault())
    assert report["ready_for_browser_launch"] is False
    spons = next(item for item in report["required_policies"] if item["canonical_key"] == "sponsorship_required")
    assert spons["satisfied"] is False
    assert "sponsorship_unknown" in spons["blocker_codes"]
    assert "sponsorship_unknown" in _codes(report)


def test_work_authorization_conflict_with_vault_fails_closed():
    profile = _complete_profile()
    vault = _consent_vault(_explicit("work_authorization", "No"))
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    assert "work_authorization_inconsistent" in _codes(report)
    assert "policy_conflict" in _codes(report)
    assert report["conflicts"][0]["canonical_key"] == "work_authorization"


def test_sponsorship_conflict_with_vault_fails_closed():
    profile = _complete_profile()
    vault = _consent_vault(_explicit("sponsorship_required", "Yes"))
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    assert "sponsorship_inconsistent" in _codes(report)


def test_us_only_answers_are_not_required_when_targeting_is_off():
    profile = _complete_profile()
    report = build_answer_policy_readiness(profile=profile, vault=_consent_vault(), country_code="CA")
    required = {item["canonical_key"] for item in report["required_policies"]}
    assert "us_work_authorization" not in required
    assert "sponsorship_required_US" not in required
    assert report["us_targeting_enabled"] is False
    assert report["ready_for_browser_launch"] is True


def test_master_policy_keeps_us_targeting_off_by_default():
    policy = load_master_policy()
    assert us_targeting_is_enabled(policy) is False
    profile = _complete_profile()
    report = build_answer_policy_readiness(profile=profile, vault=_consent_vault(), policy=policy)
    assert report["us_targeting_enabled"] is False
    assert report["ready_for_browser_launch"] is True


def test_us_country_fails_closed_while_targeting_is_off():
    profile = _complete_profile(extra_facts=[_work_auth_fact("US", authorized=True, requires_sponsorship=False)])
    vault = _consent_vault(_explicit("us_work_authorization", "Yes"), _explicit("sponsorship_required_US", "No"))
    report = build_answer_policy_readiness(profile=profile, vault=vault, country_code="US")
    assert report["ready_for_browser_launch"] is False
    assert "us_targeting_disabled" in _codes(report)
    required = {item["canonical_key"] for item in report["required_policies"]}
    assert "work_authorization" not in required
    assert "sponsorship_required" not in required


def test_us_targeting_opt_in_requires_us_authorization_and_sponsorship():
    profile = VerifiedProfile(_contact_facts())
    report = build_answer_policy_readiness(
        profile=profile,
        vault=_consent_vault(),
        country_code="US",
        us_targeting_enabled=True,
    )
    assert report["us_targeting_enabled"] is True
    assert report["ready_for_browser_launch"] is False
    required = {item["canonical_key"] for item in report["required_policies"]}
    assert "work_authorization" in required
    assert "sponsorship_required" in required
    work = next(item for item in report["required_policies"] if item["canonical_key"] == "work_authorization")
    assert work["country"] == "US"
    assert "policy_missing" in work["blocker_codes"]


def test_us_opt_in_is_ready_when_us_facts_and_consents_are_recorded():
    profile = VerifiedProfile(
        [*_contact_facts(), _work_auth_fact("US", authorized=True, requires_sponsorship=False)]
    )
    report = build_answer_policy_readiness(
        profile=profile,
        vault=_consent_vault(),
        country_code="US",
        us_targeting_enabled=True,
        platform="greenhouse",
    )
    assert report["ready_for_browser_launch"] is True
    assert report["platform"] == "greenhouse"


def test_present_us_answers_must_still_be_consistent_when_targeting_is_off():
    profile = _complete_profile(extra_facts=[_work_auth_fact("US", authorized=False, requires_sponsorship=True)])
    vault = _consent_vault(_explicit("us_work_authorization", "Yes"))
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    assert "work_authorization_inconsistent" in _codes(report)


def test_unknown_country_fails_closed():
    profile = _complete_profile()
    report = build_answer_policy_readiness(profile=profile, vault=_consent_vault(), country_code="GB")
    assert report["country_code"] == "UNKNOWN"
    assert report["ready_for_browser_launch"] is False
    assert "country_unsupported" in _codes(report)
    assert normalize_country_code(None) == "CA"
    assert normalize_country_code("ca") == "CA"


def test_missing_background_check_consent_fails_closed():
    profile = _complete_profile()
    vault = AnswerVault([
        _explicit("data_processing_consent", "I agree"),
        _explicit("terms_consent", "I consent"),
    ])
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    assert "policy_consent_missing" in _codes(report)
    consent = next(item for item in report["required_policies"] if item["canonical_key"] == "background_check_consent")
    assert consent["satisfied"] is False


def test_missing_data_processing_consent_fails_closed_for_greenhouse():
    profile = _complete_profile()
    vault = AnswerVault([
        _explicit("background_check_consent", "Yes"),
        _explicit("terms_consent", "I consent"),
    ])
    report = build_answer_policy_readiness(
        profile=profile,
        vault=vault,
        target_url="https://boards.greenhouse.io/acme/jobs/123",
    )
    assert report["platform"] == "greenhouse"
    assert report["ready_for_browser_launch"] is False
    consent = next(item for item in report["required_policies"] if item["canonical_key"] == "data_processing_consent")
    assert consent["satisfied"] is False
    assert "policy_consent_missing" in consent["blocker_codes"]


def test_inferred_profile_consent_is_rejected():
    profile = _complete_profile()
    # profile_records setdefaults terms_consent and related keys; those are not explicit records.
    inferred = profile_records(profile)
    vault = AnswerVault([
        *inferred,
        _explicit("background_check_consent", "Yes"),
        _explicit("data_processing_consent", "I agree"),
    ])
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    terms = next(item for item in report["required_policies"] if item["canonical_key"] == "terms_consent")
    assert terms["satisfied"] is False
    assert "policy_consent_inferred" in terms["blocker_codes"]


def test_generated_or_low_confidence_consent_fails_closed():
    profile = _complete_profile()
    generated = _consent_vault()
    generated.put(_explicit("background_check_consent", "Yes", source=AnswerSource.GENERATED))
    report = build_answer_policy_readiness(profile=profile, vault=generated)
    assert report["ready_for_browser_launch"] is False
    assert "policy_provenance_unknown" in _codes(report)

    low = _consent_vault()
    low.put(_explicit("background_check_consent", "Yes", confidence=0.5))
    report = build_answer_policy_readiness(profile=profile, vault=low)
    assert report["ready_for_browser_launch"] is False
    assert "policy_confidence_low" in _codes(report)


def test_non_affirmative_consent_fails_closed():
    profile = _complete_profile()
    vault = _consent_vault()
    vault.put(_explicit("background_check_consent", "No"))
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    assert "policy_consent_not_affirmative" in _codes(report)

    vault.put(_explicit("background_check_consent", "   "))
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is False
    assert "policy_answer_missing" in _codes(report) or "policy_consent_not_affirmative" in _codes(report)


def test_vault_only_work_authorization_can_satisfy_when_explicit():
    profile = VerifiedProfile(_contact_facts())
    vault = _consent_vault(
        _explicit("canadian_work_authorization", "Yes"),
        _explicit("sponsorship_required_CA", "No"),
    )
    report = build_answer_policy_readiness(profile=profile, vault=vault)
    assert report["ready_for_browser_launch"] is True


def test_complete_canada_greenhouse_pack_is_ready():
    profile = _complete_profile()
    report = build_answer_policy_readiness(
        profile=profile,
        vault=_consent_vault(),
        target_url="https://boards.greenhouse.io/northwind/jobs/99",
        company="Northwind Credit Union",
        country_code="CA",
    )
    assert detect_platform("https://boards.greenhouse.io/northwind/jobs/99") == "greenhouse"
    assert report["platform"] == "greenhouse"
    assert report["ready_for_browser_launch"] is True
    assert report["completeness_score"] == 100.0
    assert report["blockers"] == []
    assert report["conflicts"] == []
    assert report["summary"]["blocker_count"] == 0
    required = {item["canonical_key"] for item in report["required_policies"]}
    assert required == {
        "work_authorization",
        "sponsorship_required",
        "background_check_consent",
        "data_processing_consent",
        "terms_consent",
    }


def test_truth_guard_preflight_hook_exposes_the_same_report():
    profile = _complete_profile()
    via_hook = preflight_answer_policy(profile=profile, vault=_consent_vault())
    via_module = build_answer_policy_readiness(profile=profile, vault=_consent_vault())
    assert via_hook["ready_for_browser_launch"] is True
    assert via_hook["summary"] == via_module["summary"]
    assert via_hook["guarantees"] == via_module["guarantees"]


def test_required_policy_keys_omit_us_only_keys_by_default():
    keys = required_policy_keys("CA", "greenhouse", us_targeting_enabled=False)
    assert "us_work_authorization" not in keys
    assert keys[0] == "work_authorization"
    assert "background_check_consent" in keys
    assert "data_processing_consent" in keys
