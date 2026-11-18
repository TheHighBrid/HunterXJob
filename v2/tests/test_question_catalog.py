from app.question_catalog import (
    OptionRecord,
    boolean_value,
    classify_question,
    match_answers_to_options,
    normalize_text,
    score_option,
)


def test_normalize_text():
    assert normalize_text("  Montréal,  Québec! ") == "montreal quebec"
    assert normalize_text("It’s a ‘great’ role…") == "it's a 'great' role"
    assert normalize_text("C++ & C# / .NET") == "c++ c# / .net"
    assert normalize_text(None) == ""


def test_boolean_value():
    # English truthy / falsy
    assert boolean_value("Yes") is True
    assert boolean_value("I agree") is True
    assert boolean_value("Authorized") is True
    assert boolean_value("No") is False
    assert boolean_value("Decline") is False

    # French truthy / falsy
    assert boolean_value("Oui") is True
    assert boolean_value("J accepte") is True
    assert boolean_value("Non") is False
    assert boolean_value("Je refuse") is False

    # Neutral / non-boolean
    assert boolean_value("Maybe") is None
    assert boolean_value("75000") is None


def test_score_option_4_tiers():
    opt = OptionRecord(key="0", label="Authorized to work in Canada", value="auth_ca")

    # Tier 1: Exact match (100)
    assert score_option("Authorized to work in Canada", opt) == 100
    assert score_option("auth_ca", opt) == 100

    # Tier 2: Boolean equivalence (92)
    yes_opt = OptionRecord(key="1", label="Oui, j'accepte", value="opt_oui")
    assert score_option("Yes", yes_opt) == 92

    # Tier 3: High token Jaccard similarity (84)
    sim_opt = OptionRecord(key="2", label="Authorized to work within Canada", value="ca")
    assert score_option("Authorized to work in Canada", sim_opt) == 84

    # Tier 4: Substring match (70)
    sub_opt = OptionRecord(key="3", label="Full-time permanent position in Canada", value="ft")
    assert score_option("Full-time", sub_opt) == 70

    # Disabled option should score 0
    dis_opt = OptionRecord(key="4", label="Yes", value="y", disabled=True)
    assert score_option("Yes", dis_opt) == 0


def test_match_answers_to_options_single_and_multiple():
    options = [
        OptionRecord(key="0", label="Please select...", value=""),
        OptionRecord(key="1", label="No, I do not require sponsorship", value="no_spons"),
        OptionRecord(key="2", label="Yes, I require sponsorship now or in the future", value="yes_spons"),
    ]

    res = match_answers_to_options(["No"], options, allow_multiple=False)
    assert res.ok is True
    assert res.first_matched.key == "1"

    # Multiple match
    skills_options = [
        OptionRecord(key="0", label="English (Fluent)", value="en"),
        OptionRecord(key="1", label="French (Bilingual)", value="fr"),
        OptionRecord(key="2", label="Spanish", value="es"),
    ]
    res_multi = match_answers_to_options(["English", "French"], skills_options, allow_multiple=True)
    assert res_multi.ok is True
    assert len(res_multi.matched) == 2
    assert [m.key for m in res_multi.matched] == ["0", "1"]


def test_classify_question_broad_scope():
    # 1. Sponsorship now or future
    item = classify_question("Will you now or in the future require visa sponsorship?")
    assert item is not None
    assert "sponsorship" in item["canonical_key"]

    # 2. Clearances
    item = classify_question("Do you currently hold an active Secret security clearance?")
    assert item is not None
    assert item["canonical_key"] == "security_clearance"

    # 3. Consents (Drug screen, credit check, background check)
    item_drug = classify_question("Are you willing to undergo a pre-employment drug screening?")
    assert item_drug is not None
    assert item_drug["canonical_key"] == "drug_screening_consent"

    item_credit = classify_question("Do you consent to an employment credit history check?")
    assert item_credit is not None
    assert item_credit["canonical_key"] == "credit_check_consent"

    # 4. Management experience & team size
    item_mgmt = classify_question("How many years of people management or supervisory experience do you have?")
    assert item_mgmt is not None
    assert item_mgmt["canonical_key"] == "management_years"

    item_team = classify_question("What was the largest team size you have managed?")
    assert item_team is not None
    assert item_team["canonical_key"] == "team_size_managed"

    # 5. Remote workspace
    item_remote = classify_question("Do you have a quiet dedicated home office with reliable high-speed internet?")
    assert item_remote is not None
    assert item_remote["canonical_key"] == "remote_workspace"

    # 6. Prior application / interview
    item_prior = classify_question("Have you ever applied to or interviewed with Wealthsimple before?")
    assert item_prior is not None
    assert "prior" in item_prior["canonical_key"]
