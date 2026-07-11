"""Daily cap, inter-application delay, and blacklist enforcement."""
import datetime as dt

from app.models import Application, JobPosting
from app.services import safety
from app.services.runtime_settings import RuntimeSettings


def _runtime(**overrides) -> RuntimeSettings:
    defaults = dict(
        max_applications_per_day=15,
        min_delay_between_applications_seconds=90,
        blacklisted_companies=[],
        automation_dry_run=True,
        automation_enabled=True,
        llm_provider="ollama",
        llm_model="llama3.2",
    )
    defaults.update(overrides)
    return RuntimeSettings(**defaults)


def _make_job(db_session, company="Acme") -> JobPosting:
    job = JobPosting(
        source="greenhouse",
        external_id="1",
        title="Engineer",
        company=company,
        url="https://example.com/job/1",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_submitted_application(db_session, job, submitted_at) -> Application:
    app = Application(
        job_posting_id=job.id,
        status="applied",
        channel="greenhouse",
        submitted_at=submitted_at,
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)
    return app


def test_daily_cap_blocks_after_limit(db_session):
    settings = _runtime(max_applications_per_day=2)

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(minutes=10))
    _make_submitted_application(db_session, job, now - dt.timedelta(minutes=5))

    assert safety.enforce_daily_cap(db_session, settings) is False


def test_daily_cap_allows_under_limit(db_session):
    settings = _runtime(max_applications_per_day=5)

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(minutes=10))

    assert safety.enforce_daily_cap(db_session, settings) is True


def test_delay_blocks_when_too_soon(db_session):
    settings = _runtime(min_delay_between_applications_seconds=90)

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(seconds=10))

    assert safety.enforce_delay(db_session, settings) is False


def test_delay_allows_after_elapsed(db_session):
    settings = _runtime(min_delay_between_applications_seconds=90)

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(seconds=200))

    assert safety.enforce_delay(db_session, settings) is True


def test_delay_allows_when_no_prior_submissions(db_session):
    settings = _runtime()
    assert safety.enforce_delay(db_session, settings) is True


def test_blacklist_exact_and_substring_match():
    settings = _runtime(blacklisted_companies=["evilcorp", "badactor.com"])

    assert safety.is_blacklisted("EvilCorp", settings) is True
    assert safety.is_blacklisted("Subsidiary of BadActor.com", settings) is True
    assert safety.is_blacklisted("GoodCorp", settings) is False


def test_can_submit_now_combines_all_checks(db_session):
    settings = _runtime(
        blacklisted_companies=["evilcorp"],
        max_applications_per_day=15,
        min_delay_between_applications_seconds=90,
    )

    allowed, reason = safety.can_submit_now(db_session, settings, "EvilCorp")
    assert allowed is False
    assert "blacklisted" in reason

    allowed, reason = safety.can_submit_now(db_session, settings, "GoodCorp")
    assert allowed is True
    assert reason == ""


def test_can_submit_now_blocked_when_automation_disabled(db_session):
    settings = _runtime(automation_enabled=False)

    allowed, reason = safety.can_submit_now(db_session, settings, "GoodCorp")
    assert allowed is False
    assert "disabled" in reason
