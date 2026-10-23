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


from app.knowledge_book import enforce_length_constraints


def test_enforce_length_constraints_min_and_max():
    # 1. Min length expansion
    short_text = "I have 5 years of financial operations experience."
    expanded = enforce_length_constraints(short_text, min_length=120)
    assert len(expanded) >= 120
    assert "financial operations" in expanded

    # 2. Max length truncation at clean boundary
    long_text = (
        "In my banking career at Scotiabank and BMO, I managed high-volume fraud investigations and customer escalations. "
        "I consistently prioritized risk mitigation, team safety, and strict regulatory adherence. "
        "My leadership contributed to significant improvements in SLA turnaround and accuracy."
    )
    truncated = enforce_length_constraints(long_text, max_length=130)
    assert len(truncated) <= 130
    assert truncated.endswith(".")


def test_knowledge_book_resolves_extended_catalog_questions():
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)

    # 1. Sponsorship (future)
    ans, rule = kb.resolve_screening_question(
        "Will you now or in the future require visa sponsorship?",
        options=["Yes", "No"],
    )
    assert ans == "No"
    assert "sponsorship" in rule

    # 2. Drug screen consent
    ans, rule = kb.resolve_screening_question(
        "Are you willing to complete a pre-employment drug screening?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "drug_screening_consent" in rule

    # 3. Credit check consent
    ans, rule = kb.resolve_screening_question(
        "Do you consent to an employment-related credit history check?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "credit_check_consent" in rule

    # 4. Hourly rate & salary
    ans, rule = kb.resolve_screening_question("What is your expected hourly rate?")
    assert ans == "38"
    assert "hourly_rate_expectation" in rule

    # 5. Remote workspace
    ans, rule = kb.resolve_screening_question(
        "Do you have a dedicated workspace and high-speed internet for remote work?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "remote_workspace" in rule

    # 6. Management experience & team size
    ans, rule = kb.resolve_screening_question(
        "Do you have direct people management or supervisory experience?",
        options=["Yes", "No"],
    )
    assert ans == "Yes"
    assert "management_experience" in rule

    ans_years, _ = kb.resolve_screening_question("How many years of management experience do you have?")
    assert ans_years == "3"

    ans_size, _ = kb.resolve_screening_question(
        "What was the largest team size you have managed?",
        options=["1-5", "6-10", "11+"],
    )
    assert ans_size == "1-5"

    # 7. Demographics safe decline defaults
    ans_race, _ = kb.resolve_screening_question(
        "What is your race or ethnicity?",
        options=["Asian", "White", "Black", "Prefer not to answer"],
    )
    assert ans_race == "Prefer not to answer"

    ans_vet, _ = kb.resolve_screening_question(
        "Please select your veteran status",
        options=["I am a protected veteran", "I am not a protected veteran", "I decline to answer"],
    )
    assert ans_vet == "I am not a protected veteran"

    ans_dis, _ = kb.resolve_screening_question(
        "Do you have a disability or health condition?",
        options=["Yes", "No, I do not have a disability", "I decline to answer"],
    )
    assert ans_dis == "No, I do not have a disability"
