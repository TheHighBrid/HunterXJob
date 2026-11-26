"""Generic consent fallback keys must not fill non-allowlisted controls.

The Magnet Forensics (Lever) marketing checkbox is copied from the Bot 04
run-09 plan_trace (ats-batch/run1/job-09.json and
run-09-lever-magnetforensics): key, label, section, type, and a single
checkbox option "1". No personal data is included.
"""
from __future__ import annotations

import pytest

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
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


def _checkbox(
    key: str,
    label: str,
    *,
    legal: bool = False,
    required: bool = False,
    name: str = "",
    vault_keys: list[str] | None = None,
) -> FormControl:
    resolved_name = name or key
    return FormControl(
        key=key,
        label=label,
        control_type=ControlType.CHECKBOX,
        required=required,
        options=["1"],
        name=resolved_name,
        input_type="checkbox",
        confidence=0.85,
        legal=legal,
        section="consent",
        vault_keys=list(vault_keys) if vault_keys is not None else [key],
        option_values={"1": "1"},
    )


def _item(plan, key: str):
    return next(item for item in plan.items if item.control.key == key)


def _is_affirmative(value: object) -> bool:
    return str(value).strip().lower() in AFFIRMATIVE


def _assert_not_filled_from_generic_consent(item) -> None:
    assert item.status in {"skip", "review"}
    assert item.value is None or not _is_affirmative(item.value)


def test_magnet_marketing_not_filled_from_terms_consent() -> None:
    """Lever consent[marketing] must not be planned from terms_consent=Yes."""
    plan = plan_fill([MAGNET_MARKETING], _terms_only_vault())
    _assert_not_filled_from_generic_consent(_item(plan, "consent[marketing]"))


def test_terms_and_privacy_still_resolve_from_terms_consent() -> None:
    vault = _terms_only_vault()
    privacy = _checkbox(
        "privacy_statement",
        "Accept applicant privacy statement?",
        legal=True,
        vault_keys=["privacy_statement"],
    )
    terms = _checkbox(
        "terms_and_conditions",
        "Do you agree to our terms and conditions?",
        legal=True,
        vault_keys=["terms_and_conditions"],
    )
    plan = plan_fill([privacy, terms], vault)
    assert len(plan.items) == 2
    for item in plan.items:
        assert item.status == "fill"
        assert _is_affirmative(item.value)


def test_explicit_marketing_consent_still_resolves() -> None:
    """An explicit marketing_consent answer fills consent[marketing] without terms_consent."""
    vault = AnswerVault(
        [
            AnswerRecord(key="marketing_consent", value="Yes", source=AnswerSource.USER),
        ]
    )
    item = _item(plan_fill([MAGNET_MARKETING], vault), "consent[marketing]")
    assert item.status == "fill"
    assert _is_affirmative(item.value)


@pytest.mark.parametrize(
    "key, label",
    [
        ("terms_ack", "I have read and agree to the terms of use"),
        ("conditions_ack", "I agree to the terms and conditions"),
        ("privacy_ack", "I acknowledge the applicant privacy statement"),
        ("policy_ack", "I agree to the candidate privacy policy"),
        ("data_processing_ack", "I consent to data processing of my application"),
        ("gdpr_ack", "I consent under GDPR to process my applicant data"),
        ("disclaimer_ack", "Have you read and agree to the below Disclaimer and Consent?"),
        ("certify_accurate", "I certify that the information provided is true and accurate"),
    ],
)
def test_allowlisted_labels_still_resolve_from_terms_consent(key: str, label: str) -> None:
    control = _checkbox(key, label, legal=True, vault_keys=[key])
    item = _item(plan_fill([control], _terms_only_vault()), key)
    assert item.status == "fill"
    assert _is_affirmative(item.value)


@pytest.mark.parametrize(
    "key, label",
    [
        ("comms_opt_in", "I agree to receive communications from the recruiter"),
        ("newsletter_opt_in", "Sign up for the company newsletter"),
        ("sms_opt_in", "I consent to receive SMS text message updates"),
        ("email_updates", "Email me updates about the hiring process"),
        ("future_opportunities", "Contact me about future opportunities"),
        ("talent_community", "Join our talent community and stay in touch"),
        ("background_check", "I consent to a background check"),
        ("criminal_check", "I agree to a criminal check as part of screening"),
        ("identity_verification", "I acknowledge identity verification may be required"),
        ("arbitration", "I agree to binding arbitration of employment disputes"),
    ],
)
def test_non_allowlisted_consent_labels_do_not_use_generic_fallback(key: str, label: str) -> None:
    control = _checkbox(key, label, legal=True, required=True, vault_keys=[key])
    _assert_not_filled_from_generic_consent(_item(plan_fill([control], _terms_only_vault()), key))


def test_marketing_key_never_gets_fallback_even_with_privacy_wording() -> None:
    control = _checkbox(
        "consent[marketing]",
        "I agree to the privacy policy and to marketing emails",
        legal=True,
        name="consent[marketing]",
        vault_keys=["consent[marketing]"],
    )
    _assert_not_filled_from_generic_consent(
        _item(plan_fill([control], _terms_only_vault()), "consent[marketing]")
    )


def test_explicit_background_check_key_still_resolves() -> None:
    vault = AnswerVault(
        [
            AnswerRecord(key="terms_consent", value="Yes", source=AnswerSource.USER),
            AnswerRecord(key="background_check_consent", value="Yes", source=AnswerSource.USER),
        ]
    )
    control = _checkbox(
        "bg_check",
        "I consent to a background check",
        legal=True,
        required=True,
        vault_keys=["bg_check", "background_check_consent"],
    )
    item = _item(plan_fill([control], vault), "bg_check")
    assert item.status == "fill"
    assert _is_affirmative(item.value)
