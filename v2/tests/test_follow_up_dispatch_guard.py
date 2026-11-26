"""Fail-closed follow-up dispatch (Bot 03).

Every test runs with smtplib.SMTP replaced by a recorder and socket connections
blocked, so no network is ever opened. Unless a test explicitly opts in with
``smtp_spy.allow_send = True`` (the approved, hash-matched, explicit-live case),
teardown fails the test if anything opened an SMTP connection.
"""
from __future__ import annotations

import smtplib
import socket
from typing import ClassVar

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app import follow_up
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.follow_up import dispatch_follow_up_email
from app.main import app
from app.models import Application, Job, PipelineEvent, PipelineStage, ReviewTask

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}
TASK = "task_follow_up_1"
RECIPIENT = "talent@wealthsimple.com"
SMTP = {"smtp_host": "smtp.invalid", "smtp_port": 2525}


@pytest.fixture
def env():
    """In-memory DB with one submitted application and one open follow-up ReviewTask."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.add(Job(id="job_test_123", source="greenhouse", external_id="gh_123",
                        title="Senior Fraud Analyst", company="Wealthsimple", location="Ottawa, ON",
                        url="https://boards.greenhouse.io/wealthsimple/jobs/123",
                        stage=PipelineStage.submitted.value))
        session.flush()
        session.add(Application(id="app_test_123", job_id="job_test_123", stage=PipelineStage.submitted.value))
        session.add(ReviewTask(
            id=TASK, application_id="app_test_123", job_id="job_test_123", reason_code="follow_up_1_due",
            title="Follow-up #1 Due: Wealthsimple - Senior Fraud Analyst",
            detail="Subject: Following up on application for Senior Fraud Analyst\n\n"
                   "Dear Wealthsimple Hiring Team,\n\nChecking in on my application.",
        ))
        session.commit()

    def _get_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_key=KEY)
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app, client=("127.0.0.1", 50000)), Session
    app.dependency_overrides.clear()


class _RecordingSMTP:
    connections: ClassVar[list[tuple]] = []
    sent: ClassVar[list] = []
    allow_send: ClassVar[bool] = False

    def __init__(self, *args, **kwargs):
        _RecordingSMTP.connections.append(args)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        return None

    def login(self, *args):
        return None

    def send_message(self, msg):
        _RecordingSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def smtp_spy(monkeypatch):
    _RecordingSMTP.connections = []
    _RecordingSMTP.sent = []
    _RecordingSMTP.allow_send = False
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _RecordingSMTP)

    def _no_network(*args, **kwargs):
        raise AssertionError("network connection attempted during a follow-up test")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    yield _RecordingSMTP
    if not _RecordingSMTP.allow_send:
        assert _RecordingSMTP.connections == [], "SMTP opened without an approved, explicit live send"


def _approve(db, recipient=RECIPIENT):
    """Approve through the supervised helper; on main (no helper) only the status can be set."""
    helper = getattr(follow_up, "approve_follow_up_task", None)
    if helper is not None:
        return helper(db, TASK, recipient)
    task = db.get(ReviewTask, TASK)
    task.status = "approved"
    db.commit()
    return None


def _task(db):
    db.expire_all()
    return db.get(ReviewTask, TASK)


def _sent_events(db):
    rows = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == "job_test_123")).scalars()
    return [e.message for e in rows if "dispatched" in e.message or "sent" in e.message.lower()]


def _send(db, **kw):
    return dispatch_follow_up_email(db=db, task_id=TASK, **SMTP, **kw)


def test_unapproved_task_cannot_send(env, smtp_spy):
    _, Session = env
    with Session() as db:
        res = _send(db, recipient_email=RECIPIENT, dry_run=False)
        assert res["status"] == "refused"
        assert res["reason"] == "follow_up_not_approved"
        assert smtp_spy.connections == []
        assert _task(db).status == "open"
        assert _sent_events(db) == []


def test_payload_changed_after_approval_cannot_send(env, smtp_spy):
    _, Session = env
    with Session() as db:
        _approve(db)
        task = _task(db)
        task.detail = task.detail.replace("Checking in on my application.", "Edited after approval.")
        db.commit()
        res = _send(db, recipient_email=RECIPIENT, dry_run=False)
        assert res["status"] == "refused"
        assert res["reason"] == "payload_changed_since_approval"
        assert smtp_spy.connections == []
        assert _task(db).status != "resolved"


def test_recipient_changed_after_approval_cannot_send(env, smtp_spy):
    _, Session = env
    with Session() as db:
        _approve(db)
        res = _send(db, recipient_email="someone.else@wealthsimple.com", dry_run=False)
        assert res["status"] == "refused"
        assert res["reason"] == "payload_changed_since_approval"
        assert smtp_spy.connections == []


def test_status_approved_without_hash_cannot_send(env, smtp_spy):
    _, Session = env
    with Session() as db:
        task = _task(db)
        task.status = "approved"
        db.commit()
        res = _send(db, recipient_email=RECIPIENT, dry_run=False)
        assert res["status"] == "refused"
        assert res["reason"] == "approval_hash_missing"
        assert smtp_spy.connections == []


@pytest.mark.parametrize("recipient", [None, "", "   "])
def test_missing_recipient_is_refused(env, smtp_spy, recipient):
    _, Session = env
    with Session() as db:
        _approve(db)
        res = _send(db, recipient_email=recipient, dry_run=False)
        assert res["status"] == "refused"
        assert res["reason"] == "recipient_missing"
        assert res.get("recipient") != "hiring-team@employer.com"
        assert smtp_spy.connections == []
        assert _task(db).status != "resolved"


@pytest.mark.parametrize("recipient", [
    "hiring-team@employer.com",
    "jobs@example.com",
    "hr@mail.example.org",
    "noreply@wealthsimple.com",
    "no-reply@wealthsimple.com",
    "donotreply@wealthsimple.com",
    "not-an-email",
    "a@b",
    "x@y.com\r\nBcc: evil@attacker.io",
])
def test_placeholder_or_malformed_recipient_is_refused(env, smtp_spy, recipient):
    _, Session = env
    with Session() as db:
        _approve(db)
        res = _send(db, recipient_email=recipient, dry_run=False)
        assert res["status"] == "refused"
        assert res["reason"] in {"recipient_placeholder", "recipient_invalid"}
        assert smtp_spy.connections == []
        assert _task(db).status != "resolved"


def test_default_call_is_dry_run_and_never_touches_smtp(env, smtp_spy):
    _, Session = env
    with Session() as db:
        _approve(db)
        res = _send(db, recipient_email=RECIPIENT)
        assert res["status"] == "dry_run"
        assert res["sent"] is False
        assert smtp_spy.connections == []


def test_api_default_is_dry_run_and_never_touches_smtp(env, smtp_spy, monkeypatch):
    client, Session = env
    with Session() as db:
        _approve(db)
    from app.config import Settings
    monkeypatch.setattr(Settings, "smtp_host", "smtp.invalid", raising=False)
    response = client.post(f"/api/follow-ups/{TASK}/dispatch", json={"recipient_email": RECIPIENT}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "dry_run"
    assert smtp_spy.connections == []


def test_api_refuses_unapproved_with_409_and_placeholder_with_422(env, smtp_spy):
    client, Session = env
    url = f"/api/follow-ups/{TASK}/dispatch"
    unapproved = client.post(url, json={"recipient_email": RECIPIENT, "dry_run": False}, headers=HEADERS)
    assert unapproved.status_code == 409
    with Session() as db:
        _approve(db)
    placeholder = client.post(url, json={"recipient_email": "hiring-team@employer.com", "dry_run": False}, headers=HEADERS)
    assert placeholder.status_code == 422
    with Session() as db:
        assert _task(db).status != "resolved"
        assert _sent_events(db) == []
    assert smtp_spy.connections == []


@pytest.mark.parametrize("approved", [False, True])
def test_refused_or_dry_run_never_resolves_or_logs_a_send(env, smtp_spy, approved):
    _, Session = env
    with Session() as db:
        if approved:
            _approve(db)
            _send(db, recipient_email=RECIPIENT, dry_run=True)
        else:
            _send(db, recipient_email=RECIPIENT, dry_run=False)
        task = _task(db)
        assert task.status != "resolved"
        assert task.resolved_at is None
        assert _sent_events(db) == []
        assert smtp_spy.connections == []


def test_live_send_without_smtp_host_is_refused(env, smtp_spy):
    _, Session = env
    with Session() as db:
        _approve(db)
        res = dispatch_follow_up_email(db=db, task_id=TASK, recipient_email=RECIPIENT, dry_run=False)
        assert res["status"] == "refused"
        assert _task(db).status != "resolved"
        assert _sent_events(db) == []


def test_approved_matching_hash_explicit_live_sends_and_resolves(env, smtp_spy):
    _, Session = env
    with Session() as db:
        payload_hash = _approve(db)
        assert payload_hash is not None, "no supervised approval helper binds a payload hash"
        smtp_spy.allow_send = True
        res = _send(db, recipient_email=RECIPIENT, dry_run=False)
        assert res["status"] == "dispatched"
        assert res["sent"] is True
        assert smtp_spy.connections == [("smtp.invalid", 2525)]
        assert len(smtp_spy.sent) == 1
        assert smtp_spy.sent[0]["To"] == RECIPIENT
        assert "Approved-Payload-SHA256" not in smtp_spy.sent[0].get_payload()
        task = _task(db)
        assert task.status == "resolved"
        assert task.resolved_at is not None
        assert len(_sent_events(db)) == 1
