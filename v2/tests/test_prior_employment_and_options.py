"""Prior-employment answers from verified history, and deterministic option derivation."""
from __future__ import annotations

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.form_engine import ControlType, FormControl, plan_fill
from app.option_derivation import derive_option, parse_languages
from app.prior_employment import employer_in_label, looks_like, tag_prior_employer

HISTORY = {"employment_0_company": "Northwind Credit Union", "employment_1_company": "Lakeshore Payments Inc."}
LANGS = "English (Native)|French (Professional working)"


def _vault(answers: dict[str, str]) -> AnswerVault:
    return AnswerVault([AnswerRecord(key, value, AnswerSource.USER) for key, value in answers.items()])


def _prior(board: str, label: str, options: list[str], *, control_type=ControlType.SELECT, key="question_1"):
    control = FormControl(key=key, label=label, control_type=control_type, required=True, options=options, confidence=0.9)
    tag_prior_employer([control], board)
    return control


def _only(plan):
    assert len(plan.items) == 1
    return plan.items[0]


def test_known_employer_goes_to_review():
    control = _prior("lakeshore", "Have you previously worked for Lakeshore?", ["Yes", "No"])
    assert control.employer == "lakeshore"
    plan = plan_fill([control], _vault(HISTORY))
    item = _only(plan)
    assert item.status == "review"
    assert "Lakeshore Payments" in item.reason
    assert "ambiguous_question" in plan.blockers


def test_lookalike_employer_goes_to_review():
    control = _prior("northwind", "Have you ever been previously employed by Northwind?",
                     ["No", "Yes - Intern", "Yes - Full Time Employment"])
    item = _only(plan_fill([control], _vault(HISTORY)))
    assert item.status == "review"
    assert "possible match" in item.reason


def test_lookalike_similarity_and_suffixes():
    assert looks_like("northwnd", "Northwind Credit Union")
    assert looks_like("lakeshore payments", "Lakeshore Payments Inc.")
    assert looks_like("acme", "ACME Corp")
    assert not looks_like("gitlab", "Northwind Credit Union")


def test_empty_history_goes_to_review():
    control = _prior("gitlab", "Have you previously been employed by GitLab?", ["Yes", "No"])
    plan = plan_fill([control], _vault({}))
    item = _only(plan)
    assert item.status == "review"
    assert "no verified employment history" in item.reason


def test_absent_employer_fills_the_single_no_option():
    control = _prior("gitlab", "Have you previously been employed by GitLab?", ["Yes", "No"])
    item = _only(plan_fill([control], _vault(HISTORY)))
    assert (item.status, item.value) == ("fill", "No")


def test_absent_employer_fills_no_on_a_multiselect():
    options = ["No, I have not worked for D2L in any capacity", "Yes, as an employee", "Yes, as a contractor"]
    control = _prior("d2l", "Have you previously worked for D2L in any capacity?", options,
                     control_type=ControlType.MULTISELECT)
    item = _only(plan_fill([control], _vault(HISTORY)))
    assert (item.status, item.value) == ("fill", [options[0]])


def test_ambiguous_no_options_go_to_review():
    control = _prior("gitlab", "Have you previously been employed by GitLab?", ["No", "No - but applied before", "Yes"])
    assert _only(plan_fill([control], _vault(HISTORY))).status == "review"


def test_label_that_does_not_name_the_board_is_not_tagged():
    label = "Have you previously been employed by the US government?"
    control = _prior("asana", label, ["Yes", "No"])
    assert control.employer == ""
    assert employer_in_label("asana", label) == ""
    assert _only(plan_fill([control], _vault(HISTORY))).status == "review"


def test_explicit_answer_for_the_question_wins_over_history_rule():
    control = _prior("northwind", "Have you ever been previously employed by Northwind?", ["No", "Yes - Intern"])
    item = _only(plan_fill([control], _vault({**HISTORY, "question_1": "Yes - Intern"})))
    assert (item.status, item.value) == ("fill", "Yes - Intern")


def test_sensitive_or_legal_controls_never_use_the_history_rule():
    control = _prior("gitlab", "Have you previously been employed by GitLab?", ["Yes", "No"])
    control.legal = True
    assert _only(plan_fill([control], _vault(HISTORY))).status == "review"


def _lang_control(label: str, options: list[str], control_type=ControlType.SELECT):
    return FormControl(key="question_9", label=label, control_type=control_type, required=True, options=options,
                       confidence=0.9, vault_keys=["language_proficiency"])


def test_parse_languages_maps_to_cefr():
    assert parse_languages(LANGS) == {"english": "C2", "french": "B2"}


def test_bilingual_yes_when_both_languages_professional():
    control = _lang_control("Are you fluent in BOTH French AND English?",
                            ["YES, I am fluent in both.", "NO, I am not bilingual."])
    item = _only(plan_fill([control], _vault({"language_proficiency": LANGS})))
    assert (item.status, item.value) == ("fill", "YES, I am fluent in both.")


def test_bilingual_below_professional_goes_to_review():
    control = _lang_control("Are you fluent in BOTH French AND English?",
                            ["YES, I am fluent in both.", "NO, I am not bilingual."])
    vault = _vault({"language_proficiency": "English (Native)|French (Elementary)"})
    assert _only(plan_fill([control], vault)).status == "review"


def test_cefr_scale_picks_the_weaker_language():
    options = ["A1: No proficiency", "A2: Elementary", "B1: Intermediate", "B2: Upper intermediate",
               "C1: Advanced", "C2: Bilingual/Native speaker"]
    control = _lang_control("Rate your English and French proficiency", options, ControlType.MULTISELECT)
    item = _only(plan_fill([control], _vault({"language_proficiency": LANGS})))
    assert (item.status, item.value) == ("fill", ["B2: Upper intermediate"])


def test_non_bilingual_language_question_is_not_derived():
    control = _lang_control("Do you speak Spanish?", ["Yes", "No"])
    assert derive_option(control, LANGS, "language_proficiency") is None


def test_yes_no_prefix_matching():
    control = FormControl(key="q", label="Are you open to hybrid work?", control_type=ControlType.SELECT,
                          options=["Yes, I am open to it", "No, remote only"])
    assert derive_option(control, "Yes") == "Yes, I am open to it"
    assert derive_option(control, "no") == "No, remote only"
    assert derive_option(control, "Maybe") is None
    two_yes = FormControl(key="q", label="x", control_type=ControlType.SELECT, options=["Yes - A", "Yes - B", "No"])
    assert derive_option(two_yes, "Yes") is None
