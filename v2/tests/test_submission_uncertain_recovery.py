"""Native crash recovery for interrupted live submissions (Wave 3).

A live attempt that dies between the submit click and the outcome being written must
end up in ``submission_uncertain`` with an open review task, and must never be retried
automatically. Offline: the live submit is a stand-in, no browser submit reaches the network.
"""
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.autonomous_loop import (
    LIVE_SUBMIT_STARTED_MARKER,
    AutonomousConfig,
    AutonomousRunner,
    recover_interrupted_live_submissions,
)
from app.config import Settings
from app.db import Base
from app.flags import set_flag
from app.form_engine import ControlType, FormControl
from app.greenhouse_form import RealForm
from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask
from app.submission_engine import SubmissionResult


class SimulatedCrash(BaseException):
    """Stands in for the process dying mid-submit (not an ordinary, catchable Exception)."""


@pytest.fixture
def session_factory(tmp_path):
    # Local import: ruff/Codacy disagree on whether improvement_kernel is first-party.
    from improvement_kernel.storage.models import AgentVersion

    engine = create_engine(f"sqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(AgentVersion(project="hunterxjob", version="v2.0.0-champion", git_commit="initial",
                            prompt_hash="h1", config_hash="c1", status="champion"))
        db.commit()
    return factory


PROFILE = {
    "verified": True,
    "contact": {"first_name": "Jordan", "last_name": "Lee", "email": "jordan.lee@example.com",
                "phone": "+1 613 555 0199", "location": "Ottawa, ON"},
    "summary": "Senior software engineer with 5 years building automation and cloud systems.",
    "work_authorization": [{"country": "CA", "authorized": True, "requires_sponsorship": False, "status": "Citizen"}],
    "employment": [{"id": "techcorp", "employer": "TechCorp", "title": "Senior Automation Engineer",
                    "location": "Ottawa, ON", "start": "2021-01", "current": True,
                    "achievements": [{"text": "Engineered automated browser pipelines with Playwright",
                                      "metrics": ["50%"], "skills": ["Python", "Playwright"]}]}],
    "education": [{"id": "uottawa", "institution": "University of Ottawa", "degree": "B.Sc. Computer Science",
                   "start": "2016-09", "end": "2020-05"}],
    "skills": [{"name": "Python"}, {"name": "Playwright"}, {"name": "FastAPI"}],
}


def _live_job(db, tmp_path):
    html = tmp_path / "live_form.html"
    html.write_text(
        '<!DOCTYPE html><html><body><form id="f"><input id="first_name" name="first_name" />'
        '<input id="last_name" name="last_name" /><input id="email" name="email" />'
        '<input type="file" id="resume" name="resume" /><button type="submit">Submit</button></form></body></html>',
        encoding="utf-8",
    )
    job = Job(source="greenhouse", external_id="gh_crash_1", url=f"file://{html}", company="Klarna",
              title="Software Engineer", stage=PipelineStage.ready_to_apply.value, final_score=88.0)
    db.add(job)
    db.commit()
    provider = MagicMock()
    provider.fetch.return_value = RealForm(
        platform="greenhouse", source="greenhouse", url=f"file://{html}", title="Software Engineer",
        controls=[
            FormControl(key="first_name", label="First Name", control_type=ControlType.TEXT, required=True, name="first_name"),
            FormControl(key="last_name", label="Last Name", control_type=ControlType.TEXT, required=True, name="last_name"),
            FormControl(key="email", label="Email", control_type=ControlType.EMAIL, required=True, name="email"),
            FormControl(key="resume", label="Resume", control_type=ControlType.FILE, required=True, name="resume"),
        ],
    )
    return job, provider


def _runner(db, tmp_path, provider, *, live=True, stale=0.0):
    if live:
        set_flag(db, "allow_live_submission", True, "testing crash recovery")
    return AutonomousRunner(
        db=db,
        settings=Settings(allow_live_submission=live, application_mode="autonomous"),
        config=AutonomousConfig(allow_live_submission=live, min_score_threshold=70.0, profile_data=PROFILE,
                                output_dir=tmp_path, interrupted_submission_stale_seconds=stale),
        form_provider=provider,
    )


def _stuck_job(db, *, marker=True, age_seconds=3600.0):
    job = Job(source="greenhouse", external_id=f"gh_stuck_{marker}_{age_seconds}", url="https://boards.example/1",
              company="Acme", title="Analyst", stage=PipelineStage.applying.value, final_score=90.0)
    db.add(job)
    db.flush()
    payload = json.dumps({"marker": LIVE_SUBMIT_STARTED_MARKER}) if marker else None
    db.add(PipelineEvent(job_id=job.id, from_stage="ready_to_apply", to_stage=PipelineStage.applying.value,
                         message="started", payload_json=payload,
                         created_at=datetime.now(UTC) - timedelta(seconds=age_seconds)))
    db.commit()
    return job


def test_crash_mid_submit_is_recovered_to_review_and_never_retried(session_factory, tmp_path, monkeypatch):
    crash = AsyncMock(side_effect=SimulatedCrash("process killed mid-submit"))
    monkeypatch.setattr("app.autonomous_loop.execute_live_submission", crash)

    with session_factory() as db:
        job, provider = _live_job(db, tmp_path)
        job_id = job.id
        with pytest.raises(SimulatedCrash):
            _runner(db, tmp_path, provider).run()
        assert crash.await_count == 1

    # The marker was committed before the click, so it survives the crash.
    with session_factory() as db:
        stuck = db.get(Job, job_id)
        assert stuck.stage == PipelineStage.applying.value

    # "Restart": a fresh session and runner.
    resubmit = AsyncMock(return_value=SubmissionResult(status="submitted", success=True, final_url="x",
                                                       confirmation_text="ok", candidate_id="C1"))
    monkeypatch.setattr("app.autonomous_loop.execute_live_submission", resubmit)
    with session_factory() as db:
        summary = _runner(db, tmp_path, MagicMock()).run()
        assert summary.recovered_interrupted == [job_id]
        assert summary.submitted_live == 0
        assert resubmit.await_count == 0, "a recovered uncertain submission must never retry live"

        job = db.get(Job, job_id)
        assert job.stage == PipelineStage.submission_uncertain.value
        app = db.execute(select(Application).where(Application.job_id == job_id)).scalar_one_or_none()
        if app is not None:
            assert app.stage == PipelineStage.submission_uncertain.value
        tasks = db.execute(select(ReviewTask).where(ReviewTask.job_id == job_id, ReviewTask.status == "open")).scalars().all()
        assert len(tasks) == 1
        assert tasks[0].reason_code == "submission_confirmation_uncertain"

        # A second restart is idempotent: nothing new recovered, still one open task.
        again = recover_interrupted_live_submissions(db, stale_after_seconds=0)
        assert again == []
        assert len(db.execute(select(ReviewTask).where(ReviewTask.job_id == job_id)).scalars().all()) == 1


def test_recent_marker_is_left_alone_for_an_in_flight_run(session_factory):
    with session_factory() as db:
        job = _stuck_job(db, age_seconds=5.0)
        assert recover_interrupted_live_submissions(db, stale_after_seconds=900.0) == []
        assert db.get(Job, job.id).stage == PipelineStage.applying.value


def test_applying_without_live_marker_is_not_touched(session_factory):
    with session_factory() as db:
        job = _stuck_job(db, marker=False)
        assert recover_interrupted_live_submissions(db, stale_after_seconds=0) == []
        assert db.get(Job, job.id).stage == PipelineStage.applying.value


def test_stale_marker_moves_to_uncertain_with_review_task(session_factory):
    with session_factory() as db:
        job = _stuck_job(db)
        assert recover_interrupted_live_submissions(db, stale_after_seconds=900.0) == [job.id]
        assert db.get(Job, job.id).stage == PipelineStage.submission_uncertain.value
        events = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job.id)).scalars().all()
        assert any("no automatic retry" in e.message for e in events)
        assert db.execute(select(ReviewTask).where(ReviewTask.job_id == job.id)).scalars().one().status == "open"


def test_dry_run_never_writes_a_live_marker(session_factory, tmp_path, monkeypatch):
    live = AsyncMock()
    monkeypatch.setattr("app.autonomous_loop.execute_live_submission", live)
    with session_factory() as db:
        job, provider = _live_job(db, tmp_path)
        _runner(db, tmp_path, provider, live=False).run()
        assert live.await_count == 0
        events = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == job.id)).scalars().all()
        assert not any(e.payload_json and LIVE_SUBMIT_STARTED_MARKER in e.payload_json for e in events)
