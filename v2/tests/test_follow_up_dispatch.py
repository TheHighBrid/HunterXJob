"""Tests for Follow-up email generation and outbound dispatch."""
from __future__ import annotations

import pytest
from profile_helpers import load_example_profile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.follow_up import approve_follow_up_task, dispatch_follow_up_email
from app.main import app
from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def follow_up_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings(_env_file=None, api_key=KEY)

    with Session() as session:
        load_example_profile(session, verified=True)
        job = Job(
            id="job_test_123",
            source="greenhouse",
            external_id="gh_123",
            title="Senior Fraud Analyst",
            company="Wealthsimple",
            location="Ottawa, ON",
            url="https://boards.greenhouse.io/wealthsimple/jobs/123",
            stage=PipelineStage.submitted.value,
        )
        session.add(job)
        session.flush()

        app_row = Application(
            id="app_test_123",
            job_id=job.id,
            stage=PipelineStage.submitted.value,
        )
        session.add(app_row)

        task = ReviewTask(
            id="task_follow_up_1",
            application_id=app_row.id,
            job_id=job.id,
            reason_code="follow_up_1_due",
            title="Follow-up #1 Due: Wealthsimple - Senior Fraud Analyst",
            detail="Subject: Following up on application for Senior Fraud Analyst\n\nDear Wealthsimple Hiring Team,\n\nChecking in on my application.",
        )
        session.add(task)
        session.commit()

    def _get_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app, client=("127.0.0.1", 50000)), Session
    app.dependency_overrides.clear()


def test_dispatch_follow_up_email_dry_run(follow_up_env):
    """A dry run of an approved follow-up reports success but never resolves the task or logs a send."""
    _client, Session = follow_up_env
    with Session() as db:
        approve_follow_up_task(db, "task_follow_up_1", "talent@wealthsimple.com")
        res = dispatch_follow_up_email(
            db=db,
            task_id="task_follow_up_1",
            recipient_email="talent@wealthsimple.com",
            dry_run=True,
        )
        assert res["status"] == "dry_run"
        assert res["sent"] is False
        assert res["recipient"] == "talent@wealthsimple.com"

        # Huncho's rule: a dry run never resolves the task (it stays approved, unsent).
        task = db.get(ReviewTask, "task_follow_up_1")
        assert task.status == "approved"
        assert task.resolved_at is None

        # No "dispatched"/"sent" event is logged when nothing was sent.
        messages = [e.message for e in db.query(PipelineEvent).filter_by(job_id="job_test_123")]
        assert not any("dispatched" in m or "sent" in m.lower() for m in messages)


def test_dispatch_follow_up_api_route(follow_up_env):
    """Verify POST /api/follow-ups/{task_id}/dispatch endpoint."""
    client, Session = follow_up_env
    with Session() as db:
        approve_follow_up_task(db, "task_follow_up_1", "recruiting@wealthsimple.com")
    payload = {
        "recipient_email": "recruiting@wealthsimple.com",
        "dry_run": True,
    }
    response = client.post("/api/follow-ups/task_follow_up_1/dispatch", json=payload, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run"
    assert data["recipient"] == "recruiting@wealthsimple.com"


def test_cloud_captcha_solver_initialization():
    """Verify CloudCaptchaSolver configuration and provider mappings."""
    from app.captcha_bridge import CloudCaptchaSolver

    solver_2cap = CloudCaptchaSolver(api_key="key_2cap", provider="2captcha")
    assert solver_2cap.api_key == "key_2cap"
    assert "2captcha.com" in solver_2cap.base_url

    solver_cap = CloudCaptchaSolver(api_key="key_capsolver", provider="capsolver")
    assert solver_cap.api_key == "key_capsolver"
    assert "capsolver.com" in solver_cap.base_url
