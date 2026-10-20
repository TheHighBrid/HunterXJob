"""Verified-facts profile: import, verification rules, résumé parsing, form mapping."""

import zipfile

import pytest
from profile_helpers import example_document, load_example_profile, memory_db
from sqlalchemy import select

from app.answer_vault import AnswerSource, FieldRequest, ResolutionStatus
from app.form_engine import ControlType, FormControl, plan_fill
from app.greenhouse_browser import DomField, dom_control
from app.materials import JobContext, build_resume
from app.models import ProfileFact
from app.profile import (
    ProfileError,
    drafts_from_document,
    edit_fact,
    import_profile_document,
    readiness,
    set_verified,
    store_drafts,
    verified_profile,
)
from app.profile_vault import history_key, profile_answers
from app.render import render_docx_bytes, render_pdf_bytes
from app.resume_import import extract_text, normalize_date, parse_resume_text
from app.vault_store import load_vault, upsert_answer

FIXTURE_RESUME = __import__("pathlib").Path(__file__).parent / "fixtures" / "profile" / "resume_sample.txt"


def _fact(db, key):
    return db.execute(select(ProfileFact).where(ProfileFact.key == key)).scalar_one()


def test_profile_file_is_unverified_unless_the_file_says_so():
    db = memory_db()
    result = load_example_profile(db, verified=False)
    assert result.created == 36 and result.verified == 0
    assert verified_profile(db).empty
    assert readiness(verified_profile(db))

    again = load_example_profile(db, verified=True)
    assert again.unchanged == 36 and again.verified == 36
    assert readiness(verified_profile(db)) == []


def test_per_entry_verified_flag_and_nested_achievements():
    document = example_document(verified=False)
    document["employment"][0]["verified"] = True
    drafts = {draft.key: draft for draft in drafts_from_document(document, origin="t.yaml")}
    assert drafts["employment:northwind"].verified is True
    northwind_achievements = [d for d in drafts.values() if d.data.get("employment") == "employment:northwind"]
    assert len(northwind_achievements) == 4 and all(d.verified for d in northwind_achievements)
    assert drafts["employment:lakeshore"].verified is False
    assert drafts["employment:northwind"].provenance == "t.yaml#employment[0]"


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d.update(salary=1), "unknown top-level keys"),
    (lambda d: d["employment"][0].update(start="April 2022"), "YYYY or YYYY-MM"),
    (lambda d: d["contact"].update(ssn="x"), "unknown field"),
    (lambda d: d["employment"][0].update(invented="x"), "employment"),
    (lambda d: d["work_authorization"][0].update(country="Canada"), "work_authorization"),
])
def test_invalid_profile_files_are_rejected(mutate, message):
    document = example_document()
    mutate(document)
    with pytest.raises(ProfileError, match=message):
        drafts_from_document(document, origin="bad.yaml")


def test_editing_a_fact_unverifies_it_unless_reverified():
    db = memory_db()
    load_example_profile(db)
    fact = _fact(db, "employment:lakeshore")
    data = {"employer": "Lakeshore Payments Inc.", "title": "Senior Disputes Analyst", "start": "2019-06", "end": "2022-03"}
    edited = edit_fact(db, fact.id, data)
    assert edited.verified is False and edited.source == "manual"
    assert "employment:lakeshore" not in verified_profile(db).by_key()
    # Achievements of an unverified employer cannot be attributed, so they are dropped too.
    assert not verified_profile(db).achievements_for("employment:lakeshore")
    assert edit_fact(db, fact.id, data, verified=True).verified is True
    assert set_verified(db, fact.id, False).verified is False


def test_resume_text_becomes_unverified_drafts():
    drafts, warnings = parse_resume_text(FIXTURE_RESUME.read_text(encoding="utf-8"), origin="resume_sample.txt")
    assert warnings == []
    assert all(draft.verified is False for draft in drafts)
    by_category = {}
    for draft in drafts:
        by_category.setdefault(draft.category, []).append(draft.data)
    jobs = by_category["employment"]
    assert [(j["title"], j["employer"], j["start"], j["end"], j["current"]) for j in jobs] == [
        ("Senior Fraud Analyst", "Northwind Credit Union", "2022-04", None, True),
        ("Disputes Analyst", "Lakeshore Payments Inc.", "2019-06", "2022-03", False),
    ]
    assert by_category["education"] == [{"institution": "Capital City University", "degree": "Bachelor of Commerce",
                                         "field_of_study": "Finance", "location": "", "start": None, "end": "2017"}]
    assert {s["name"] for s in by_category["skill"]} >= {"AML", "KYC", "SQL"}
    assert any(a["metrics"] == ["1,800", "96%"] for a in by_category["achievement"])
    assert drafts[0].provenance.startswith("resume_sample.txt: ")


def test_resume_import_never_marks_verified_or_overwrites_verified_facts():
    db = memory_db()
    load_example_profile(db)
    drafts, _ = parse_resume_text("Avery Quinn\nlinkedin.com/in/someone-else\navery@example.org\n", origin="cv.txt")
    result = store_drafts(db, drafts, "resume_text")
    assert _fact(db, "contact:email").data_json.find("avery.quinn@example.com") > 0
    assert any("kept verified fact contact:email" in warning for warning in result.warnings)
    assert all(not row.verified for row in db.execute(select(ProfileFact).where(ProfileFact.source == "resume_text")).scalars())


@pytest.mark.parametrize("renderer, suffix, source", [(render_pdf_bytes, ".pdf", "resume_pdf"), (render_docx_bytes, ".docx", "resume_docx")])
def test_pdf_and_docx_resumes_round_trip(tmp_path, renderer, suffix, source):
    db = memory_db()
    load_example_profile(db)
    doc = build_resume(verified_profile(db), JobContext("Fraud Analyst", "Maplebank", description="AML KYC"))
    path = tmp_path / f"cv{suffix}"
    path.write_bytes(renderer(doc))
    text, detected = extract_text(path)
    assert detected == source
    drafts, _ = parse_resume_text(text, origin=path.name)
    employers = {d.data["employer"] for d in drafts if d.category == "employment"}
    assert {"Northwind Credit Union", "Lakeshore Payments Inc."} <= employers
    assert all(not d.verified for d in drafts)


def test_unsupported_or_broken_resume_files(tmp_path):
    (tmp_path / "cv.rtf").write_text("x")
    with pytest.raises(ProfileError, match="supported"):
        extract_text(tmp_path / "cv.rtf")
    (tmp_path / "cv.docx").write_bytes(b"not a zip")
    with pytest.raises(ProfileError, match="DOCX"):
        extract_text(tmp_path / "cv.docx")
    with zipfile.ZipFile(tmp_path / "empty.docx", "w") as archive:
        archive.writestr("other.xml", "<x/>")
    with pytest.raises(ProfileError, match="DOCX"):
        extract_text(tmp_path / "empty.docx")


@pytest.mark.parametrize("raw, expected", [("Mar 2021", ("2021-03", False)), ("03/2021", ("2021-03", False)),
                                           ("2019", ("2019", False)), ("Present", (None, True)), ("Sept 2020", ("2020-09", False))])
def test_normalize_date(raw, expected):
    assert normalize_date(raw) == expected


# ------------------------------------------------------------------ form mapping


@pytest.mark.parametrize("key, label, expected", [
    ("school--0", "School", "education_0_school"),
    ("degree--1", "Degree", "education_1_degree"),
    ("discipline--0", "Discipline", "education_0_discipline"),
    ("company-name-0", "Company name", "employment_0_company"),
    ("title-0", "Title", "employment_0_title"),
    ("start-date-month-0", "Start date month (employment)", "employment_0_start_month"),
    ("education-start-year-0", "Start year", "education_0_start_year"),
    ("start-year-0", "Start year", None),  # section is ambiguous -> review
    ("school", "School", None),  # no row index -> review
])
def test_history_keys(key, label, expected):
    assert history_key(key, label) == expected


def test_verified_profile_fills_contact_history_and_country_scoped_authorization():
    db = memory_db()
    load_example_profile(db)
    answers = profile_answers(verified_profile(db))
    assert answers["first_name"] == "Avery" and answers["current_company"] == "Northwind Credit Union"
    assert answers["employment_0_company"] == "Northwind Credit Union"
    assert answers["employment_0_current"] is True
    assert answers["employment_1_end_month"] == "March" and answers["employment_1_end_year"] == "2022"
    assert answers["education_0_school"] == "Capital City University"
    assert answers["work_authorization_CA"] == "Yes" and answers["sponsorship_US"] == "Yes"
    assert "work_authorization_FR" not in answers  # never assumed for other countries

    vault = load_vault(db)
    assert vault.resolve(FieldRequest(key="work_authorization_ca", sensitive=True)).status is ResolutionStatus.RESOLVED
    # An explicit stored answer wins over the profile.
    upsert_answer(db, key="first_name", value="Ave")
    assert load_vault(db).resolve(FieldRequest(key="first_name")).value == "Ave"


def test_verified_profile_loads_language_proficiency_for_classifiers():
    db = memory_db()
    load_example_profile(db)
    answers = profile_answers(verified_profile(db))
    assert answers["language_proficiency"] == "English (Native)|French (Professional working)"
    vault = load_vault(db)
    resolved = vault.resolve(FieldRequest(key="language_proficiency"))
    assert resolved.status is ResolutionStatus.RESOLVED
    assert resolved.value == "English (Native)|French (Professional working)"


def test_availability_date_policy_falls_back_to_start_date():
    db = memory_db()
    upsert_answer(db, key="availability_date", value="Two weeks", source="policy")
    vault = load_vault(db, profile=False)
    assert vault.resolve(FieldRequest(key="availability_date")).value == "Two weeks"
    assert vault.resolve(FieldRequest(key="start_date")).value == "Two weeks"
    # Explicit start_date wins over the availability fallback.
    upsert_answer(db, key="start_date", value="2026-11-01", source="user")
    vault = load_vault(db, profile=False)
    assert vault.resolve(FieldRequest(key="start_date")).value == "2026-11-01"


def test_background_check_consent_loads_only_from_explicit_policy():
    db = memory_db()
    load_example_profile(db)
    vault = load_vault(db)
    assert vault.resolve(FieldRequest(key="background_check_consent", sensitive=True)).status is ResolutionStatus.MISSING
    upsert_answer(db, key="background_check_consent", value="Yes", source="policy", sensitive=True)
    vault = load_vault(db)
    resolved = vault.resolve(FieldRequest(key="background_check_consent", sensitive=True))
    assert resolved.status is ResolutionStatus.RESOLVED
    assert resolved.value == "Yes"
    assert resolved.source is AnswerSource.POLICY


def test_unverified_profile_facts_never_reach_forms():
    db = memory_db()
    load_example_profile(db, verified=False)
    assert profile_answers(verified_profile(db)) == {}
    control = dom_control(DomField(tag="input", type="text", id="company-name-0", name="", role="", label="Company name",
                                   required=True))
    plan = plan_fill([control], load_vault(db))
    assert plan.items[0].status == "review"


def test_dom_history_field_is_filled_from_the_verified_profile():
    db = memory_db()
    load_example_profile(db)
    company = dom_control(DomField(tag="input", type="text", id="company-name-0", name="", role="", label="Company name",
                                   required=True))
    assert company.vault_keys == ["company-name-0", "employment_0_company"]
    title = FormControl(key="title-1", label="Title", control_type=ControlType.TEXT, required=True, confidence=0.9,
                        vault_keys=["title-1", "employment_1_title"])
    plan = plan_fill([company, title], load_vault(db))
    assert [(item.status, item.value) for item in plan.items] == [
        ("fill", "Northwind Credit Union"), ("fill", "Disputes Analyst")]


def test_import_profile_document_rejects_non_mapping():
    with pytest.raises(ProfileError):
        import_profile_document(memory_db(), ["not", "a", "mapping"], origin="x")
