"""Regression tests for D2L application questions and deterministic option derivation.

Verifies the four real-world questions from candidate application screenshots:
1. Work location: Kitchener Hybrid vs Remote within Canada -> 'Remote within Canada'
2. Disclaimer & consent: Terms, disclaimer & applicant declaration -> 'Yes'
3. Desired salary: $65,000 / $60,000 expectation -> '60-70K'
4. Time zone: Eastern Time / America/Toronto -> 'GMT-5 :Eastern Standard Time (EST)'
"""
from __future__ import annotations

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.form_engine import ControlType, FormControl, plan_fill
from app.greenhouse_form import classify_question, shared_answer_key
from app.option_derivation import derive_option


def _test_vault() -> AnswerVault:
    records = [
        AnswerRecord("work_location_preference", "Remote within Canada", AnswerSource.USER),
        AnswerRecord("work_arrangement_nonlocal", "Remote", AnswerSource.POLICY),
        AnswerRecord("terms_consent", "Yes", AnswerSource.POLICY),
        AnswerRecord("disclaimer_and_consent", "Yes", AnswerSource.POLICY),
        AnswerRecord("agreement_consent", "Yes", AnswerSource.USER),
        AnswerRecord("salary_expectation", "65,000", AnswerSource.USER),
        AnswerRecord("minimum_acceptable_salary", "60,000", AnswerSource.POLICY),
        AnswerRecord("time_zone", "Eastern Time", AnswerSource.POLICY),
    ]
    return AnswerVault(records)


def test_d2l_question_classification_and_shared_keys():
    # 1. Work location
    lbl_loc = "This role can be either hybrid from D2L's Kitchener office, or remote from within Canada. Please indicate which work location would work for you."
    assert classify_question(lbl_loc) == "work_location_preference"
    assert shared_answer_key(lbl_loc) == "work_location_preference"

    # 2. Disclaimer and consent
    lbl_disc = "Have you read and agree to the below Disclaimer and Consent?"
    assert classify_question(lbl_disc) == "terms_consent"
    assert shared_answer_key(lbl_disc) == "terms_consent"

    # 3. Desired salary
    lbl_sal = "Please indicate your desired salary range"
    assert classify_question(lbl_sal) == "salary_expectation"
    assert shared_answer_key(lbl_sal) == "salary_expectation"

    # 4. Time zone
    lbl_tz = "Please select your time zone"
    assert classify_question(lbl_tz) == "time_zone"
    assert shared_answer_key(lbl_tz) == "time_zone"


def test_d2l_option_derivation():
    # 1. Work location
    c_loc = FormControl(
        key="question_70430100",
        label="This role can be either hybrid from D2L's Kitchener office, or remote from within Canada. Please indicate which work location would work for you.",
        control_type=ControlType.SELECT,
        options=["Hybrid from Kitchener office", "Remote within Canada", "Neither of these options work for me"],
        vault_keys=["work_location_preference"],
    )
    assert derive_option(c_loc, "Remote Canada", "work_location_preference") == "Remote within Canada"
    assert derive_option(c_loc, "Remote", "work_location_preference") == "Remote within Canada"

    # 2. Disclaimer and consent
    c_disc = FormControl(
        key="question_70406767",
        label="Have you read and agree to the below Disclaimer and Consent?",
        control_type=ControlType.SELECT,
        options=["Yes"],
        legal=True,
        vault_keys=["terms_consent"],
    )
    assert derive_option(c_disc, "Yes", "terms_consent") == "Yes"

    # 3. Desired salary
    c_sal = FormControl(
        key="question_70406769",
        label="Please indicate your desired salary range",
        control_type=ControlType.SELECT,
        options=["30-40K", "40-50K", "50-60K", "60-70K", "70-80K", "80-90K", "90K-100K", "+100K"],
        vault_keys=["salary_expectation"],
    )
    assert derive_option(c_sal, "65,000", "salary_expectation") == "60-70K"
    assert derive_option(c_sal, "60000", "salary_expectation") == "60-70K"
    assert derive_option(c_sal, 65000, "salary_expectation") == "60-70K"

    # 4. Time zone
    c_tz = FormControl(
        key="question_70406770",
        label="Please select your time zone",
        control_type=ControlType.SELECT,
        options=[
            "GMT+2 :Eastern European Time (EET)",
            "GMT-10 :Hawaii Standard Time (HST)",
            "GMT-8 :Pacific Standard Time (PST)",
            "GMT-7 :Mountain Standard Time (MST)",
            "GMT-6 :Central Standard Time (CST)",
            "GMT-5 :Eastern Standard Time (EST)",
            "GMT-4 :Atlantic Standard Time (AST)",
        ],
        vault_keys=["time_zone"],
    )
    assert derive_option(c_tz, "Eastern Time", "time_zone") == "GMT-5 :Eastern Standard Time (EST)"
    assert derive_option(c_tz, "America/Toronto", "time_zone") == "GMT-5 :Eastern Standard Time (EST)"
    assert derive_option(c_tz, "EST", "time_zone") == "GMT-5 :Eastern Standard Time (EST)"


def test_d2l_plan_fill_resolves_all_four_controls_without_blockers():
    controls = [
        FormControl(
            key="question_70430100",
            label="This role can be either hybrid from D2L's Kitchener office, or remote from within Canada. Please indicate which work location would work for you.",
            control_type=ControlType.SELECT,
            required=True,
            options=["Hybrid from Kitchener office", "Remote within Canada", "Neither of these options work for me"],
            vault_keys=["work_location_preference"],
            confidence=0.9,
        ),
        FormControl(
            key="question_70406767",
            label="Have you read and agree to the below Disclaimer and Consent?",
            control_type=ControlType.SELECT,
            required=True,
            options=["Yes"],
            legal=True,
            vault_keys=["terms_consent"],
            confidence=0.9,
        ),
        FormControl(
            key="question_70406769",
            label="Please indicate your desired salary range",
            control_type=ControlType.SELECT,
            required=True,
            options=["30-40K", "40-50K", "50-60K", "60-70K", "70-80K", "80-90K", "90K-100K", "+100K"],
            vault_keys=["salary_expectation"],
            confidence=0.9,
        ),
        FormControl(
            key="question_70406770",
            label="Please select your time zone",
            control_type=ControlType.SELECT,
            required=True,
            options=[
                "GMT+2 :Eastern European Time (EET)",
                "GMT-8 :Pacific Standard Time (PST)",
                "GMT-6 :Central Standard Time (CST)",
                "GMT-5 :Eastern Standard Time (EST)",
                "GMT-4 :Atlantic Standard Time (AST)",
            ],
            vault_keys=["time_zone"],
            confidence=0.9,
        ),
    ]

    vault = _test_vault()
    plan = plan_fill(controls, vault)

    assert plan.blockers == []
    assert plan.ready is True

    items_by_key = {item.control.key: item for item in plan.items}
    assert items_by_key["question_70430100"].status == "fill"
    assert items_by_key["question_70430100"].value == "Remote within Canada"

    assert items_by_key["question_70406767"].status == "fill"
    assert items_by_key["question_70406767"].value == "Yes"

    assert items_by_key["question_70406769"].status == "fill"
    assert items_by_key["question_70406769"].value == "60-70K"

    assert items_by_key["question_70406770"].status == "fill"
    assert items_by_key["question_70406770"].value == "GMT-5 :Eastern Standard Time (EST)"
