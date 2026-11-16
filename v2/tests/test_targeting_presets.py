from __future__ import annotations

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
