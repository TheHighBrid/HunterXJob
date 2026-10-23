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
