"""Tests for Follow-up email generation and outbound dispatch."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.follow_up import dispatch_follow_up_email, generate_follow_up_draft
from app.main import app
from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask
from profile_helpers import load_example_profile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    """Verify follow-up email dispatch in dry-run mode."""
    client, Session = follow_up_env
    with Session() as db:
        res = dispatch_follow_up_email(
            db=db,
            task_id="task_follow_up_1",
            recipient_email="talent@wealthsimple.com",
            dry_run=True,
        )
        assert res["status"] == "dry_run_dispatched"
        assert res["recipient"] == "talent@wealthsimple.com"

        task = db.get(ReviewTask, "task_follow_up_1")
        assert task.status == "resolved"

        # Event logged
        event = db.query(PipelineEvent).filter_by(job_id="job_test_123").first()
        assert event is not None
        assert "Follow-up email dispatched" in event.message


def test_dispatch_follow_up_api_route(follow_up_env):
    """Verify POST /api/follow-ups/{task_id}/dispatch endpoint."""
    client, Session = follow_up_env
    payload = {
        "recipient_email": "recruiting@wealthsimple.com",
        "dry_run": True,
    }
    response = client.post("/api/follow-ups/task_follow_up_1/dispatch", json=payload, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dry_run_dispatched"
    assert data["recipient"] == "recruiting@wealthsimple.com"


def test_cloud_captcha_solver_initialization():
    """Verify CloudCaptchaSolver configuration and provider mappings."""
    from app.captcha_bridge import CaptchaChallenge, CloudCaptchaSolver

    solver_2cap = CloudCaptchaSolver(api_key="key_2cap", provider="2captcha")
    assert solver_2cap.api_key == "key_2cap"
    assert "2captcha.com" in solver_2cap.base_url

    solver_cap = CloudCaptchaSolver(api_key="key_capsolver", provider="capsolver")
    assert solver_cap.api_key == "key_capsolver"
    assert "capsolver.com" in solver_cap.base_url
