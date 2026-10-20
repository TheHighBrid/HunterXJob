import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from app.autonomous_loop import (
    AutonomousConfig,
    AutonomousRunner,
)
from app.captcha_bridge import MockCaptchaSolver
from app.config import Settings
from app.db import Base
from app.flags import set_flag
from app.form_engine import ControlType, FormControl
from app.greenhouse_form import RealForm
from app.models import (
    Application,
    Job,
    PipelineEvent,
    PipelineStage,
    ProfileFact,
    ReviewTask,
)
from app.submission_engine import SubmissionResult
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_autonomous.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    SessionClass = sessionmaker(bind=engine)
    with SessionClass() as db:
        yield db


@pytest.fixture
def candidate_profile():
    return {
        "verified": True,
        "contact": {
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@example.com",
            "phone": "+1 613 555 0199",
            "location": "Ottawa, ON",
            "linkedin_url": "https://www.linkedin.com/in/jordanlee",
            "github_url": "https://github.com/jordanlee",
        },
        "summary": "Senior software engineer with 5 years building automation and cloud systems.",
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
                "id": "techcorp",
                "employer": "TechCorp",
                "title": "Senior Automation Engineer",
                "location": "Ottawa, ON",
                "start": "2021-01",
                "current": True,
                "achievements": [
                    {
                        "text": "Engineered automated browser pipelines with Playwright",
                        "metrics": ["50%"],
                        "skills": ["Python", "Playwright"],
                    }
                ],
            }
        ],
        "education": [
            {
                "id": "uottawa",
                "institution": "University of Ottawa",
                "degree": "B.Sc. Computer Science",
                "start": "2016-09",
                "end": "2020-05",
            }
        ],
        "skills": [
            {"name": "Python"},
            {"name": "Playwright"},
            {"name": "FastAPI"},
        ],
    }


def test_kill_switch_aborts_loop(test_db, tmp_path, candidate_profile):
    set_flag(test_db, "global_kill_switch", True, "testing safety abort")
    runner = AutonomousRunner(
        db=test_db,
        config=AutonomousConfig(profile_data=candidate_profile, output_dir=tmp_path),
    )
    summary = runner.run()
    assert summary.aborted_by_kill_switch is True
    assert summary.submitted_live == 0
    assert summary.verified_dry_runs == 0


def test_autonomous_dry_run_cycle(test_db, tmp_path, candidate_profile):
    # Create HTML test form
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <form id="apply-form">
          <input type="text" id="first_name" name="first_name" value="" />
          <input type="text" id="last_name" name="last_name" value="" />
          <input type="email" id="email" name="email" value="" />
          <input type="file" id="resume" name="resume" />
          <button type="submit" id="submit-btn">Submit</button>
        </form>
      </body>
    </html>
    """
    html_file = tmp_path / "form.html"
    html_file.write_text(html, encoding="utf-8")

    job = Job(
        source="greenhouse",
        external_id="gh_123",
        url=f"file://{html_file}",
        company="Shopify",
        title="Senior Python Engineer",
        stage=PipelineStage.ready_to_apply.value,
        final_score=92.0,
    )
    test_db.add(job)
    test_db.commit()

    # Mock form provider returning controls matching our fixture
    mock_provider = MagicMock()
    mock_provider.fetch.return_value = RealForm(
        platform="greenhouse",
        source="greenhouse",
        url=f"file://{html_file}",
        title="Senior Python Engineer",
        controls=[
            FormControl(key="first_name", label="First Name", control_type=ControlType.TEXT, required=True, name="first_name"),
            FormControl(key="last_name", label="Last Name", control_type=ControlType.TEXT, required=True, name="last_name"),
            FormControl(key="email", label="Email", control_type=ControlType.EMAIL, required=True, name="email"),
            FormControl(key="resume", label="Resume", control_type=ControlType.FILE, required=True, name="resume"),
        ],
    )

    runner = AutonomousRunner(
        db=test_db,
        config=AutonomousConfig(
            allow_live_submission=False,
            min_score_threshold=70.0,
            profile_data=candidate_profile,
            output_dir=tmp_path,
        ),
        form_provider=mock_provider,
    )

    summary = runner.run()
    assert summary.aborted_by_kill_switch is False
    assert summary.candidates_considered >= 1
    assert summary.shortlisted == 1
    assert summary.verified_dry_runs == 1
    assert summary.submitted_live == 0

    # Verify event recorded in DB
    event = test_db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job.id)).scalar_one_or_none()
    assert event is not None
    assert "dry-run verified" in event.message


def test_autonomous_dedup_blocks_duplicate(test_db, tmp_path, candidate_profile):
    job1 = Job(
        id="job1_primary",
        source="greenhouse",
        external_id="ext_dup1",
        url="https://boards.greenhouse.io/acme/jobs/111",
        company="Acme Corp",
        title="Software Engineer",
        stage=PipelineStage.submitted.value,
        final_score=95.0,
    )
    job2 = Job(
        id="job2_duplicate",
        source="lever",
        external_id="ext_dup2",
        url="https://jobs.lever.co/acme/222",
        company="Acme Corp",
        title="Software Engineer",
        stage=PipelineStage.ready_to_apply.value,
        final_score=95.0,
        duplicate_of_id="job1_primary",
    )
    test_db.add_all([job1, job2])
    test_db.commit()

    runner = AutonomousRunner(
        db=test_db,
        config=AutonomousConfig(
            allow_live_submission=False,
            min_score_threshold=70.0,
            profile_data=candidate_profile,
            output_dir=tmp_path,
        ),
    )
    summary = runner.run()
    # job2 is a duplicate of already-submitted job1, so it must be skipped by dedup
    assert summary.skipped_duplicates >= 1
    assert summary.submitted_live == 0


def test_autonomous_live_submission_flow(test_db, tmp_path, candidate_profile, monkeypatch):
    from unittest.mock import AsyncMock
    from app.submission_engine import SubmissionResult

    html = """
    <!DOCTYPE html>
    <html><body><form id="f"><input id="first_name" name="first_name" /><input id="last_name" name="last_name" /><input id="email" name="email" /><input type="file" id="resume" name="resume" /><button type="submit">Submit</button></form></body></html>
    """
    html_file = tmp_path / "live_form.html"
    html_file.write_text(html, encoding="utf-8")

    job = Job(
        source="greenhouse",
        external_id="gh_live_999",
        url=f"file://{html_file}",
        company="Klarna",
        title="Software Engineer",
        stage=PipelineStage.ready_to_apply.value,
        final_score=88.0,
    )
    test_db.add(job)
    test_db.commit()

    mock_provider = MagicMock()
    mock_provider.fetch.return_value = RealForm(
        platform="greenhouse",
        source="greenhouse",
        url=f"file://{html_file}",
        title="Software Engineer",
        controls=[
            FormControl(key="first_name", label="First Name", control_type=ControlType.TEXT, required=True, name="first_name"),
            FormControl(key="last_name", label="Last Name", control_type=ControlType.TEXT, required=True, name="last_name"),
            FormControl(key="email", label="Email", control_type=ControlType.EMAIL, required=True, name="email"),
            FormControl(key="resume", label="Resume", control_type=ControlType.FILE, required=True, name="resume"),
        ],
    )

    # Mock execute_live_submission to return a verified submission
    mock_sub = AsyncMock()
    mock_sub.return_value = SubmissionResult(
        status="submitted",
        success=True,
        final_url="https://boards.greenhouse.io/confirmation",
        confirmation_text="Application submitted successfully. Candidate ID: CAND-7788",
        candidate_id="CAND-7788",
        screenshot_sha256="abc123proofsha",
    )
    monkeypatch.setattr("app.autonomous_loop.execute_live_submission", mock_sub)

    # Enable live submission on DB and settings
    set_flag(test_db, "allow_live_submission", True, "testing live run")
    settings = Settings(allow_live_submission=True, application_mode="autonomous")

    runner = AutonomousRunner(
        db=test_db,
        settings=settings,
        config=AutonomousConfig(
            allow_live_submission=True,
            min_score_threshold=70.0,
            profile_data=candidate_profile,
            output_dir=tmp_path,
        ),
        form_provider=mock_provider,
    )

    summary = runner.run()
    assert summary.submitted_live == 1
    assert len(summary.records) == 1
    assert summary.records[0].submitted is True
    assert summary.records[0].candidate_id == "CAND-7788"
    assert summary.records[0].evidence_sha256 == "abc123proofsha"

    # Verify job stage updated to submitted
    test_db.refresh(job)
    assert job.stage == PipelineStage.submitted.value

    # Verify event logged
    events = test_db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job.id)).scalars().all()
    assert any("Application submitted autonomously" in e.message for e in events)
    assert any("Follow-up cadence scheduled" in e.message for e in events)


def test_api_autonomous_endpoints(monkeypatch, tmp_path):
    from app import main
    from app.config import Settings, get_settings
    from app.db import Base
    from app.main import app, get_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    KEY = "k" * 40
    settings = Settings(api_key=KEY, database_file=str(tmp_path / "test.db"))
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _get_db():
        with Session() as session:
            yield session

    monkeypatch.setattr(main, "settings", settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _get_db

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/autonomous/status", headers={"X-API-Key": KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert "kill_switch_engaged" in data
    assert "adapters_available" in data
    assert "smartrecruiters" in data["adapters_available"]
    assert "workday" in data["adapters_available"]
    app.dependency_overrides.clear()
