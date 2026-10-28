from pathlib import Path
from app.knowledge_book import KnowledgeBook
from app.truth_guard import audit_form_answers, enforce_answer_truthfulness
from app.decisioning import Decision, DecisionContext, JobFacts, evaluate_job
from app.profile import Fact, VerifiedProfile

EXAMPLE_YAML = Path(__file__).parent.parent / "examples" / "profile.example.yaml"


def test_truth_guard_cpa_question_fails_closed():
    """Verify that CPA designation questions NEVER return Yes when candidate lacks CPA."""
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)
    
    # Wealthsimple style question
    q_label = "Do you hold a CPA designation (or equivalent)?"
    ans, rule = kb.resolve_screening_question(q_label, options=["Yes", "No"])
    assert ans == "No"
    assert "truth_guard" in rule or "catalog" in rule

    # Test fallback option choice
    ans_fallback, _ = kb.resolve_with_llm_or_fallback(q_label, options=["Yes", "No"])
    assert ans_fallback == "No"


def test_truth_guard_security_clearance_fails_closed():
    """Verify that security clearance questions return negative, never Other or Yes."""
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)

    q_label = "Do you currently hold an active security clearance?"
    options = [
        "Top Secret",
        "Secret",
        "Reliability",
        "I do not have security clearance",
        "Other",
    ]
    ans, rule = kb.resolve_screening_question(q_label, options=options)
    assert ans == "I do not have security clearance"
    assert "security_clearance" in rule

    # Also test fallback
    ans_fb, _ = kb.resolve_with_llm_or_fallback(q_label, options=options)
    assert ans_fb == "I do not have security clearance"


def test_audit_and_enforce_form_answers_truthfulness():
    facts = [
        Fact("contact", "contact", {"first_name": "Mohamed", "last_name": "Alem"}),
        Fact("cert_1", "certification", {"name": "CompTIA A+"}),
        Fact("cert_2", "certification", {"name": "ITIL 4 Foundation"}),
    ]
    profile = VerifiedProfile(facts)

    bad_answers = {
        "cpa_question": "Yes",
        "clearance_question": "Top Secret",
        "regular_question": "Yes",
    }
    labels = {
        "cpa_question": "Do you hold a CPA designation (or equivalent)?",
        "clearance_question": "Select your security clearance level",
        "regular_question": "Are you at least 18 years of age?",
    }

    violations = audit_form_answers(bad_answers, profile, labels=labels)
    assert len(violations) == 2
    assert any("CPA" in v for v in violations)
    assert any("SECURITY_CLEARANCE" in v for v in violations)

    sanitized, corrections = enforce_answer_truthfulness(bad_answers, profile, labels=labels)
    assert sanitized["cpa_question"] == "No"
    assert sanitized["clearance_question"] == "I do not have security clearance"
    assert sanitized["regular_question"] == "Yes"
    assert len(corrections) == 2


def test_job_eligibility_vetoes_hybrid_outside_ottawa():
    """Hybrid/on-site outside Ottawa–Gatineau must be rejected before browser launch."""
    ctx = DecisionContext(
        target_locations=("Ottawa", "Gatineau", "National Capital Region", "Remote Canada", "Canada"),
        target_keywords=("fraud", "compliance", "operations"),
        shortlist_threshold=50.0,
    )

    # 1. Cohere London UK hybrid -> REJECT
    cohere_uk = JobFacts(
        title="Technical Operations Specialist",
        company="Cohere",
        location="London, United Kingdom (Hybrid)",
        description="Support fraud and compliance operations for our London office 3 days a week.",
        remote=False,
    )
    rep_uk = evaluate_job(cohere_uk, ctx)
    assert rep_uk.decision is Decision.REJECT
    assert any("outside_ottawa" in v or "international" in v for v in rep_uk.vetoes)

    # 2. Toronto hybrid role -> REJECT
    toronto_hybrid = JobFacts(
        title="Fraud Specialist",
        company="Fintech Corp",
        location="Toronto, ON",
        description="Hybrid role requiring 2 days per week in our downtown Toronto office. Fraud investigations and compliance.",
        remote=False,
    )
    rep_toronto = evaluate_job(toronto_hybrid, ctx)
    assert rep_toronto.decision is Decision.REJECT
    assert "non_remote_outside_ottawa" in rep_toronto.vetoes

    # 3. Ottawa in-person role -> ACCEPT (not vetoed by location)
    ottawa_job = JobFacts(
        title="Fraud & Compliance Specialist",
        company="Ottawa Credit Union",
        location="Ottawa, ON",
        description="On-site role in Ottawa handling banking fraud, customer escalations, and AML compliance. 5 years experience.",
        remote=False,
    )
    rep_ottawa = evaluate_job(ottawa_job, ctx)
    assert rep_ottawa.decision is not Decision.REJECT

    # 4. Canada-wide 100% Remote role -> ACCEPT
    remote_job = JobFacts(
        title="Fraud Analyst",
        company="Wealthsimple",
        location="Remote, Canada",
        description="100% remote opportunity across Canada handling customer fraud investigations, AML and regulatory compliance.",
        remote=True,
    )
    rep_remote = evaluate_job(remote_job, ctx)
    assert rep_remote.decision is not Decision.REJECT


def test_job_eligibility_vetoes_mandatory_cpa_requirement():
    """Roles requiring mandatory CPA must be rejected before opening forms."""
    ctx = DecisionContext(
        target_locations=("Ottawa", "Remote Canada", "Canada"),
        target_keywords=("compliance", "controls", "finance"),
        shortlist_threshold=50.0,
    )

    wealthsimple_cpa = JobFacts(
        title="Manager, Internal Controls",
        company="Wealthsimple",
        location="Remote, Canada",
        description="Manage internal controls and compliance audits. CPA designation required. Minimum 5 years accounting.",
        remote=True,
    )
    rep = evaluate_job(wealthsimple_cpa, ctx)
    assert rep.decision is Decision.REJECT
    assert "unverified_mandatory_credential" in rep.vetoes


def test_client_facing_experience_question_resolved():
    """Cohere client-facing experience question must return a substantive narrative."""
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)

    q_label = "Describe your experience working in client-facing or customer-facing roles."
    ans, rule = kb.resolve_screening_question(q_label)
    assert ans is not None
    assert len(ans) > 50
    assert "client-facing" in ans.lower()
    assert "profile_client_facing_narrative" in rule
