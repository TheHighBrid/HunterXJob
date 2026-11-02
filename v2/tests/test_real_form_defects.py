"""Regression tests for three defects from the Oct 6 real-posting dry-run re-run."""
from __future__ import annotations

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.form_engine import ControlType, FormControl, plan_fill
from app.greenhouse_form import classify_question
from app.option_derivation import iso_date

LANGS = "English (Native)|French (Professional working)"


def _vault(answers: dict[str, str]) -> AnswerVault:
    return AnswerVault([AnswerRecord(key, value, AnswerSource.USER) for key, value in answers.items()])


def _control(label: str, control_type: ControlType, key: str, options: list[str] | None = None) -> FormControl:
    return FormControl(key="question_1", label=label, control_type=control_type, required=True,
                       options=options or [], confidence=0.9, vault_keys=[key])


def _only(plan):
    assert len(plan.items) == 1
    return plan.items[0]


# Defect 1: start-date answers went into date-picker fields.
def test_relative_start_date_in_a_date_picker_goes_to_review():
    control = _control("Earliest start date", ControlType.DATE, "start_date")
    plan = plan_fill([control], _vault({"start_date": "2 weeks notice"}))
    assert _only(plan).status == "review"
    assert "ambiguous_question" in plan.blockers


def test_concrete_start_date_in_a_date_picker_is_written_as_iso():
    control = _control("Earliest start date", ControlType.DATE, "start_date")
    item = _only(plan_fill([control], _vault({"start_date": "January 4, 2027"})))
    assert (item.status, item.value) == ("fill", "2027-01-04")


def test_iso_date_rejects_ambiguous_formats():
    assert iso_date("2027-01-04") == "2027-01-04"
    assert iso_date("04/01/2027") is None
    assert iso_date("Immediately") is None


def test_relative_start_date_still_fills_a_text_field():
    control = _control("When can you start?", ControlType.TEXT, "start_date")
    item = _only(plan_fill([control], _vault({"start_date": "2 weeks notice"})))
    assert (item.status, item.value) == ("fill", "2 weeks notice")


# Defect 2: an optional referrer-name field was matched as how-did-you-hear.
def test_referrer_name_field_is_not_how_did_you_hear():
    for label in ("Referral: employee's name", "Who referred you?", "Name of the employee who referred you",
                  "How did you hear about us? If referred, referrer's name"):
        assert classify_question(label) is None, label
    assert classify_question("How did you hear about us?") == "referral_source"
    assert classify_question("[Optional] Preferred Full Name (The name you'd like to be referred to as)") == "preferred_name"


# Defect 3: a single-language fluency question got the whole pipe-joined list.
def test_single_language_fluency_text_field_gets_plain_yes():
    control = _control("Are you fluent in French?", ControlType.TEXT, "language_proficiency")
    item = _only(plan_fill([control], _vault({"language_proficiency": LANGS})))
    assert (item.status, item.value) == ("fill", "Yes")


def test_single_language_fluency_radio_picks_yes():
    control = _control("Are you fluent in French?", ControlType.RADIO, "language_proficiency", ["Yes", "No"])
    item = _only(plan_fill([control], _vault({"language_proficiency": LANGS})))
    assert (item.status, item.value) == ("fill", "Yes")


def test_single_language_below_professional_goes_to_review():
    control = _control("Are you fluent in French?", ControlType.TEXT, "language_proficiency")
    vault = _vault({"language_proficiency": "English (Native)|French (Elementary)"})
    assert _only(plan_fill([control], vault)).status == "review"


def test_single_language_not_in_stored_list_goes_to_review():
    control = _control("Do you speak Spanish fluently?", ControlType.TEXT, "language_proficiency")
    assert _only(plan_fill([control], _vault({"language_proficiency": LANGS}))).status == "review"
