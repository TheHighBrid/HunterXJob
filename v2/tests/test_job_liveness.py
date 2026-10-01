"""Conservative liveness / closed-listing detection.

Transient errors must never close a posting; a single "gone" answer only
marks it suspect; two definitive answers far enough apart close it.
"""
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.flags import ensure_flags
from app.job_liveness import (
    GONE,
    LIVE,
    UNKNOWN,
    CheckContext,
    Observation,
    due_jobs,
    ensure_live,
    hint_missing_from_feed,
    probe_posting,
    record_observation,
    run_due_checks,
)
from app.models import Application, Job, LivenessCheck, PipelineStage, ReviewTask
from app.pipeline import execute_apply
from app.review_queue import open_task

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
ASHBY_ID = "9b99caed-e385-574f-94f7-4be75f67782d"
LEVER_ID = "ed663b5f-1b13-5fb7-853b-e4cdf805c6dd"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    ensure_flags(db)
    return db


def _settings(**overrides):
    return Settings(_env_file=None, automation_enabled=True, **overrides)


def _job(db, stage=PipelineStage.shortlisted, *, source="greenhouse", board="maplebank", external_id="101", url=None):
    job = Job(source=source, board=board, company=board, external_id=external_id, title="Bilingual Fraud Analyst",
              location="Toronto", description="d", stage=stage.value, platform=source,
              url=url or f"https://job-boards.greenhouse.io/{board}/jobs/{external_id}", liveness_failures=0)
    db.add(job)
    db.commit()
    return job


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


GONE_OBS = Observation(GONE, "ats_api_not_found", 404, "posting does not exist")
LIVE_OBS = Observation(LIVE, "ats_api_ok", 200, "ok")
FLAKY = Observation(UNKNOWN, "http_503", 503, "busy")


# --- probes ------------------------------------------------------------------------------------

@pytest.mark.parametrize("status, payload, outcome, signal", [
    (200, {"id": 101, "title": "x"}, LIVE, "ats_api_ok"),
    (404, {"status": 404}, GONE, "ats_api_not_found"),
    (410, {}, GONE, "ats_api_not_found"),
    (500, {}, UNKNOWN, "http_500"),
    (429, {}, UNKNOWN, "http_429"),
    (403, {}, UNKNOWN, "http_403"),
    (301, {}, UNKNOWN, "http_301"),
    (200, {"unexpected": True}, UNKNOWN, "unexpected_payload"),
])
def test_greenhouse_probe_reads_the_public_job_endpoint(status, payload, outcome, signal):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    observation = probe_posting(_job(_db()), client=_client(handler))
    assert (observation.outcome, observation.signal) == (outcome, signal)
    assert [(r.method, r.url.host, r.url.path) for r in seen] == [("GET", "boards-api.greenhouse.io", "/v1/boards/maplebank/jobs/101")]


def test_network_errors_and_non_json_are_unknown():
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    assert probe_posting(_job(_db()), client=_client(boom)).signal == "network_error"
    assert probe_posting(_job(_db()), client=_client(lambda r: httpx.Response(200, text="<html>"))).signal == "non_json"


def test_lever_probe_uses_the_postings_api():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(404, json={"ok": False, "error": "Document not found"})

    job = _job(_db(), source="lever", board="northwind", external_id=LEVER_ID, url=f"https://jobs.lever.co/northwind/{LEVER_ID}")
    assert probe_posting(job, client=_client(handler)).outcome == GONE
    assert str(seen[0].url) == f"https://api.lever.co/v0/postings/northwind/{LEVER_ID}"


@pytest.mark.parametrize("payload, outcome, signal", [
    ({"data": {"jobPosting": {"id": ASHBY_ID, "title": "x", "isListed": True}}}, LIVE, "ashby_posting_ok"),
    ({"data": {"jobPosting": None}}, GONE, "ashby_posting_null"),
    ({"errors": [{"message": "rate limited"}], "data": None}, UNKNOWN, "graphql_error"),
])
def test_ashby_probe_uses_a_read_only_graphql_get(payload, outcome, signal):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    job = _job(_db(), source="ashby", board="northwind", external_id=ASHBY_ID, url=f"https://jobs.ashbyhq.com/northwind/{ASHBY_ID}")
    observation = probe_posting(job, client=_client(handler))
    assert (observation.outcome, observation.signal) == (outcome, signal)
    assert seen[0].method == "GET" and "mutation" not in seen[0].url.params["query"]


@pytest.mark.parametrize("status, body, outcome", [
    (200, "<h1>Analyst</h1><button>Apply now</button>", LIVE),
    (200, "<p>This role is no longer accepting applications.</p>", GONE),
    (404, "Not found", GONE),
    (200, "<p>Please verify you are human</p>", UNKNOWN),
    (200, "<p>Welcome to our careers site</p>", UNKNOWN),
    (502, "Bad gateway", UNKNOWN),
])
def test_generic_page_probe_needs_explicit_signals(status, body, outcome):
    job = _job(_db(), source="feed", board="x", url="https://careers.example.com/jobs/1")
    assert probe_posting(job, client=_client(lambda r: httpx.Response(status, text=body))).outcome == outcome


# --- policy ----------------------------------------------------------------------------------

def test_transient_errors_never_close_and_back_off_exponentially():
    db = _db()
    settings = _settings()
    job = _job(db)
    delays = []
    for attempt in range(12):
        now = T0 + timedelta(hours=attempt * 30)
        record_observation(db, settings, job, FLAKY, CheckContext("scheduled", now=now))
        delays.append(job.liveness_next_check_at.replace(tzinfo=UTC) - now)
    assert job.stage == PipelineStage.shortlisted.value and job.closed_at is None
    assert job.liveness == UNKNOWN and job.liveness_failures == 12
    assert delays[:4] == [timedelta(minutes=30), timedelta(minutes=60), timedelta(minutes=120), timedelta(minutes=240)]
    assert max(delays) == timedelta(hours=24)
    assert db.execute(select(LivenessCheck)).scalars().all().__len__() == 12
    # One non-blocking review task after five inconclusive checks, never more.
    tasks = db.execute(select(ReviewTask).where(ReviewTask.reason_code == "liveness_review")).scalars().all()
    assert len(tasks) == 1 and tasks[0].status == "open"
    assert job.stage == PipelineStage.shortlisted.value


def test_one_gone_answer_only_marks_the_posting_suspect():
    db = _db()
    job = _job(db)
    check = record_observation(db, _settings(), job, GONE_OBS, CheckContext("scheduled", now=T0))
    assert check.action == "suspect" and job.liveness == "suspect"
    assert job.stage == PipelineStage.shortlisted.value
    assert job.liveness_next_check_at.replace(tzinfo=UTC) == T0 + timedelta(minutes=30)


def test_a_second_gone_answer_inside_the_window_does_not_confirm():
    db = _db()
    job = _job(db)
    record_observation(db, _settings(), job, GONE_OBS, CheckContext("scheduled", now=T0))
    check = record_observation(db, _settings(), job, GONE_OBS, CheckContext("pre_dry_run", now=T0 + timedelta(minutes=5)))
    assert check.action == "awaiting_confirmation" and job.stage == PipelineStage.shortlisted.value


def test_two_definitive_answers_close_the_posting_and_its_review_tasks():
    db = _db()
    job = _job(db, PipelineStage.materials_generated)
    application = Application(job_id=job.id, mode="dry_run", stage=PipelineStage.materials_generated.value)
    db.add(application)
    db.commit()
    open_task(db, reason_code="material_review", title="Review drafts", job=job, application=application, hold=False)
    settings = _settings()
    record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0))
    # A transient error in between neither confirms nor clears the suspicion.
    record_observation(db, settings, job, FLAKY, CheckContext("scheduled", now=T0 + timedelta(minutes=20)))
    assert job.liveness == "suspect"
    check = record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0 + timedelta(minutes=45)))
    assert check.action == "closed"
    assert job.stage == PipelineStage.closed.value and job.liveness == "closed" and job.closed_at is not None
    assert application.stage == PipelineStage.closed.value
    task = db.execute(select(ReviewTask).where(ReviewTask.job_id == job.id)).scalar_one()
    assert task.status == "auto_closed" and "posting closed" in task.detail


def test_live_answer_clears_suspicion_and_reopens_closed_postings():
    db = _db()
    settings = _settings()
    job = _job(db)
    record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0))
    record_observation(db, settings, job, LIVE_OBS, CheckContext("scheduled", now=T0 + timedelta(minutes=40)))
    assert job.liveness == LIVE and job.liveness_failures == 0
    record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0 + timedelta(hours=2)))
    record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0 + timedelta(hours=3)))
    assert job.stage == PipelineStage.closed.value
    check = record_observation(db, settings, job, LIVE_OBS, CheckContext("manual", now=T0 + timedelta(hours=5)))
    assert check.action == "reopened" and job.stage == PipelineStage.discovered.value and job.closed_at is None


def test_in_flight_postings_are_never_closed():
    db = _db()
    job = _job(db, PipelineStage.applying)
    settings = _settings()
    record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0))
    check = record_observation(db, settings, job, GONE_OBS, CheckContext("scheduled", now=T0 + timedelta(hours=1)))
    assert check.action == "close_blocked" and job.stage == PipelineStage.applying.value


def test_ensure_live_reuses_a_fresh_result_and_defers_anything_else():
    db = _db()
    settings = _settings()
    job = _job(db)
    calls = []

    def probe(item):
        calls.append(item.id)
        return LIVE_OBS

    assert ensure_live(db, settings, job, action="prepare", ctx=CheckContext(probe=probe, now=T0)).allowed
    assert ensure_live(db, settings, job, action="prepare", ctx=CheckContext(probe=probe, now=T0 + timedelta(hours=1))).action == "cached"
    assert ensure_live(db, settings, job, action="dry_run", ctx=CheckContext(probe=probe, now=T0 + timedelta(hours=1))).allowed
    assert len(calls) == 2
    decision = ensure_live(db, settings, job, action="dry_run", ctx=CheckContext(probe=lambda item: FLAKY, now=T0 + timedelta(hours=2)))
    assert not decision.allowed and decision.status == UNKNOWN
    decision = ensure_live(db, settings, job, action="dry_run", ctx=CheckContext(probe=lambda item: GONE_OBS, now=T0 + timedelta(hours=3)))
    assert not decision.allowed and decision.status == "suspect"


def test_due_jobs_only_covers_queued_stages_and_respects_the_schedule():
    db = _db()
    queued = _job(db, PipelineStage.ready_to_apply, external_id="1")
    _job(db, PipelineStage.discovered, external_id="2")
    _job(db, PipelineStage.submitted, external_id="3")
    later = _job(db, PipelineStage.shortlisted, external_id="4")
    later.liveness_next_check_at = T0 + timedelta(hours=1)
    db.commit()
    assert [job.id for job in due_jobs(db, T0, 10)] == [queued.id]
    summary = run_due_checks(db, _settings(), CheckContext(probe=lambda item: LIVE_OBS, now=T0))
    assert summary["checked"] == 1 and summary["live"] == 1


def test_missing_from_a_complete_feed_only_schedules_a_check():
    db = _db()
    job = _job(db, PipelineStage.ready_to_apply)
    job.canonical_id = "greenhouse:maplebank:101"
    job.liveness_next_check_at = T0 + timedelta(hours=20)
    db.commit()
    assert hint_missing_from_feed(db, "greenhouse", "maplebank", {"greenhouse:maplebank:999"}, now=T0) == 1
    assert job.stage == PipelineStage.ready_to_apply.value
    assert job.liveness_next_check_at.replace(tzinfo=UTC) == T0
    assert hint_missing_from_feed(db, "greenhouse", "maplebank", {"greenhouse:maplebank:101"}, now=T0) == 0


# --- pipeline integration --------------------------------------------------------------------

def _ready(db):
    job = _job(db, PipelineStage.ready_to_apply)
    application = Application(job_id=job.id, mode="dry_run", stage=PipelineStage.ready_to_apply.value)
    db.add(application)
    db.commit()
    return job, application


def test_dry_run_is_deferred_when_the_posting_cannot_be_confirmed_live(monkeypatch):
    from app import job_liveness

    monkeypatch.setattr(job_liveness, "probe_posting", lambda job, **kwargs: FLAKY)
    db = _db()
    job, application = _ready(db)

    class NoForms:
        def fetch(self, job):
            raise AssertionError("the form must not be fetched for a posting that is not confirmed live")

    result = execute_apply(db, _settings(), application.id, form_provider=NoForms())
    assert result["status"] == "deferred" and result["reason"] == "liveness_unknown" and result["submitted"] is False
    assert job.stage == PipelineStage.ready_to_apply.value


def test_dry_run_refuses_linked_duplicates():
    db = _db()
    primary = _job(db, PipelineStage.shortlisted, external_id="1")
    job, application = _ready(db)
    job.duplicate_of_id = primary.id
    db.commit()
    with pytest.raises(RuntimeError, match="duplicate of"):
        execute_apply(db, _settings(), application.id)
