"""Regression tests for three defects from the Oct 6 real-posting dry-run re-run."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime

from conftest import load_ats_fixture, load_ats_json, load_fixture_json

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.ashby_form import AshbyJobRef, parse_ashby_payload
from app.form_engine import ControlType, FormControl, plan_fill, verify_filled
from app.greenhouse_form import GreenhouseJobRef, classify_question, parse_greenhouse_payload
from app.lever_form import LeverJobRef, parse_lever_apply_page
from app.option_derivation import iso_date
from app.smartrecruiters_form import SmartRecruitersJobRef, parse_smartrecruiters_form
from app.workday_form import WorkdayJobRef, parse_workday_form

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


# --- Expanded Wave 1 verification: date pickers, referrer names, fluency & real DOM fixtures ---

def test_iso_date_and_verify_filled_for_date_controls():
    assert iso_date("January 4th, 2027") == "2027-01-04"
    assert iso_date("2027/01/04") == "2027-01-04"
    assert iso_date(date(2027, 1, 4)) == "2027-01-04"
    assert iso_date(datetime(2027, 1, 4, 9, 30, tzinfo=UTC)) == "2027-01-04"
    assert iso_date(True) is None

    date_ctrl = _control("Earliest start date", ControlType.DATE, "start_date")
    assert verify_filled(date_ctrl, "January 4, 2027", "2027-01-04") is True
    assert verify_filled(date_ctrl, "2027-01-04", "Jan 4, 2027") is True
    assert verify_filled(date_ctrl, "2027-01-04", "2027-01-05") is False
    assert verify_filled(date_ctrl, "2027-01-04", "") is False


def test_greenhouse_smartrecruiters_workday_lever_and_ashby_date_controls():
    # Greenhouse API input_date/date mapping
    payload = load_fixture_json("d2l_7696196.json")
    payload["questions"].append({
        "label": "Earliest start date",
        "required": True,
        "fields": [{"name": "question_date_1", "type": "input_date", "values": []}],
    })
    gh_form = parse_greenhouse_payload(payload, GreenhouseJobRef("d2l", "7696196"))
    gh_date = next(c for c in gh_form.controls if c.key == "question_date_1")
    assert gh_date.control_type is ControlType.DATE
    assert "start_date" in gh_date.vault_keys
    assert _only(plan_fill([gh_date], _vault({"start_date": "2 weeks notice"}))).status == "review"
    assert _only(plan_fill([gh_date], _vault({"start_date": "January 4, 2027"}))).value == "2027-01-04"

    # SmartRecruiters <input type="date">
    sr_html = '<form><label for="sd">Earliest start date</label><input type="date" id="sd" name="startDate" required /></form>'
    sr_form = parse_smartrecruiters_form(sr_html, SmartRecruitersJobRef("acme", "123"))
    sr_date = sr_form.controls[0]
    assert sr_date.control_type is ControlType.DATE
    assert _only(plan_fill([sr_date], _vault({"start_date": "2 weeks notice"}))).status == "review"
    assert _only(plan_fill([sr_date], _vault({"start_date": "January 4, 2027"}))).value == "2027-01-04"

    # Workday <input type="date">
    wd_html = '<form><label for="wd_sd">Earliest start date</label><input type="date" id="wd_sd" data-automation-id="startDate" required /></form>'
    wd_form = parse_workday_form(wd_html, WorkdayJobRef("acme", "acme", "job1"))
    wd_date = wd_form.controls[0]
    assert wd_date.control_type is ControlType.DATE
    assert _only(plan_fill([wd_date], _vault({"start_date": "2 weeks notice"}))).status == "review"
    assert _only(plan_fill([wd_date], _vault({"start_date": "January 4, 2027"}))).value == "2027-01-04"

    # Lever date card
    lever_tpl = json.dumps({"text": "Start", "fields": [{"id": "f0", "text": "Earliest start date", "type": "date", "required": True}]})
    lever_html = f'<form id="application-form"><input type="hidden" name="cards[00000000-0000-0000-0000-000000000001][baseTemplate]" value=\'{lever_tpl}\' /></form>'
    lever_form = parse_lever_apply_page(lever_html, LeverJobRef("acme", "00000000-0000-0000-0000-000000000002"))
    assert lever_form.controls[0].control_type is ControlType.DATE
    assert _only(plan_fill(lever_form.controls, _vault({"start_date": "2 weeks notice"}))).status == "review"

    # Ashby Date vs real String notice-period fixture
    eng_data = load_ats_json("ashby_form_engineer.json")["data"]
    eng_form = parse_ashby_payload(eng_data, AshbyJobRef("northwind", "831c138d-1ab7-5afa-b938-dd5da8882214"))
    notice = next(c for c in eng_form.controls if "Notice" in c.label)
    assert notice.control_type is ControlType.TEXT
    assert _only(plan_fill([notice], _vault({"start_date": "2 weeks notice"}))).value == "2 weeks notice"

    eng_data["jobPosting"]["applicationForm"]["sections"][0]["fieldEntries"].append({
        "id": "d1",
        "isRequired": True,
        "field": {"path": "custom_start_date", "title": "Earliest start date", "type": "Date", "isDeactivated": False},
    })
    ashby_with_date = parse_ashby_payload(eng_data, AshbyJobRef("northwind", "831c138d-1ab7-5afa-b938-dd5da8882214"))
    ashby_date = next(c for c in ashby_with_date.controls if c.key == "custom_start_date")
    assert ashby_date.control_type is ControlType.DATE
    assert _only(plan_fill([ashby_date], _vault({"start_date": "2 weeks notice"}))).status == "review"


def test_referrer_name_never_resolves_referral_source_in_plan_fill_or_real_fixtures():
    for label in (
        "How did you hear about us? If someone referred you, please list their name",
        "How did you hear about us? If referred, referrer's name",
        "If someone from D2L referred you to this role, please help us thank them by listing their name here!",
        "Has someone at Northwind referred you for this role? If so, please include their name here:",
    ):
        assert classify_question(label) is None, label
        ctrl = FormControl(key="question_ref_name", label=label, control_type=ControlType.TEXT, required=False, confidence=0.9)
        item = _only(plan_fill([ctrl], _vault({"referral_source": "LinkedIn"})))
        assert item.status == "skip", label

    # Real D2L fixture: source dropdown maps to referral_source; referrer name does not.
    d2l = parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), GreenhouseJobRef("d2l", "7696196"))
    d2l_by_key = {c.key: c for c in d2l.controls}
    assert "referral_source" in d2l_by_key["question_64283535"].vault_keys
    assert d2l_by_key["question_64283537"].vault_keys == ["question_64283537"]

    # Real Asana fixture: "name you would like to be referred to as" still maps to preferred_name.
    asana = parse_greenhouse_payload(load_fixture_json("asana_8165477.json"), GreenhouseJobRef("asana", "8165477"))
    asana_by_key = {c.key: c for c in asana.controls}
    assert asana_by_key["question_68886291"].vault_keys[-1] == "preferred_name"


def test_single_language_classification_and_real_bilingual_fixtures_have_no_regressions():
    assert classify_question("Do you speak French fluently?") == "language_proficiency"
    assert classify_question("Are you proficient in French?") == "language_proficiency"

    # Real D2L bilingual CEFR multiselect
    d2l = parse_greenhouse_payload(load_fixture_json("d2l_7696196.json"), GreenhouseJobRef("d2l", "7696196"))
    d2l_lang = next(c for c in d2l.controls if c.key == "question_64283526")
    d2l_item = _only(plan_fill([d2l_lang], _vault({"language_proficiency": LANGS})))
    assert (d2l_item.status, d2l_item.value) == ("fill", ["B2: Professional working proficiency"])

    # Real Ashby bilingual Yes/No select
    advisor = parse_ashby_payload(
        load_ats_json("ashby_form_advisor.json")["data"],
        AshbyJobRef("northwind", "9b99caed-e385-574f-94f7-4be75f67782d"),
    )
    ashby_lang = next(c for c in advisor.controls if "BOTH French AND English" in c.label)
    ashby_item = _only(plan_fill([ashby_lang], _vault({"language_proficiency": LANGS})))
    assert (ashby_item.status, ashby_item.value) == ("fill", "YES, I am fluent in both French and English.")

    # Real Lever fixture still parses cleanly with zero regressions
    lever = parse_lever_apply_page(
        load_ats_fixture("lever_apply.html"),
        LeverJobRef("northwind", "ed663b5f-1b13-5fb7-853b-e4cdf805c6dd"),
    )
    assert len(lever.controls) > 20

    # Real GitLab LLM fluency question must never match spoken language_proficiency
    gitlab = parse_greenhouse_payload(load_fixture_json("gitlab_8556658002.json"), GreenhouseJobRef("gitlab", "8556658002"))
    llm_ctrl = next(c for c in gitlab.controls if c.key == "question_36667495002")
    assert "language_proficiency" not in llm_ctrl.vault_keys
