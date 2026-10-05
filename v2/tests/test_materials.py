"""Tailored materials: selection, truthfulness guard, LLM fallback, rendering, approval gating."""

import copy
import io
import json
import zipfile

import pytest
from profile_helpers import example_document, load_example_profile, memory_db
from sqlalchemy import select

from app.config import Settings
from app.material_llm import llm_client, reword_cover_letter, reword_resume
from app.material_store import MaterialsError, manifest, materials_ready, vault_records
from app.material_workflow import approve_material, generate_for_application, reject_material
from app.materials import (
    JobContext,
    build_cover_letter,
    build_resume,
    cover_letter_text,
    is_finance_employment,
    is_finance_target,
    resume_text,
)
from app.models import Application, ApplicationMaterial, Job, PipelineStage, ProfileFact, ReviewTask, SubmissionEvidence
from app.pipeline import approve_application, execute_apply
from app.profile import Fact, import_profile_document, set_verified, verified_profile
from app.render import render_docx_bytes, render_pdf_bytes
from app.resume_import import extract_text
from app.truth_guard import check_cover_letter, check_resume

JOB = JobContext(
    title="Bilingual Fraud Analyst", company="Maplebank", location="Ottawa",
    description="Investigate card fraud alerts, apply AML and KYC controls, support disputes and chargebacks, "
                "write SQL. Nice to have: Kubernetes, Actimize, an MBA.",
)


@pytest.fixture
def profile():
    db = memory_db()
    load_example_profile(db)
    return verified_profile(db)


def _resume(profile):
    return build_resume(profile, JOB)


# --------------------------------------------------------------------- selection


def test_selection_uses_only_verified_facts_ordered_by_relevance(profile):
    doc = _resume(profile)
    skills = [item["text"] for item in doc["skills"]]
    assert skills[:4] == ["AML", "KYC", "SQL", "Chargebacks"]  # named in the posting first
    assert "Kubernetes" not in skills and "Actimize" not in skills
    northwind = doc["experience"][0]
    assert northwind["employer"] == "Northwind Credit Union" and northwind["dates"] == "Apr 2022 \u2013 Present"
    assert northwind["bullets"][0]["text"].startswith("Cut false-positive fraud alerts by 32%")
    assert [entry["employer"] for entry in doc["experience"]] == [
        "Northwind Credit Union", "Lakeshore Payments Inc.", "Riverbend Savings Bank"]  # reverse-chronological
    assert all(len(entry["bullets"]) <= 4 for entry in doc["experience"])
    assert check_resume(doc, profile) == []


def test_unverified_facts_are_never_selected():
    db = memory_db()
    load_example_profile(db)
    fact = db.execute(select(ProfileFact).where(ProfileFact.data_json.contains("1,800 chargeback"))).scalar_one()
    set_verified(db, fact.id, False)
    skill = db.execute(select(ProfileFact).where(ProfileFact.key == "skill:sql")).scalar_one()
    set_verified(db, skill.id, False)
    text = resume_text(build_resume(verified_profile(db), JOB))
    assert "1,800" not in text
    assert "SQL" not in text.split("EXPERIENCE")[0]  # not in the skills list any more


def test_irrelevant_projects_are_left_out(profile):
    unrelated = JobContext("Barista", "Cafe", description="espresso latte art")
    assert _resume(profile)["projects"] and not build_resume(profile, unrelated)["projects"]


# ------------------------------------------------- finance-first experience order

# A made-up current, non-finance role, more recent than every finance role.
FOUNDER = {
    "id": "harbourlane", "employer": "Harbour Lane Studio", "title": "Founder", "start": "2024-01", "current": True,
    "achievements": ["Founded Harbour Lane Studio, a print shop, and run it as a self-employed business."],
}
NON_FINANCE_JOB = JobContext(
    title="Brand Marketing Coordinator", company="Cedar Goods",
    description="Plan product launches, present campaign results and run the print shop calendar.",
)


@pytest.fixture
def founder_profile():
    db = memory_db()
    document = example_document()
    document["employment"].insert(0, copy.deepcopy(FOUNDER))
    import_profile_document(db, document, origin="profile.example.yaml")
    return verified_profile(db)


def _employers(doc):
    return [entry["employer"] for entry in doc["experience"]]


def test_finance_target_puts_finance_roles_above_a_current_non_finance_role(founder_profile):
    doc = build_resume(founder_profile, JOB)
    assert doc["experience_order"] == "finance_first"
    assert _employers(doc) == [
        "Northwind Credit Union", "Lakeshore Payments Inc.", "Riverbend Savings Bank", "Harbour Lane Studio"]
    founder = doc["experience"][-1]
    assert founder["title"] == "Founder" and founder["dates"] == "Jan 2024 \u2013 Present" and founder["bullets"]  # kept, just lower
    assert check_resume(doc, founder_profile) == []
    letter = build_cover_letter(founder_profile, JOB, doc)
    experience = [p["text"] for p in letter["paragraphs"] if p["text"].startswith("As ")]
    assert experience[0].startswith("As Senior Fraud Analyst at Northwind Credit Union")
    assert not any("Harbour Lane" in text for text in experience)
    assert check_cover_letter(letter, founder_profile, job_description=JOB.text) == []


def test_non_finance_target_keeps_reverse_chronological_order(founder_profile):
    doc = build_resume(founder_profile, NON_FINANCE_JOB)
    assert doc["experience_order"] == "chronological"
    assert _employers(doc) == [
        "Harbour Lane Studio", "Northwind Credit Union", "Lakeshore Payments Inc.", "Riverbend Savings Bank"]
    letter = build_cover_letter(founder_profile, NON_FINANCE_JOB, doc)
    assert letter["paragraphs"][1]["text"].startswith("As Founder at Harbour Lane Studio (Jan 2024 \u2013 present), ")


@pytest.mark.parametrize("title, company, description, expected", [
    ("Fraud Strategy Analyst", "Acme", "", True),
    ("Collections Agent", "Acme", "", True),
    ("Credit Adjudicator", "Acme", "", True),
    ("Compliance Officer", "Acme", "", True),
    ("KYC Analyst", "Acme", "", True),
    ("AML Investigator, Financial Crimes", "Acme", "", True),
    ("Disputes Specialist", "Acme", "", True),
    ("Personal Banking Associate", "Acme", "", True),
    ("Client Service Representative", "Maple Bank", "", True),
    ("Customer Support Specialist", "Acme", "Help members of our credit union with accounts.", True),
    ("Member Services Advisor", "Acme", "A fintech serving newcomers.", True),
    ("Client Care Associate", "Acme", "Explain loans, mortgages and bank transfers to clients.", True),
    ("CX Coordinator", "Acme Payment Systems", "Help merchants with payments and credit card terminals.", True),
    ("Account Protection Specialist", "Acme", "", True),
    ("Customer Support Specialist", "Acme", "Help shoppers track orders and returns.", False),
    ("Support Specialist, Premium", "Acme Stays", "Guide hosts through payment issues and payouts to their bank account.", False),
    ("Software Engineer", "Maple Bank", "Build banking APIs.", False),
    ("Brand Marketing Coordinator", "Cedar Goods", "", False),
])
def test_finance_target_detection(title, company, description, expected):
    assert is_finance_target(JobContext(title=title, company=company, description=description)) is expected


@pytest.mark.parametrize("employer, title, expected", [
    ("Scotiabank", "Officer", True),
    ("BMO", "Officer", True),
    ("RBC", "Officer", True),
    ("TD", "Officer", True),
    ("Canada Trust", "Officer", True),
    ("Northwind Credit Union", "Analyst", True),
    ("Lakeshore Payments Inc.", "Analyst", True),
    ("Cedar Goods", "Fraud Analyst", True),  # finance-domain title at a non-bank
    ("Harbour Lane Studio", "Founder", False),
    ("Tdot Coffee", "Barista", False),
])
def test_finance_employment_detection(employer, title, expected):
    fact = Fact(key="employment:x", category="employment", data={"employer": employer, "title": title})
    assert is_finance_employment(fact) is expected


# -------------------------------------------------------------- truthfulness guard


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d["experience"][0].update(employer="Google"), "employer"),
    (lambda d: d["experience"][0].update(title="Director of Fraud"), "title"),
    (lambda d: d["experience"][1].update(start="2018-06"), "start"),
    (lambda d: d["education"][0].update(degree="MBA"), "degree"),
    (lambda d: d["experience"][0]["bullets"][0].update(text="Cut false-positive fraud alerts by 45% using SQL"), "number '45'"),
    (lambda d: d["experience"][0]["bullets"][1].update(text="Led weekly AML case reviews on Kubernetes for 6 analysts"), "kubernetes"),
    (lambda d: d["experience"][0]["bullets"][1].update(text="Led weekly AML reviews and doubled team output for 6 analysts"), "doubled"),
    (lambda d: d["experience"][0]["bullets"].append({"text": "Saved $2M", "fact": "achievement:invented"}), "no verified achievement"),
    (lambda d: d["skills"].append({"text": "Actimize", "fact": "skill:actimize"}), "not a verified skill"),
    (lambda d: d["experience"].append({**d["experience"][0], "fact": "employment:acme", "employer": "Acme"}), "not a verified employment"),
    (lambda d: d["summary"].update(text="Senior fraud analyst with 12 years at Google."), "12"),
])
def test_guard_rejects_fabricated_resume_claims(profile, mutate, expected):
    doc = copy.deepcopy(_resume(profile))
    mutate(doc)
    violations = check_resume(doc, profile)
    assert violations and any(expected.lower() in v.lower() for v in violations), violations


def test_guard_accepts_faithful_rewording(profile):
    doc = copy.deepcopy(_resume(profile))
    doc["experience"][0]["bullets"][0]["text"] = "Redesigned card-transaction rules in SQL, cutting false-positive fraud alerts by 32%"
    assert check_resume(doc, profile) == []


@pytest.mark.parametrize("addition, expected", [
    (" I also hold an MBA.", "mba"),
    (" Before that I worked at Google.", "Google"),
    (" I cut losses by 50%.", "number '50'"),
    (" I am an expert in Actimize.", "actimize"),
    (" I was promoted to Director in March 2020.", "director"),
])
def test_guard_rejects_fabricated_cover_letter_claims(profile, addition, expected):
    letter = build_cover_letter(profile, JOB, _resume(profile))
    assert check_cover_letter(letter, profile, job_description=JOB.text) == []
    letter["paragraphs"][1]["text"] += addition
    violations = check_cover_letter(letter, profile, job_description=JOB.text)
    assert any(expected.lower() in v.lower() for v in violations), violations


def test_template_wording_is_not_flagged_when_a_real_posting_uses_the_same_words(profile):
    # Real postings often say "we welcome applications", "following requirements", "this posting"...
    # The template's own fixed phrasing is not a claim and must not block the letter.
    job = JobContext(title=JOB.title, company=JOB.company, location=JOB.location,
                     description=JOB.description + " We welcome every chance to discuss the following requirements "
                     "of this posting; considering a match, you hold the keys.")
    letter = build_cover_letter(profile, job, build_resume(profile, job))
    assert check_cover_letter(letter, profile, job_description=job.text) == []
    letter["paragraphs"][1]["text"] += " I am an expert in Actimize."
    assert check_cover_letter(letter, profile, job_description=job.text)


def test_cover_letter_names_the_job_but_claims_only_verified_facts(profile):
    text = cover_letter_text(build_cover_letter(profile, JOB, _resume(profile)))
    assert text.startswith("Dear Hiring Team at Maplebank,")
    assert "Bilingual Fraud Analyst position at Maplebank" in text
    assert "32%" in text and "Kubernetes" not in text and "MBA" not in text
    assert text.rstrip().endswith("+1 613 555 0142")


# ---------------------------------------------------------------- LLM rewording


class FakeLLM:
    name = "fake:test"

    def __init__(self, transform):
        self.transform = transform
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        items = json.loads(prompt[prompt.index("{"):])
        return json.dumps({key: self.transform(key, text) for key, text in items.items()})


def test_llm_is_off_by_default_and_needs_explicit_config():
    assert llm_client(Settings(_env_file=None)) is None
    assert llm_client(Settings(_env_file=None, materials_llm_enabled=True, materials_llm_provider="openai")) is None
    client = llm_client(Settings(_env_file=None, materials_llm_enabled=True))
    assert client is not None and client.name.startswith("ollama:")


def test_faithful_llm_rewording_is_accepted(profile):
    doc = _resume(profile)
    llm = FakeLLM(lambda key, text: text.rstrip(".") + ".")
    result, report = reword_resume(doc, profile, llm)
    assert report == {"status": "accepted", "llm": "fake:test"}
    assert result["experience"][0]["bullets"][0]["text"].endswith(".")
    assert "Kubernetes" in llm.prompts[0] or "do not add" in llm.prompts[0]


@pytest.mark.parametrize("transform, status", [
    (lambda key, text: text.replace("32%", "52%"), "rejected"),
    (lambda key, text: text + " using Kubernetes", "rejected"),
    (lambda key, text: text.replace("Northwind", "Google"), "accepted_if_unchanged"),
])
def test_fabricating_llm_output_falls_back_to_the_template(profile, transform, status):
    doc = _resume(profile)
    result, report = reword_resume(doc, profile, FakeLLM(transform))
    if status == "rejected":
        assert report["status"] == "rejected" and report["violations"]
        assert result is doc
    else:  # bullets do not contain the employer name, so nothing changed
        assert check_resume(result, profile) == []


@pytest.mark.parametrize("raw", ["not json", "{}", '{"summary": ""}', "[1, 2]"])
def test_broken_llm_output_is_an_error_not_a_crash(profile, raw):
    class Broken:
        name = "broken"

        def complete(self, prompt):
            return raw

    result, report = reword_resume(_resume(profile), profile, Broken())
    assert report["status"] == "error"
    letter = build_cover_letter(profile, JOB, result)
    same, letter_report = reword_cover_letter(letter, profile, Broken(), JOB.text)
    assert same is letter and letter_report["status"] == "error"


def test_cover_letter_llm_fabrication_is_rejected(profile):
    letter = build_cover_letter(profile, JOB, _resume(profile))
    result, report = reword_cover_letter(letter, profile, FakeLLM(lambda k, t: t + " I have an MBA."), JOB.text)
    assert report["status"] == "rejected" and result is letter


# --------------------------------------------------------------------- rendering


def test_pdf_is_ats_friendly_reproducible_and_truthful(profile, tmp_path):
    doc = _resume(profile)
    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF") and pdf == render_pdf_bytes(doc)
    path = tmp_path / "resume.pdf"
    path.write_bytes(pdf)
    text, _ = extract_text(path)
    for expected in ("Avery Quinn", "Northwind Credit Union", "Senior Fraud Analyst", "Apr 2022 \u2013 Present", "• Cut false-positive"):
        assert expected in text
    assert "Kubernetes" not in text
    letter = render_pdf_bytes(build_cover_letter(profile, JOB, doc))
    assert letter.startswith(b"%PDF")


def test_docx_is_a_valid_word_document(profile):
    data = render_docx_bytes(_resume(profile))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(archive.namelist())
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "Northwind Credit Union" in xml and "Kubernetes" not in xml


# ------------------------------------------------------------- approval workflow


def _shortlisted(db, tmp_path, stage=PipelineStage.shortlisted.value):
    job = Job(source="greenhouse", external_id="77", title=JOB.title, company=JOB.company, location="Ottawa, ON",
              url="https://job-boards.greenhouse.io/maplebank/jobs/77", description=JOB.description, stage=stage,
              platform="greenhouse")
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, stage=stage, mode="dry_run", adapter_name="greenhouse")
    db.add(application)
    db.commit()
    settings = Settings(_env_file=None, materials_dir=str(tmp_path / "materials"), automation_enabled=True)
    return job, application, settings


def test_generation_needs_a_ready_profile(tmp_path):
    db = memory_db()
    load_example_profile(db, verified=False)
    _job, application, settings = _shortlisted(db, tmp_path)
    with pytest.raises(MaterialsError, match="profile is not ready"):
        generate_for_application(db, settings, application.id)


def test_drafts_are_versioned_hashed_and_never_attached_until_approved(tmp_path):
    db = memory_db()
    load_example_profile(db)
    job, application, settings = _shortlisted(db, tmp_path)
    rows = generate_for_application(db, settings, application.id)
    db.refresh(job)
    assert job.stage == PipelineStage.materials_generated.value
    assert [(r.kind, r.version, r.status) for r in rows] == [("resume", 1, "draft"), ("cover_letter", 1, "draft")]
    assert all(len(r.content_sha256) == 64 and r.pdf_sha256 and r.docx_sha256 for r in rows)
    task = db.execute(select(ReviewTask)).scalars().one()
    assert task.reason_code == "materials_review" and task.status == "open"
    assert job.stage == PipelineStage.materials_generated.value  # the task does not hold the job

    # Drafts never count: not ready, not offered to forms, not in a dry-run manifest.
    approve_application(db, application.id)
    db.refresh(job)
    assert job.stage == PipelineStage.materials_generated.value
    assert not materials_ready(db, application)
    assert vault_records(db, application) == []
    assert manifest(db, application)["attached"] == []

    # Regenerating supersedes the drafts and bumps the version.
    second = generate_for_application(db, settings, application.id)
    assert [r.version for r in second] == [2, 2]
    statuses = {(r.kind, r.version): r.status for r in db.execute(select(ApplicationMaterial)).scalars()}
    assert statuses[("resume", 1)] == "superseded" and statuses[("resume", 2)] == "draft"

    resume, letter = second
    approve_material(db, resume.id, "looks right")
    assert db.get(ReviewTask, task.id).status == "open"  # cover letter still pending
    reject_material(db, letter.id, "no cover letter for this one")
    db.refresh(job)
    assert db.get(ReviewTask, task.id).status == "approved"
    assert job.stage == PipelineStage.ready_to_apply.value
    records = {record.key: record.value for record in vault_records(db, application)}
    assert records["resume"] == resume.pdf_path and "cover_letter" not in records
    db.refresh(application)
    assert application.tailored_resume_path == resume.pdf_path and application.cover_letter_text is None

    kinds = [e.kind for e in db.execute(select(SubmissionEvidence).order_by(SubmissionEvidence.created_at)).scalars()]
    assert kinds.count("materials_draft") == 4 and kinds.count("materials_approved") == 1
    assert not db.execute(select(SubmissionEvidence).where(SubmissionEvidence.sufficient.is_(True))).first()


def test_approval_is_refused_for_stale_or_tampered_materials(tmp_path):
    db = memory_db()
    load_example_profile(db)
    _job, application, settings = _shortlisted(db, tmp_path)
    resume, letter = generate_for_application(db, settings, application.id)
    fact = db.execute(select(ProfileFact).where(ProfileFact.key == "employment:northwind")).scalar_one()
    set_verified(db, fact.id, False)
    with pytest.raises(MaterialsError, match="no longer verified"):
        approve_material(db, resume.id)
    set_verified(db, fact.id, True)
    with open(letter.pdf_path, "ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(MaterialsError, match="modified"):
        approve_material(db, letter.id)
    approve_material(db, resume.id)
    with pytest.raises(MaterialsError, match="only a draft"):
        approve_material(db, resume.id)
    # Unverifying a fact later also withdraws the approved version from use.
    set_verified(db, fact.id, False)
    assert not materials_ready(db, application)
    assert vault_records(db, application) == []
    assert "no longer verified" in manifest(db, application)["problems"][0]


FORM = """<form><label for=fn>First name</label><input id=fn name=first_name required>
<label for=ln>Last name</label><input id=ln name=last_name required>
<label for=em>Email</label><input id=em name=email type=email required>
<label for=cv>Resume</label><input id=cv name=resume type=file required></form>"""


def test_dry_run_attaches_only_the_approved_resume_and_records_its_hash(tmp_path):
    db = memory_db()
    load_example_profile(db)
    job, application, settings = _shortlisted(db, tmp_path, stage=PipelineStage.ready_to_apply.value)
    # No approved résumé: the required file field blocks the dry-run.
    blocked = execute_apply(db, settings, application.id, html=FORM)
    assert blocked["status"] == "needs_review" and "resume" in blocked["blocked_fields"]

    job.stage = PipelineStage.ready_to_apply.value
    db.commit()
    resume, _letter = generate_for_application(db, settings, application.id)
    approve_material(db, resume.id)
    result = execute_apply(db, settings, application.id, html=FORM)
    assert result["status"] == "dry_run_complete" and result["submitted"] is False
    attached = result["materials"]["attached"]
    assert [(item["kind"], item["version"], item["content_sha256"]) for item in attached] == [
        ("resume", 1, resume.content_sha256)]
    db.refresh(application)
    assert json.loads(application.answers_json)["resume"] == resume.pdf_path
    assert json.loads(application.validation_json)["materials"]["attached"][0]["material_id"] == resume.id
