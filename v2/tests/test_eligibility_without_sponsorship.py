"""Regression: 'eligible to work without sponsorship' is work authorization, not sponsorship.

Found on the Fullscript (Lever) dry-run: the question
\"Are you legally eligible to work full time in Canada without sponsorship?\"
was classified as sponsorship_ca and planned as
\"No, I'm not legally eligible to work in Canada\". A Canadian citizen who is
authorized to work without sponsorship must be answered Yes.
"""
from __future__ import annotations

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.form_engine import ControlType, FormControl, plan_fill
from app.greenhouse_form import classify_question, shared_answer_key

# Exact Fullscript (Lever) label and radio options from the dry-run batch.
FULLSCRIPT_LABEL = "Are you legally eligible to work full time in Canada without sponsorship?"
FULLSCRIPT_YES = "Yes, I'm legally eligible to work in Canada without the need for sponsorship"
FULLSCRIPT_NO = "No, I'm not legally eligible to work in Canada"

ELIGIBILITY_CA = (
    FULLSCRIPT_LABEL,
    "Are you authorized to work in Canada without requiring sponsorship?",
    "Are you legally authorized to work in Canada without the need for visa sponsorship?",
    "Are you legally eligible to work in Canada without visa sponsorship?",
)

SPONSORSHIP_CA = (
    "Will you now or in the future require sponsorship to work in Canada?",
    "Do you require visa sponsorship to work in Canada?",
    "Will you now or in the future require visa sponsorship for employment in Canada?",
)


def _vault() -> AnswerVault:
    # Both answers are present, as they are for a Canadian citizen who does not
    # need sponsorship. Misclassifying the Fullscript question as sponsorship_ca
    # would select the No eligibility option.
    return AnswerVault([
        AnswerRecord("work_authorization_ca", "Yes", AnswerSource.USER),
        AnswerRecord("sponsorship_ca", "No", AnswerSource.USER),
    ])


def _control(label: str, options: list[str], key: str = "cards[fullscript][field0]") -> FormControl:
    canonical = shared_answer_key(label)
    return FormControl(
        key=key,
        label=label,
        control_type=ControlType.RADIO,
        required=True,
        options=options,
        confidence=0.9,
        sensitive=True,
        vault_keys=[key, canonical] if canonical else [key],
    )


def test_fullscript_label_classifies_as_work_authorization_ca():
    assert classify_question(FULLSCRIPT_LABEL) == "work_authorization_ca"
    assert shared_answer_key(FULLSCRIPT_LABEL) == "work_authorization_ca"


def test_eligibility_without_sponsorship_variants_are_work_authorization():
    for label in ELIGIBILITY_CA:
        assert classify_question(label) == "work_authorization_ca", label
        assert shared_answer_key(label) == "work_authorization_ca", label
    assert classify_question(
        "Are you eligible to work in the United States without sponsorship?"
    ) == "work_authorization_us"


def test_require_sponsorship_questions_still_map_to_sponsorship_ca():
    for label in SPONSORSHIP_CA:
        assert classify_question(label) == "sponsorship_ca", label
        assert shared_answer_key(label) == "sponsorship_ca", label
    # Unscoped sponsorship stays unanswered rather than being reused across countries.
    assert classify_question(
        "Will you now or in the future require visa sponsorship?",
        job_location="Remote",
    ) is None


def test_fullscript_plan_fills_yes_never_sponsorship_no():
    control = _control(FULLSCRIPT_LABEL, [FULLSCRIPT_YES, FULLSCRIPT_NO])
    plan = plan_fill([control], _vault())
    item = plan.items[0]
    assert item.status == "fill"
    assert item.value == FULLSCRIPT_YES
    assert item.value != FULLSCRIPT_NO
    assert "work_authorization_ca" in control.vault_keys
    assert "sponsorship_ca" not in control.vault_keys


def test_genuine_sponsorship_question_still_answers_no():
    label = "Will you now or in the future require sponsorship to work in Canada?"
    yes = "Yes, I require sponsorship now or in the future"
    no = "No, I do not require sponsorship"
    control = _control(label, [yes, no], key="cards[sponsorship][field0]")
    plan = plan_fill([control], _vault())
    item = plan.items[0]
    assert item.status == "fill"
    assert item.value == no
    assert "sponsorship_ca" in control.vault_keys
    assert "work_authorization_ca" not in control.vault_keys
