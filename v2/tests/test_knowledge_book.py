from pathlib import Path
from app.knowledge_book import KnowledgeBook

EXAMPLE_YAML = Path(__file__).parent.parent / "examples" / "profile.example.yaml"


def test_knowledge_book_loads_from_yaml():
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)
    assert kb.profile is not None
    assert kb.profile.contact["first_name"] == "Avery"
    assert kb.profile.contact["last_name"] == "Quinn"


def test_knowledge_book_contact_answers():
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)
    contact = kb.get_contact_answers()

    assert contact["first_name"] == "Avery"
    assert contact["last_name"] == "Quinn"
    assert contact["full_name"] == "Avery Quinn"
    assert contact["name"] == "Avery Quinn"
    assert contact["email"] == "avery.quinn@example.com"
    assert contact["phone"] == "+1 613 555 0142"
    assert contact["org"] == "Northwind Credit Union"
    assert "urls[LinkedIn]" in contact


def test_knowledge_book_resolves_common_screening_questions():
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)

    # 1. Prior employment
    ans, rule = kb.resolve_screening_question(
        "Have you ever been previously employed by D2L in any capacity?",
        options=["No", "Yes - Intern", "Yes - Full Time"],
    )
    assert ans == "No"
    assert "prior_employment" in rule

    # 2. Relocation
    ans, rule = kb.resolve_screening_question(
        "Are you willing to relocate to the location for which this role is posted?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "relocation" in rule

    # 3. Background check consent
    ans, rule = kb.resolve_screening_question(
        "Do you consent to a standard pre-employment background check?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "background_check" in rule

    # 4. Terms and disclaimer
    ans, rule = kb.resolve_screening_question(
        "Have you read and agree to the below Disclaimer and Consent?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "terms_consent" in rule

    # 5. Canadian work authorization
    ans, rule = kb.resolve_screening_question(
        "Are you legally eligible to work in Canada where this position is located?",
        options=["Authorized to work in Canada", "Need sponsorship"],
    )
    assert ans == "Authorized to work in Canada"
    assert "work_authorization" in rule

    # 6. How did you hear
    ans, rule = kb.resolve_screening_question(
        "Which of the sources below best describe how you heard about D2L?",
        options=["Company Website", "LinkedIn", "Referral", "Other"],
    )
    assert ans == "LinkedIn"
    assert "how_heard" in rule
