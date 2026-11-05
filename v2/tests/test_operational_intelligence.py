"""Unit tests for failure taxonomy diagnostics and adapter health monitoring."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.adapter_health import calculate_platform_health
from app.db import Base
from app.diagnostics import FailureCategory, classify_failure, scrub_pii
from app.models import Application, Job, PipelineStage, ReviewTask


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


# --- Diagnostics & PII Scrubbing Tests ---------------------------------------

def test_scrub_pii_redacts_sensitive_data():
    raw = (
        "Failed to submit for user mohamed.alem@example.com with phone 613-555-0199 "
        "using Bearer ya29.a0AfH6SM... and api_key=AIzaSyD9876543210123"
    )
    scrubbed = scrub_pii(raw)
    assert "mohamed.alem@example.com" not in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed
    assert "613-555-0199" not in scrubbed
    assert "[REDACTED_PHONE]" in scrubbed
    assert "ya29" not in scrubbed
    assert "[REDACTED_TOKEN]" in scrubbed
    assert "AIzaSy" not in scrubbed
    assert "[REDACTED_KEY]" in scrubbed


@pytest.mark.parametrize("error_text, http_status, expected_cat, human_req", [
    ("Encountered interactive Cloudflare Turnstile challenge on submit", None, FailureCategory.CAPTCHA_CHALLENGE, True),
    ("DataDome blocked automated request", 403, FailureCategory.CAPTCHA_CHALLENGE, True),
    ("Please login with your password or enter MFA code", None, FailureCategory.AUTHENTICATION_REQUIREMENT, True),
    ("Navigation timeout of 30000ms exceeded while waiting for selector", None, FailureCategory.PAGE_LOADING_TIMEOUT, False),
    ("Upstream gateway error", 502, FailureCategory.NETWORK_FAILURE, False),
    ("Form field validation rejected: required field missing", None, FailureCategory.VALIDATION_FAILURE, True),
    ("Locator selector '#apply-btn' did not resolve in DOM", None, FailureCategory.MISSING_OR_CHANGED_SELECTOR, False),
    ("Resume file upload rejected: attachment size exceeded", None, FailureCategory.FILE_UPLOAD_FAILURE, False),
])
def test_classify_failure_taxonomy(error_text, http_status, expected_cat, human_req):
    rec = classify_failure(error_text, platform="ashby", http_status=http_status)
    assert rec.category is expected_cat
    assert rec.human_intervention_required is human_req
    assert rec.platform == "ashby"
    assert rec.timestamp is not None


# --- Adapter Health Monitoring Tests -----------------------------------------

def test_calculate_platform_health_metrics(db_session):
    # 4 Ashby applications: 3 submitted, 1 uncertain
    for i in range(3):
        job = Job(id=f"ashby-{i}", title="Eng", company="Co", source="ashby", platform="ashby", external_id=f"a-{i}", url=f"https://example.com/a/{i}")
        app = Application(id=f"app-a-{i}", job_id=job.id, stage=PipelineStage.submitted.value)
        db_session.add_all([job, app])

    job_unc = Job(id="ashby-unc", title="Eng", company="Co", source="ashby", platform="ashby", external_id="a-unc", url="https://example.com/a/unc")
    app_unc = Application(id="app-a-unc", job_id=job_unc.id, stage=PipelineStage.submission_uncertain.value)
    db_session.add_all([job_unc, app_unc])

    # 1 ReviewTask for Ashby with CAPTCHA
    task = ReviewTask(
        id="task-1",
        job_id="ashby-unc",
        reason_code="captcha_detected",
        status="open",
        title="CAPTCHA on submit",
    )
    db_session.add(task)
    db_session.commit()

    health = calculate_platform_health(db_session, platform="ashby")
    assert "ashby" in health
    rep = health["ashby"]
    assert rep.total_attempts == 4
    assert rep.successful_submissions == 3
    assert rep.uncertain_outcomes == 1
    assert rep.success_rate == 0.75
    assert rep.health_status == "degraded"  # 75% is degraded (threshold 80%)
    assert rep.captcha_challenges == 1


def test_insufficient_data_status(db_session):
    job = Job(id="sr-1", title="PM", company="BigCo", source="smartrecruiters", platform="smartrecruiters", external_id="sr-1", url="https://example.com/sr/1")
    app = Application(id="app-sr-1", job_id=job.id, stage=PipelineStage.submitted.value)
    db_session.add_all([job, app])
    db_session.commit()

    health = calculate_platform_health(db_session, platform="smartrecruiters")
    assert "smartrecruiters" in health
    # Only 1 attempt -> insufficient data
    assert health["smartrecruiters"].health_status == "insufficient_data"
