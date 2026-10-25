from app.answer_vault import (
    AnswerRecord,
    AnswerSource,
    AnswerVault,
    FieldRequest,
    ResolutionStatus,
)


def test_answer_vault_resolves_sensitive_keys_strictly():
    vault = AnswerVault([
        AnswerRecord(key="sponsorship", value="No", source=AnswerSource.USER),
        AnswerRecord(key="salary_expectation", value="75000", source=AnswerSource.POLICY),
        AnswerRecord(key="work_authorization", value="Yes", source=AnswerSource.GENERATED),
    ])

    # User provenance succeeds
    res_spons = vault.resolve(FieldRequest(key="sponsorship_required"))
    assert res_spons.status == ResolutionStatus.RESOLVED
    assert res_spons.value == "No"

    # Policy provenance succeeds
    res_sal = vault.resolve(FieldRequest(key="desired_salary"))
    assert res_sal.status == ResolutionStatus.RESOLVED
    assert res_sal.value == "75000"

    # Generated provenance on sensitive key fails closed to REVIEW
    res_auth = vault.resolve(FieldRequest(key="work_authorization"))
    assert res_auth.status == ResolutionStatus.REVIEW
    assert "sensitive answer requires explicit user or policy provenance" in res_auth.reason


def test_answer_vault_length_constraints():
    vault = AnswerVault([
        AnswerRecord(key="short_summary", value="Too short", source=AnswerSource.USER),
        AnswerRecord(key="long_summary", value="A" * 200, source=AnswerSource.USER),
        AnswerRecord(key="valid_summary", value="A" * 80, source=AnswerSource.USER),
    ])

    # Fails min_length constraint
    res_short = vault.resolve(FieldRequest(key="short_summary", min_length=50))
    assert res_short.status == ResolutionStatus.REVIEW
    assert "below minimum requirement" in res_short.reason

    # Fails max_length constraint
    res_long = vault.resolve(FieldRequest(key="long_summary", max_length=100))
    assert res_long.status == ResolutionStatus.REVIEW
    assert "exceeds maximum limit" in res_long.reason

    # Passes constraints
    res_valid = vault.resolve(FieldRequest(key="valid_summary", min_length=50, max_length=100))
    assert res_valid.status == ResolutionStatus.RESOLVED
    assert res_valid.value == "A" * 80


def test_answer_vault_key_aliases_broad_coverage():
    vault = AnswerVault([
        AnswerRecord(key="salary_expectation", value="85000", source=AnswerSource.POLICY),
        AnswerRecord(key="willing_to_relocate", value="Yes", source=AnswerSource.POLICY),
        AnswerRecord(key="remote_workspace", value="Yes", source=AnswerSource.USER),
        AnswerRecord(key="background_check_consent", value="Yes", source=AnswerSource.POLICY),
        AnswerRecord(key="start_date", value="Immediate", source=AnswerSource.USER),
        AnswerRecord(key="language_french", value="Bilingual", source=AnswerSource.USER),
        AnswerRecord(key="linkedin_url", value="https://linkedin.com/in/mohamedalem", source=AnswerSource.USER),
    ])

    # Resolves via salary aliases
    for k in ("desired_salary", "expected_salary", "salary_range", "compensation_expectation", "desired_compensation"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "85000"

    # Resolves via relocation aliases
    for k in ("relocation", "relocate", "open_to_relocation"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "Yes"

    # Resolves via remote workspace aliases
    for k in ("home_office", "dedicated_workspace", "remote_work_environment"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "Yes"

    # Resolves via background check aliases
    for k in ("criminal_check_consent", "screening_consent", "background_check"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "Yes"

    # Resolves via start date / notice period aliases
    for k in ("availability_date", "available_start_date", "earliest_start", "notice_period", "when_can_you_start"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "Immediate"

    # Resolves via French / language aliases
    for k in ("french_proficiency", "bilingual_french", "language_proficiency"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "Bilingual"

    # Resolves via LinkedIn aliases
    for k in ("linkedin", "social_linkedin", "linkedin_profile"):
        res = vault.resolve(FieldRequest(key=k))
        assert res.status == ResolutionStatus.RESOLVED
        assert res.value == "https://linkedin.com/in/mohamedalem"


def test_form_engine_option_matching_with_different_wording():
    from app.form_engine import ControlType, FormControl, _match_option

    # 1. Boolean equivalence on verbose ATS options
    work_ctrl = FormControl(
        key="work_auth",
        label="Are you legally authorized to work in Canada?",
        control_type=ControlType.SELECT,
        options=[
            "Yes, I am legally authorized to work in Canada without restriction",
            "No, I require current or future visa sponsorship",
        ]
    )
    assert _match_option(work_ctrl, "Yes") == "Yes, I am legally authorized to work in Canada without restriction"
    assert _match_option(work_ctrl, "No") == "No, I require current or future visa sponsorship"

    # 2. Token overlap & substring on French language options
    lang_ctrl = FormControl(
        key="french_skill",
        label="Bilingual French proficiency",
        control_type=ControlType.SELECT,
        options=[
            "Bilingual - English and Canadian French",
            "Fluent in English only",
            "Intermediate French",
            "Basic French",
        ]
    )
    assert _match_option(lang_ctrl, "Bilingual") == "Bilingual - English and Canadian French"

    # 3. Multiselect partial and synonym matching
    multi_ctrl = FormControl(
        key="avail",
        label="Availability shifts",
        control_type=ControlType.MULTISELECT,
        options=[
            "Evenings and Weekends",
            "Weekday Business Hours",
            "Overnight Rotation",
        ]
    )
    matched = _match_option(multi_ctrl, "Evenings|Weekday")
    assert matched == ["Evenings and Weekends", "Weekday Business Hours"]

    # 4. Ambiguous ties fail closed to None (no guessing)
    ambiguous_ctrl = FormControl(
        key="sched",
        label="Schedule preference",
        control_type=ControlType.SELECT,
        options=[
            "Yes - Full time 40hrs",
            "Yes - Part time 20hrs",
        ]
    )
    assert _match_option(ambiguous_ctrl, "Yes") is None
