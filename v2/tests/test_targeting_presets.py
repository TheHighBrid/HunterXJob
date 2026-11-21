from __future__ import annotations

from pathlib import Path

import pytest
from targeting_fixture import FRAUD_SEARCH_POSTINGS

from app.config import Settings
from app.decisioning import (
    Decision,
    DecisionContext,
    JobFacts,
    TargetingPolicy,
    _title_alignment_matches,
    evaluate_job,
    targeting_policy,
)
from app.models import Job
from app.pipeline import deterministic_gate
from app.runtime_settings import SettingsPatch, apply_changes

LOCATIONS = ("Ottawa", "Gatineau", "National Capital Region", "Remote Canada", "Canada")
EXCLUDED = ("United States", "US Remote", "Bengaluru", "Dublin", "Tokyo")
LONG_DESC = (
    "Investigate card fraud, disputes and chargebacks, review suspicious transactions, document case "
    "findings, and work with banking, AML and KYC partners to resolve client escalations promptly."
)


def _ctx(preset: str = "broad", keywords: tuple[str, ...] = ("fraud",), **overrides: bool) -> DecisionContext:
    return DecisionContext(
        target_locations=LOCATIONS,
        target_keywords=keywords,
        excluded_locations=EXCLUDED,
        excluded_titles=("software engineer", "legal counsel", "product manager"),
        policy=targeting_policy(preset, **overrides),
    )


def _job(title: str, location: str = "Remote, Canada", remote: bool = True) -> JobFacts:
    return JobFacts(title=title, company="Example Co", location=location, description=LONG_DESC, remote=remote)


def test_default_context_and_settings_use_broad_preset():
    assert DecisionContext().policy == TargetingPolicy()
    assert DecisionContext().policy.preset == "broad"
    assert Settings(_env_file=None).targeting_preset == "broad"


def test_targeting_policy_overrides_and_validation():
    policy = targeting_policy("core", allow_data_science=True, allow_onsite_canada=None)
    assert policy.allow_data_science is True
    assert policy.allow_onsite_canada is False
    with pytest.raises(ValueError):
        targeting_policy("everything")
    with pytest.raises(ValueError):
        targeting_policy("broad", allow_astronauts=True)


def test_single_keyword_synonyms_always_expand():
    assert _title_alignment_matches("Senior Insurance Claims Investigator", ("fraud",))
    assert _title_alignment_matches("Chargeback Analyst", ("fraud",))
    assert _title_alignment_matches("KYC Onboarding Associate", ("fraud",))
    assert not _title_alignment_matches("Streamlined Office Coordinator", ("fraud",))


@pytest.mark.parametrize("index", range(len(FRAUD_SEARCH_POSTINGS)))
def test_broad_preset_selects_all_fraud_search_postings(index: int):
    _family, job = FRAUD_SEARCH_POSTINGS[index]
    report = evaluate_job(job, _ctx("broad"))
    assert report.decision is Decision.SHORTLIST, f"{job.title}: {report.reason} {report.review_flags}"


def test_broad_preset_ranks_core_ic_roles_above_other_families():
    scored = [(family, evaluate_job(job, _ctx("broad")).score) for family, job in FRAUD_SEARCH_POSTINGS]
    lowest_core = min(score for family, score in scored if family == "core")
    highest_other = max(score for family, score in scored if family != "core")
    assert lowest_core > highest_other


@pytest.mark.parametrize("index", range(len(FRAUD_SEARCH_POSTINGS)))
def test_core_preset_keeps_only_core_fraud_roles(index: int):
    family, job = FRAUD_SEARCH_POSTINGS[index]
    report = evaluate_job(job, _ctx("core"))
    if family == "core":
        assert report.decision is Decision.SHORTLIST, f"{job.title}: {report.reason}"
    else:
        assert report.decision is Decision.REJECT, f"{job.title} should be rejected in core"
        assert report.vetoes and report.vetoes[0].startswith("role_family_")


def test_toronto_onsite_allowed_in_broad_only():
    job = _job("Fraud Analyst", location="Toronto, Ontario", remote=False)
    assert evaluate_job(job, _ctx("broad")).decision is Decision.SHORTLIST
    core = evaluate_job(job, _ctx("core"))
    assert core.decision is Decision.REJECT
    assert core.vetoes == ("non_remote_outside_ottawa",)


@pytest.mark.parametrize("preset", ["broad", "core"])
def test_us_and_international_stay_rejected(preset: str):
    for location in ("Remote - US", "New York, NY", "London, UK", "Bengaluru, India"):
        report = evaluate_job(_job("Fraud Analyst", location=location), _ctx(preset))
        assert report.decision is Decision.REJECT, location


@pytest.mark.parametrize("preset", ["broad", "core"])
def test_senior_ic_titles_are_allowed(preset: str):
    report = evaluate_job(_job("Senior Fraud Analyst"), _ctx(preset))
    assert report.decision is Decision.SHORTLIST


def test_plain_manager_title_is_review_in_core_and_allowed_in_broad():
    job = _job("Fraud Manager")
    core = evaluate_job(job, _ctx("core"))
    assert core.decision is Decision.REVIEW
    assert "manager title may be a people-management role" in core.review_flags
    assert evaluate_job(job, _ctx("broad")).decision is Decision.SHORTLIST


def test_account_manager_excluded_in_core():
    for title in ("Account Manager", "Account Manager, Fraud Platform", "Strategic Account Manager"):
        report = evaluate_job(_job(title), _ctx("core"))
        assert report.decision is Decision.REJECT, title


def test_unrelated_titles_still_need_alignment_in_broad():
    for title in ("Head of MyG Core Workflows", "Office Coordinator", "Senior Software Engineer, Fraud"):
        report = evaluate_job(_job(title), _ctx("broad", ("fraud", "aml", "compliance")))
        assert report.decision is Decision.REJECT, title


def test_financial_surveillance_titles_stay_core():
    report = evaluate_job(_job("AML Transaction Surveillance Analyst"), _ctx("core"))
    assert report.decision is Decision.SHORTLIST


def test_single_toggle_override_on_core():
    ctx = _ctx("core", allow_data_science=True)
    assert evaluate_job(_job("Data Scientist, Risk & Fraud"), ctx).decision is Decision.SHORTLIST
    assert evaluate_job(_job("Head of Fraud Operations"), ctx).decision is Decision.REJECT


def test_config_and_env_example_consistency():
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    env_values: dict[str, str] = {}
    for line in env_example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env_values[key.strip()] = val.strip()

    settings = Settings(_env_file=None)
    assert settings.targeting_preset == env_values["TARGETING_PRESET"]
    assert settings.target_keyword_list == [item.strip() for item in env_values["TARGET_KEYWORDS"].split(",") if item.strip()]
    assert settings.target_location_list == [item.strip() for item in env_values["TARGET_LOCATIONS"].split(",") if item.strip()]
    assert settings.excluded_location_list == [item.strip() for item in env_values["EXCLUDED_LOCATIONS"].split(",") if item.strip()]
    assert settings.excluded_title_list == [item.strip() for item in env_values["EXCLUDED_TITLES"].split(",") if item.strip()]


def test_blank_targeting_overrides_and_preset_normalization_in_settings():
    settings = Settings(
        _env_file=None,
        targeting_preset=" CORE ",
        targeting_allow_senior_leadership="",
        targeting_allow_data_science="   ",
        targeting_allow_onsite_canada="true",
    )
    assert settings.targeting_preset == "core"
    assert settings.targeting_allow_senior_leadership is None
    assert settings.targeting_allow_data_science is None
    assert settings.targeting_allow_onsite_canada is True

    policy = targeting_policy(settings.targeting_preset, **settings.targeting_overrides)
    assert policy.preset == "core"
    assert policy.allow_senior_leadership is False
    assert policy.allow_onsite_canada is True


def test_runtime_settings_patch_compatible_with_deterministic_gate():
    settings = Settings(_env_file=None)
    patch = SettingsPatch(target_keywords=["fraud", "aml"], blacklisted_companies=["Blocked Bank"])
    apply_changes(settings, patch.changes())

    blocked = Job(
        source="test",
        external_id="blocked-1",
        title="Fraud Analyst",
        company="Blocked Bank",
        location="Ottawa, ON",
        url="https://example.test/blocked",
        description=LONG_DESC,
        remote=False,
    )
    res_blocked = deterministic_gate(blocked, settings)
    assert res_blocked.decision is Decision.REJECT
    assert "blacklisted_company" in (res_blocked.report or {}).get("vetoes", [])

    allowed = Job(
        source="test",
        external_id="allowed-1",
        title="Fraud Analyst",
        company="Open Bank",
        location="Ottawa, ON",
        url="https://example.test/allowed",
        description=LONG_DESC,
        remote=False,
    )
    res_allowed = deterministic_gate(allowed, settings)
    assert res_allowed.decision is Decision.SHORTLIST


def test_direct_keyword_matching_avoids_substring_false_positives():
    report = evaluate_job(
        _job("Streamlined Office Coordinator"),
        _ctx("broad", ("fraud", "aml", "compliance")),
    )
    assert report.decision is Decision.REJECT
    assert report.vetoes == ("no_role_overlap",)


def test_core_preset_catches_hyphenated_onsite_and_negated_remote_descriptions():
    # Hyphenated "On-Site" / "In-Person" in location or title even if remote flag is mis-set.
    for job in (
        _job("Fraud Analyst", location="Toronto, ON (On-Site)", remote=True),
        _job("Fraud Analyst - In-Person", location="Vancouver, BC", remote=True),
        JobFacts(
            title="Fraud Analyst",
            company="Example Co",
            location="Toronto, ON",
            description="In-office role in downtown Toronto. " + LONG_DESC,
            remote=False,
        ),
        JobFacts(
            title="Fraud Analyst",
            company="Example Co",
            location="Toronto, ON",
            description="Hybrid role (2 days/week), not 100% remote. " + LONG_DESC,
            remote=False,
        ),
    ):
        report = evaluate_job(job, _ctx("core"))
        assert report.decision is Decision.REJECT, job
        assert report.vetoes == ("non_remote_outside_ottawa",)


def test_explicit_city_target_uses_word_boundaries_and_canada_target_scope():
    # "on" as a target token must not match "Toronto" as an explicit city target.
    ctx_on = DecisionContext(
        target_locations=("Ottawa", "ON", "Canada"),
        target_keywords=("fraud",),
        policy=targeting_policy("core"),
    )
    rep_toronto_core = evaluate_job(_job("Fraud Analyst", location="Toronto, Ontario", remote=False), ctx_on)
    assert rep_toronto_core.decision is Decision.REJECT
    assert rep_toronto_core.vetoes == ("non_remote_outside_ottawa",)

    # Ottawa-only target_locations must not allow Toronto onsite even under broad.
    ctx_ottawa_only = DecisionContext(
        target_locations=("Ottawa",),
        target_keywords=("fraud",),
        policy=targeting_policy("broad"),
    )
    rep_ottawa_only = evaluate_job(_job("Fraud Analyst", location="Toronto, Ontario", remote=False), ctx_ottawa_only)
    assert rep_ottawa_only.decision is Decision.REJECT
    assert rep_ottawa_only.vetoes == ("location_not_eligible",)

    # When "Canada" is in target_locations and location explicitly includes Canada, location_fit stays 5.0.
    rep_broad_ca = evaluate_job(_job("Fraud Analyst", location="Toronto, Ontario, Canada", remote=False), _ctx("broad"))
    assert rep_broad_ca.decision is Decision.SHORTLIST
    loc_dim = next(d for d in rep_broad_ca.dimensions if d.name == "location_fit")
    assert loc_dim.score == 5.0


@pytest.mark.parametrize("preset", ["broad", "core"])
def test_foreign_location_rejected_even_when_title_mentions_canada(preset: str):
    report = evaluate_job(
        _job("Fraud Analyst (Canada)", location="New York, NY", remote=False),
        _ctx(preset),
    )
    assert report.decision is Decision.REJECT
    assert "international_non_canada" in report.vetoes or "excluded_location" in report.vetoes


def test_supervisor_and_gestionnaire_titles_respect_preset_and_ml_data_science_toggle():
    for title in ("Fraud Supervisor", "Gestionnaire de fraude"):
        core = evaluate_job(_job(title), _ctx("core"))
        assert core.decision is Decision.REVIEW, title
        assert "manager title may be a people-management role" in core.review_flags
        assert evaluate_job(_job(title), _ctx("broad")).decision is Decision.SHORTLIST

    ml_job = _job("ML Engineer, Fraud")
    assert evaluate_job(ml_job, _ctx("broad")).decision is Decision.SHORTLIST
    ml_core = evaluate_job(ml_job, _ctx("core"))
    assert ml_core.decision is Decision.REJECT
    assert ml_core.vetoes == ("role_family_data_science",)

    vp_job = evaluate_job(_job("VP, Fraud Operations"), _ctx("broad"))
    assert vp_job.decision is Decision.REVIEW
    assert "seniority may exceed target level" in vp_job.review_flags
