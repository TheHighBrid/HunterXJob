"""End-to-end Browser Dry-Run Verification Suite for Newly Added Units.

Covers:
1. Low Difficulty Platforms:
   - Workable (apply.workable.com)
   - Breezy HR (breezy.hr)
   - Recruitee (recruitee.com)
   - Lever (jobs.lever.co)
   - Greenhouse (boards.greenhouse.io)

2. Canadian Federal, Provincial & Municipal Government Units 🇨🇦:
   - GC Jobs (Federal - jobs.gc.ca)
   - City of Ottawa Careers (Municipal - ottawa.ca / HRsmart)
   - Ontario Public Service (Provincial - gojobs.gov.on.ca)
   - Ville de Gatineau (Municipal Outaouais - gatineau.ca)
   - Québec Emploi (Provincial Québec - quebecemploi.gouv.qc.ca)

Enforces:
- Form field discovery & interactive DOM population via Playwright
- File attachments (resume PDF) via set_input_files
- Read-back validation against expected answers
- Pre-submission Truth Guard enforcement (fail-closed on unverified claims, country-aware location)
- Strict dry-run safety guarantee: submitted is NEVER True, final submit button is NOT clicked
- Pre-submission screenshot and SHA-256 evidence hashing
"""
from __future__ import annotations

import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from app.autonomous_loop import AutonomousConfig, AutonomousRunner
from app.browser_filler import fill_application_form
from app.db import Base
from app.form_engine import ControlType, FormControl
from app.greenhouse_form import RealForm
from app.models import Application, Job, PipelineEvent, PipelineStage
from app.truth_guard import audit_form_answers, enforce_answer_truthfulness
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, format, *args):
        pass


@pytest.fixture(scope="module")
def fixture_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def candidate_profile():
    return {
        "verified": True,
        "contact": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, ON, Canada",
            "linkedin_url": "https://www.linkedin.com/in/jordanlee",
            "github_url": "https://github.com/jordanlee",
        },
        "summary": "Senior Automation and Cloud Systems Engineer with over 6 years of expertise.",
        "work_authorization": [
            {
                "country": "CA",
                "authorized": True,
                "requires_sponsorship": False,
                "status": "Citizen",
            }
        ],
        "employment": [
            {
                "id": "exp1",
                "employer": "Public Sector Innovation Corp",
                "title": "Senior Cloud Infrastructure Engineer",
                "location": "Ottawa, ON",
                "start": "2020-01",
                "current": True,
                "achievements": [
                    {
                        "text": "Automated government workflows and cloud deployments.",
                        "metrics": ["40%"],
                        "skills": ["Python", "Playwright", "FastAPI"],
                    }
                ],
            }
        ],
        "education": [
            {
                "id": "edu1",
                "institution": "University of Ottawa",
                "degree": "B.Sc. Computer Science",
                "start": "2015-09",
                "end": "2019-05",
            }
        ],
        "skills": [
            {"name": "Python"},
            {"name": "Playwright"},
            {"name": "FastAPI"},
            {"name": "PostgreSQL"},
        ],
        "certifications": [],  # Verified: NO CPA designation!
    }


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_dryruns.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    SessionClass = sessionmaker(bind=engine)
    with SessionClass() as db:
        yield db


# ============================================================================
# 1. Low Difficulty Platforms: Workable, Breezy HR, Recruitee, Lever, Greenhouse
# ============================================================================

def test_dryrun_workable_unit(fixture_server, tmp_path):
    """Verify dry-run execution on Workable form DOM structure."""
    resume_file = tmp_path / "jordan_lee_workable_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Workable Resume")

    url = f"{fixture_server}/ats/workable_apply.html"
    answers = {
        "name": "Jordan Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "address": "Ottawa, Ontario, Canada",
        "summary": "Senior engineer excited to contribute to high-reliability platforms.",
        "work_auth": "Yes",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "workable_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False, "CRITICAL: Dry-run must never click submit"
    assert result.fields_filled >= 6
    assert len(result.files_attached) == 1
    assert result.files_attached[0].file_name == "jordan_lee_workable_resume.pdf"
    assert result.field_reports["name"].read_back_value == "Jordan Lee"
    assert result.field_reports["email"].read_back_value == "jordan.lee@example.com"
    assert screenshot.is_file()
    assert result.screenshot_sha256 is not None


def test_dryrun_breezy_unit(fixture_server, tmp_path):
    """Verify dry-run execution on Breezy HR form DOM structure."""
    resume_file = tmp_path / "jordan_lee_breezy_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Breezy Resume")

    url = f"{fixture_server}/ats/breezy_apply.html"
    answers = {
        "name": "Jordan Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "address": "Ottawa, ON, Canada",
        "cover_letter": "Passionate about building scalable distributed systems.",
        "eligible_canada": "Yes",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "breezy_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 6
    assert len(result.files_attached) == 1
    assert result.field_reports["name"].read_back_value == "Jordan Lee"
    assert result.field_reports["eligible_canada"].matched is True
    assert screenshot.is_file()


def test_dryrun_recruitee_unit(fixture_server, tmp_path):
    """Verify dry-run execution on Recruitee form DOM structure."""
    resume_file = tmp_path / "jordan_lee_recruitee_cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Recruitee CV")

    url = f"{fixture_server}/ats/recruitee_apply.html"
    answers = {
        "name": "Jordan Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "cover_letter": "Strong background in cloud native engineering.",
        "open_question": "Excited about modern architecture and continuous automation.",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "recruitee_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 5
    assert len(result.files_attached) == 1
    assert result.files_attached[0].file_name == "jordan_lee_recruitee_cv.pdf"
    assert result.field_reports["name"].read_back_value == "Jordan Lee"
    assert screenshot.is_file()


def test_dryrun_lever_unit(fixture_server, tmp_path):
    """Verify dry-run execution on public Lever /apply form."""
    resume_file = tmp_path / "jordan_lee_lever_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Lever Resume")

    url = f"{fixture_server}/ats/lever_apply.html"
    answers = {
        "name": "Jordan Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Ottawa, Ontario, Canada",
        "org": "Public Sector Innovation Corp",
        "urls[LinkedIn]": "https://www.linkedin.com/in/jordanlee",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "lever_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 6
    assert len(result.files_attached) == 1
    assert result.field_reports["name"].read_back_value == "Jordan Lee"
    assert result.field_reports["org"].read_back_value == "Public Sector Innovation Corp"
    assert screenshot.is_file()


def test_dryrun_greenhouse_unit(fixture_server, tmp_path):
    """Verify dry-run execution on Greenhouse form."""
    resume_file = tmp_path / "jordan_lee_greenhouse_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Greenhouse Resume")

    url = f"{fixture_server}/ats/greenhouse_apply.html"
    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Ottawa, Ontario, Canada",
        "work_authorization": "Yes",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "greenhouse_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 6
    assert len(result.files_attached) == 1
    assert result.field_reports["first_name"].read_back_value == "Jordan"
    assert screenshot.is_file()


# ============================================================================
# 2. Canadian Federal, Provincial & Municipal Government Units 🇨🇦
# ============================================================================

def test_dryrun_gc_jobs_unit(fixture_server, tmp_path):
    """Verify dry-run on Federal GC Jobs (jobs.gc.ca) with PSEA citizenship and language profile."""
    resume_file = tmp_path / "jordan_lee_gc_jobs_cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 GC Jobs Resume")

    url = f"{fixture_server}/ats/gc_jobs_apply.html"
    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Ottawa, Ontario, Canada",
        "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
        "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
        "security_clearance": "Reliability Status / Cote de fiabilité",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "gc_jobs_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 8
    assert len(result.files_attached) == 1
    assert result.field_reports["first_name"].read_back_value == "Jordan"
    assert result.field_reports["canadian_citizenship"].matched is True
    assert result.field_reports["language_proficiency"].matched is True
    assert result.field_reports["security_clearance"].matched is True
    assert screenshot.is_file()


def test_dryrun_ottawa_careers_unit(fixture_server, tmp_path):
    """Verify dry-run on City of Ottawa Careers with NCR residency and bilingual qualification."""
    resume_file = tmp_path / "jordan_lee_ottawa_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Ottawa Careers Resume")

    url = f"{fixture_server}/ats/ottawa_apply.html"
    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Ottawa, Ontario, Canada",
        "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
        "ottawa_resident": "Yes / Oui",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "ottawa_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 7
    assert len(result.files_attached) == 1
    assert result.field_reports["first_name"].read_back_value == "Jordan"
    assert result.field_reports["ottawa_resident"].matched is True
    assert screenshot.is_file()


def test_dryrun_ops_careers_unit(fixture_server, tmp_path):
    """Verify dry-run on Ontario Public Service (gojobs.gov.on.ca)."""
    resume_file = tmp_path / "jordan_lee_ops_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 OPS Resume")

    url = f"{fixture_server}/ats/ops_apply.html"
    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Ottawa, Ontario, Canada",
        "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
        "ontario_resident": "Yes / Oui",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "ops_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 7
    assert len(result.files_attached) == 1
    assert result.field_reports["first_name"].read_back_value == "Jordan"
    assert result.field_reports["ontario_resident"].matched is True
    assert screenshot.is_file()


def test_dryrun_gatineau_careers_unit(fixture_server, tmp_path):
    """Verify dry-run on Ville de Gatineau Careers (cvmanager French municipal portal)."""
    resume_file = tmp_path / "jordan_lee_gatineau_cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Gatineau CV")

    url = f"{fixture_server}/ats/gatineau_apply.html"
    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Gatineau, QC, Canada",
        "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
        "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
        "outaouais_resident": "Yes / Oui",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "gatineau_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 8
    assert len(result.files_attached) == 1
    assert result.field_reports["first_name"].read_back_value == "Jordan"
    assert result.field_reports["outaouais_resident"].matched is True
    assert screenshot.is_file()


def test_dryrun_quebec_emploi_unit(fixture_server, tmp_path):
    """Verify dry-run on Québec Emploi provincial portal."""
    resume_file = tmp_path / "jordan_lee_quebec_cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Quebec CV")

    url = f"{fixture_server}/ats/quebec_emploi_apply.html"
    answers = {
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1 613 555 0199",
        "location": "Gatineau, QC, Canada",
        "canadian_citizenship": "Canadian Citizen / Citoyen canadien",
        "language_proficiency": "Bilingual / Bilingue (English & French / Anglais et Français)",
        "quebec_resident": "Yes / Oui",
    }
    files = {"resume": resume_file}
    screenshot = tmp_path / "quebec_emploi_pre_submit.png"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            screenshot_path=screenshot,
            headless=True,
            allow_live_submit=False,
        )
    )

    assert result.success is True
    assert result.submitted is False
    assert result.fields_filled >= 8
    assert len(result.files_attached) == 1
    assert result.field_reports["first_name"].read_back_value == "Jordan"
    assert result.field_reports["quebec_resident"].matched is True
    assert screenshot.is_file()


# ============================================================================
# 3. Truth Guard Audit Fixes Verification in Dry-Run
# ============================================================================

def test_truth_guard_blocks_unverified_cpa_in_dry_run(candidate_profile):
    """Verify that unverified CPA question is strictly corrected to 'No' before browser filling."""
    answers = {
        "first_name": "Jordan",
        "email": "jordan.lee@example.com",
        "cpa_designation": "Yes",  # Unverified assertion from recording audit!
    }
    labels = {
        "cpa_designation": "Do you hold a CPA designation (or equivalent)?",
    }

    violations = audit_form_answers(answers, candidate_profile, labels=labels)
    assert len(violations) > 0
    assert any("CPA" in v for v in violations)

    enforced_answers, corrections = enforce_answer_truthfulness(answers, candidate_profile, labels=labels)
    assert enforced_answers["cpa_designation"] == "No"
    assert len(corrections) > 0


# ============================================================================
# 4. Autonomous Loop End-to-End Dry-Run on New Units
# ============================================================================

def test_autonomous_runner_executes_new_units_dryrun(test_db, tmp_path, candidate_profile, fixture_server):
    """Verify that AutonomousRunner processes new platform jobs in safe dry-run mode."""
    # Seed 3 jobs for newly added units: Workable, Breezy, GC Jobs
    jobs = [
        Job(
            source="workable",
            external_id="workable_1",
            url=f"{fixture_server}/ats/workable_apply.html",
            company="Acme Workable",
            title="Senior Python Backend Developer",
            stage=PipelineStage.ready_to_apply.value,
            final_score=94.0,
        ),
        Job(
            source="breezy",
            external_id="breezy_1",
            url=f"{fixture_server}/ats/breezy_apply.html",
            company="CloudFlow Breezy",
            title="Senior Cloud Engineer",
            stage=PipelineStage.ready_to_apply.value,
            final_score=91.0,
        ),
        Job(
            source="gc_jobs",
            external_id="gc_jobs_1",
            url=f"{fixture_server}/ats/gc_jobs_apply.html",
            company="Government of Canada",
            title="Senior Policy Analyst",
            stage=PipelineStage.ready_to_apply.value,
            final_score=88.0,
        ),
    ]
    for j in jobs:
        test_db.add(j)
    test_db.commit()

    class MockFormProvider:
        def fetch(self, job):
            if "workable" in getattr(job, "source", ""):
                return RealForm(
                    platform="workable",
                    source="workable",
                    url=job.url,
                    title=job.title,
                    controls=[
                        FormControl(key="name", label="Full name", control_type=ControlType.TEXT, required=True, name="name"),
                        FormControl(key="email", label="Email", control_type=ControlType.EMAIL, required=True, name="email"),
                        FormControl(key="phone", label="Phone", control_type=ControlType.TEL, required=True, name="phone"),
                        FormControl(key="address", label="Address / City", control_type=ControlType.TEXT, required=True, name="address"),
                        FormControl(key="summary", label="Summary", control_type=ControlType.TEXTAREA, required=False, name="summary"),
                        FormControl(key="resume", label="Resume/CV", control_type=ControlType.FILE, required=True, name="resume"),
                        FormControl(key="work_auth", label="Are you legally authorized to work in Canada?", control_type=ControlType.SELECT, required=True, name="work_auth", options=["Yes", "No"]),
                    ],
                )
            elif "breezy" in getattr(job, "source", ""):
                return RealForm(
                    platform="breezy",
                    source="breezy",
                    url=job.url,
                    title=job.title,
                    controls=[
                        FormControl(key="name", label="Name", control_type=ControlType.TEXT, required=True, name="name"),
                        FormControl(key="email", label="Email", control_type=ControlType.EMAIL, required=True, name="email"),
                        FormControl(key="phone", label="Phone", control_type=ControlType.TEL, required=True, name="phone"),
                        FormControl(key="address", label="Location", control_type=ControlType.TEXT, required=True, name="address"),
                        FormControl(key="resume", label="Resume", control_type=ControlType.FILE, required=True, name="resume"),
                        FormControl(key="cover_letter", label="Cover Letter", control_type=ControlType.TEXTAREA, required=False, name="cover_letter"),
                        FormControl(key="eligible_canada", label="Are you eligible to work in Canada?", control_type=ControlType.RADIO, required=True, name="eligible_canada"),
                    ],
                )
            else:  # gc_jobs
                from app.canadian_gov_portals import make_canadian_gov_controls
                return RealForm(
                    platform="gc_jobs",
                    source="gc_jobs",
                    url=job.url,
                    title=job.title,
                    controls=make_canadian_gov_controls("gc_jobs"),
                )

    runner = AutonomousRunner(
        db=test_db,
        config=AutonomousConfig(
            allow_live_submission=False,
            min_score_threshold=70.0,
            profile_data=candidate_profile,
            output_dir=tmp_path,
            headless=True,
        ),
        form_provider=MockFormProvider(),
    )

    summary = runner.run()

    assert summary.aborted_by_kill_switch is False
    assert summary.candidates_considered >= 3
    assert summary.shortlisted == 3
    assert summary.submitted_live == 0, "No live submission during dry-run"
    assert summary.verified_dry_runs == 3, "All 3 newly added units must verify dry-run"

    # Verify pipeline events recorded in database
    events = test_db.execute(select(PipelineEvent)).scalars().all()
    event_messages = [e.message for e in events]
    assert any("dry-run verified" in m for m in event_messages)
