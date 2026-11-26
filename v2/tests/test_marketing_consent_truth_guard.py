"""Generic terms_consent must never fill a marketing opt-in.

Field shapes for consent[marketing] are copied from the Magnet Forensics
(Lever) dry-run (Bot 04 run-09 plan_trace): key, label, section, type, and
a single checkbox option "1". No personal data is included.
"""
from __future__ import annotations

from app.answer_policy_compiler import load_master_policy
from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault, FieldRequest, ResolutionStatus
from app.form_engine import ControlType, FormControl, plan_fill

# Magnet Forensics (Lever) marketing checkbox, as recorded in plan_trace.
MAGNET_MARKETING = FormControl(
    key="consent[marketing]",
    label="consent[marketing]",
    control_type=ControlType.CHECKBOX,
    required=False,
    options=["1"],
    name="consent[marketing]",
    input_type="checkbox",
    confidence=0.85,
    sensitive=False,
    legal=False,
    section="consent",
    vault_keys=["consent[marketing]"],
    option_values={"1": "1"},
)

AFFIRMATIVE = {"1", "yes", "true"}


def _terms_only_vault() -> AnswerVault:
    return AnswerVault(
        [
            AnswerRecord(key="terms_consent", value="Yes", source=AnswerSource.USER),
            AnswerRecord(key="agreement_consent", value="Yes", source=AnswerSource.USER),
            AnswerRecord(key="general_consent", value="Yes", source=AnswerSource.USER),
        ]
    )


def _privacy_control() -> FormControl:
    return FormControl(
        key="privacy_statement",
        label="Accept applicant privacy statement?",
        control_type=ControlType.CHECKBOX,
        required=False,
        options=["1"],
        name="privacy_statement",
        input_type="checkbox",
        confidence=0.85,
        legal=True,
        section="consent",
        vault_keys=["privacy_statement"],
        option_values={"1": "1"},
    )


def _terms_control() -> FormControl:
    return FormControl(
        key="terms_and_conditions",
        label="Do you agree to our terms and conditions?",
        control_type=ControlType.CHECKBOX,
        required=False,
        options=["1"],
        name="terms_and_conditions",
        input_type="checkbox",
        confidence=0.85,
        legal=True,
        section="consent",
        vault_keys=["terms_and_conditions"],
        option_values={"1": "1"},
    )


def _marketing_item(plan) -> object:
    return next(item for item in plan.items if item.control.key == "consent[marketing]")


def _is_affirmative(value: object) -> bool:
    return str(value).strip().lower() in AFFIRMATIVE


def test_terms_consent_does_not_fill_marketing_opt_in() -> None:
    """terms_consent=Yes with no marketing answer must not opt in."""
    plan = plan_fill([MAGNET_MARKETING], _terms_only_vault())
    item = _marketing_item(plan)
    assert item.status in {"skip", "fill"}
    if item.status == "fill":
        assert item.value is not None
        assert not _is_affirmative(item.value)
    resolved = _terms_only_vault().resolve(FieldRequest(key="consent[marketing]", required=False))
    assert resolved.status is not ResolutionStatus.RESOLVED or not _is_affirmative(resolved.value)


def test_lever_consent_marketing_is_classified_as_marketing_not_terms() -> None:
    from app.truth_guard import classify_consent_field

    assert classify_consent_field("consent[marketing]", "consent[marketing]") == "marketing"
    assert classify_consent_field("consent[marketing]", "") == "marketing"
    policy = load_master_policy(force_reload=True)
    item, score = policy.find_match("consent[marketing]")
    assert item is not None
    assert item.key == "marketing_consent"
    assert item.key != "terms_consent"
    assert score >= 70.0
    labeled, _ = policy.find_match("Marketing updates?")
    assert labeled is not None
    assert labeled.key == "marketing_consent"


def test_explicit_marketing_yes_may_be_filled() -> None:
    vault = AnswerVault(
        [
            AnswerRecord(key="terms_consent", value="Yes", source=AnswerSource.USER),
            AnswerRecord(key="marketing_consent", value="Yes", source=AnswerSource.USER),
        ]
    )
    plan = plan_fill([MAGNET_MARKETING], vault)
    item = _marketing_item(plan)
    assert item.status == "fill"
    assert _is_affirmative(item.value)


def test_terms_and_privacy_still_resolve_from_terms_consent() -> None:
    vault = _terms_only_vault()
    plan = plan_fill([_privacy_control(), _terms_control()], vault)
    assert len(plan.items) == 2
    for item in plan.items:
        assert item.status == "fill"
        assert _is_affirmative(item.value)
    resolved = vault.resolve(FieldRequest(key="terms_consent"))
    assert resolved.status is ResolutionStatus.RESOLVED
    assert resolved.value == "Yes"
    policy = load_master_policy(force_reload=True)
    privacy, _ = policy.find_match("Accept applicant privacy statement?")
    terms, _ = policy.find_match("Do you agree to our terms and conditions?")
    assert privacy is not None and privacy.key == "terms_consent"
    assert terms is not None and terms.key == "terms_consent"
