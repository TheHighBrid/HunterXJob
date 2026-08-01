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


def test_sensitive_answer_requires_explicit_provenance():
    vault = AnswerVault([
        AnswerRecord("work_authorization", "Authorized", AnswerSource.RESUME, confidence=1.0),
    ])
    result = vault.resolve(FieldRequest("work_authorization"))
    assert result.status is ResolutionStatus.REVIEW
    assert "explicit user or policy" in result.reason


def test_user_answer_resolves_sensitive_field():
    vault = AnswerVault([
        AnswerRecord("work_authorization", "Authorized to work in Canada", AnswerSource.USER, confidence=1.0),
    ])
    result = vault.resolve(FieldRequest("work_authorization"))
    assert result.status is ResolutionStatus.RESOLVED
    assert result.value == "Authorized to work in Canada"


def test_liveness_classifier_distinguishes_expired_blocked_and_live():
    assert classify_liveness(410, "https://example.test/job", "").status is Liveness.EXPIRED
    assert classify_liveness(200, "https://example.test/job", "Verify you are human").status is Liveness.BLOCKED
    assert classify_liveness(200, "https://example.test/job", "Apply for this job").status is Liveness.LIVE
