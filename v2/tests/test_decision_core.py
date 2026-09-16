from app.answer_vault import (
    AnswerRecord,
    AnswerSource,
    AnswerVault,
    FieldRequest,
    ResolutionStatus,
)
from app.decisioning import Decision, DecisionContext, JobFacts, evaluate_job
from app.liveness import Liveness, classify_liveness


def test_decision_engine_rejects_excluded_location():
    report = evaluate_job(
        JobFacts(
            title="Fraud Analyst",
            company="Example",
            location="US Remote",
            description="Fraud and compliance operations",
            remote=True,
        ),
        DecisionContext(
            target_locations=("Ottawa", "Remote Canada", "Canada"),
            target_keywords=("fraud", "compliance"),
            excluded_locations=("United States", "US Remote"),
        ),
    )
    assert report.decision is Decision.REJECT
    assert report.reason == "excluded location"


def test_decision_engine_scores_relevant_canadian_role():
    report = evaluate_job(
        JobFacts(
            title="Bilingual Fraud Analyst",
            company="Canadian Bank",
            location="Ottawa, Ontario, Canada",
            description=(
                "Investigate fraud alerts, follow AML and compliance controls, document evidence, "
                "support credit-card disputes, and communicate with customers in English and French. "
                "The analyst works with banking operations and payment-risk teams."
            ),
        ),
        DecisionContext(
            target_locations=("Ottawa", "Remote Canada", "Canada"),
            target_keywords=("fraud", "compliance", "bilingual", "aml", "credit"),
            excluded_locations=("United States", "US Remote"),
            excluded_titles=("software engineer",),
        ),
    )
    assert report.decision is Decision.SHORTLIST
    assert report.score >= 60
    assert "fraud" in report.matched_keywords
    assert {dimension.name for dimension in report.dimensions} == {
        "role_relevance",
        "location_fit",
        "sector_fit",
        "language_fit",
        "seniority_fit",
        "evidence_quality",
    }


def test_company_name_does_not_create_role_overlap():
    report = evaluate_job(
        JobFacts(
            title="Office Administrator",
            company="Fraud Solutions Inc",
            location="Ottawa, Ontario, Canada",
            description="Manage calendars, invoices, records, and office supplies for the local team.",
        ),
        DecisionContext(
            target_locations=("Ottawa",),
            target_keywords=("fraud",),
        ),
    )
    assert report.decision is Decision.REJECT
    assert report.vetoes == ("no_role_overlap",)


def test_unicode_blacklist_and_keyword_normalization_is_not_empty():
    rejected = evaluate_job(
        JobFacts(
            title="Analyste conformité",
            company="Example",
            location="Montréal, Québec",
            description="Conformité et risques financiers.",
        ),
        DecisionContext(
            target_locations=("Montréal",),
            target_keywords=("conformité",),
            blacklisted_companies=("Société interdite",),
        ),
    )
    assert rejected.decision is Decision.SHORTLIST

    blacklisted = evaluate_job(
        JobFacts(
            title="Analyste conformité",
            company="Société interdite",
            location="Montréal, Québec",
            description="Conformité et risques financiers.",
        ),
        DecisionContext(
            target_locations=("Montréal",),
            target_keywords=("conformité",),
            blacklisted_companies=("Société interdite",),
        ),
    )
    assert blacklisted.decision is Decision.REJECT
    assert blacklisted.vetoes == ("blacklisted_company",)


def test_threshold_only_review_has_accurate_reason():
    report = evaluate_job(
        JobFacts(
            title="Fraud Analyst",
            company="Example",
            location="Ottawa",
            description="Fraud operations and case review.",
        ),
        DecisionContext(target_locations=("Ottawa",), target_keywords=("fraud",), shortlist_threshold=99.0),
    )
    assert report.decision is Decision.REVIEW
    assert report.review_flags == ()
    assert report.reason == "below shortlist threshold"


def test_sensitive_answer_requires_explicit_provenance():
    vault = AnswerVault([
        AnswerRecord("work_authorization", "Authorized", AnswerSource.RESUME, confidence=1.0),
    ])
    result = vault.resolve(FieldRequest("work_authorization"))
    assert result.status is ResolutionStatus.REVIEW
    assert "explicit user or policy" in result.reason


def test_sensitive_alias_is_canonicalized():
    vault = AnswerVault([
        AnswerRecord("WORK_AUTHORIZATION", "Authorized to work in Canada", AnswerSource.USER, confidence=1.0),
    ])
    result = vault.resolve(FieldRequest("work-authorization"))
    assert result.status is ResolutionStatus.RESOLVED
    assert result.key == "work_authorization"


def test_empty_required_answer_is_missing():
    vault = AnswerVault([
        AnswerRecord("work_authorization", "", AnswerSource.USER, confidence=1.0),
    ])
    result = vault.resolve(FieldRequest("work_authorization"))
    assert result.status is ResolutionStatus.MISSING
    assert "empty" in result.reason


def test_liveness_classifier_distinguishes_expired_blocked_and_live():
    assert classify_liveness(410, "https://example.test/job", "").status is Liveness.EXPIRED
    assert classify_liveness(200, "https://example.test/job", "Verify you are human").status is Liveness.BLOCKED
    assert classify_liveness(200, "https://example.test/job", "Apply for this job").status is Liveness.LIVE


def test_generic_job_description_does_not_prove_liveness():
    result = classify_liveness(200, "https://example.test/careers", "Welcome. Here is the job description for our careers site.")
    assert result.status is Liveness.REVIEW
