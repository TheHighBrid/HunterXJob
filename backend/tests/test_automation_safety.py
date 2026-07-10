"""Daily cap, inter-application delay, and blacklist enforcement."""
import datetime as dt

from app.models import Application, JobPosting
from app.services import safety


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
    from app.config import get_settings

    settings = get_settings()
    settings.MAX_APPLICATIONS_PER_DAY = 2

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(minutes=10))
    _make_submitted_application(db_session, job, now - dt.timedelta(minutes=5))

    assert safety.enforce_daily_cap(db_session, settings) is False


def test_daily_cap_allows_under_limit(db_session):
    from app.config import get_settings

    settings = get_settings()
    settings.MAX_APPLICATIONS_PER_DAY = 5

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(minutes=10))

    assert safety.enforce_daily_cap(db_session, settings) is True


def test_delay_blocks_when_too_soon(db_session):
    from app.config import get_settings

    settings = get_settings()
    settings.MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS = 90

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(seconds=10))

    assert safety.enforce_delay(db_session, settings) is False


def test_delay_allows_after_elapsed(db_session):
    from app.config import get_settings

    settings = get_settings()
    settings.MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS = 90

    job = _make_job(db_session)
    now = dt.datetime.utcnow()
    _make_submitted_application(db_session, job, now - dt.timedelta(seconds=200))

    assert safety.enforce_delay(db_session, settings) is True


def test_delay_allows_when_no_prior_submissions(db_session):
    from app.config import get_settings

    settings = get_settings()
    assert safety.enforce_delay(db_session, settings) is True


def test_blacklist_exact_and_substring_match():
    from app.config import get_settings

    settings = get_settings()
    settings.BLACKLISTED_COMPANIES = "evilcorp, badactor.com"

    assert safety.is_blacklisted("EvilCorp", settings) is True
    assert safety.is_blacklisted("Subsidiary of BadActor.com", settings) is True
    assert safety.is_blacklisted("GoodCorp", settings) is False


def test_can_submit_now_combines_all_checks(db_session):
    from app.config import get_settings

    settings = get_settings()
    settings.BLACKLISTED_COMPANIES = "evilcorp"
    settings.MAX_APPLICATIONS_PER_DAY = 15
    settings.MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS = 90

    allowed, reason = safety.can_submit_now(db_session, settings, "EvilCorp")
    assert allowed is False
    assert "blacklisted" in reason

    allowed, reason = safety.can_submit_now(db_session, settings, "GoodCorp")
    assert allowed is True
    assert reason == ""
