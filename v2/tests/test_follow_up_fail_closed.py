"""Red tests (Bot 02): follow-up dispatch must fail closed.

Contract, agnostic to how refusal is signalled (an exception OR a non-sent status):
  1. No SMTP connection without an approved ReviewTask.
  2. No SMTP connection to a missing/placeholder recipient.
  3. The task is never marked resolved when nothing was sent.
  4. dispatch_follow_up_email defaults to dry_run=True.
  5. The API endpoint defaults to dry_run=True.
Uses the follow_up_env fixture from test_follow_up_dispatch.py (copy or move to conftest).
"""
from __future__ import annotations

import inspect
import smtplib
from typing import ClassVar

import pytest
from test_follow_up_dispatch import follow_up_env as _shared_follow_up_env  # noqa: F401  (registers the shared fixture)

from app.follow_up import dispatch_follow_up_email
from app.main import FollowUpDispatchIn
from app.models import ReviewTask

TASK = "task_follow_up_1"
PLACEHOLDERS = (None, "", "hiring-team@employer.com")


@pytest.fixture
def follow_up_env(request):
    """Same fixture as test_follow_up_dispatch.follow_up_env, re-exposed under its original name."""
    return request.getfixturevalue("_shared_follow_up_env")


class _RecordingSMTP:
    connections: ClassVar[list[tuple]] = []

    def __init__(self, *args, **kwargs):
        _RecordingSMTP.connections.append(args)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        pass


@pytest.fixture
def smtp_spy(monkeypatch):
    _RecordingSMTP.connections = []
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    return _RecordingSMTP


def _call(db, **kw):
    try:
        return dispatch_follow_up_email(db=db, task_id=TASK, smtp_host="smtp.invalid", **kw)
    except (ValueError, PermissionError, RuntimeError) as exc:
        return {"status": "refused", "error": str(exc)}


def test_unapproved_task_never_sends(follow_up_env, smtp_spy):
    _, Session = follow_up_env
    with Session() as db:
        assert db.get(ReviewTask, TASK).status != "approved"
        res = _call(db, recipient_email="talent@example.org", dry_run=False)
        assert smtp_spy.connections == [], "SMTP opened for an unapproved follow-up"
        assert res["status"] != "dispatched"
        assert db.get(ReviewTask, TASK).status != "resolved"


@pytest.mark.parametrize("recipient", PLACEHOLDERS)
def test_placeholder_recipient_never_sends(follow_up_env, smtp_spy, recipient):
    _, Session = follow_up_env
    with Session() as db:
        res = _call(db, recipient_email=recipient, dry_run=False)
        assert smtp_spy.connections == [], f"SMTP opened for recipient {recipient!r}"
        assert res["status"] != "dispatched"
        assert res.get("recipient") != "hiring-team@employer.com"
        assert db.get(ReviewTask, TASK).status != "resolved"


def test_function_defaults_to_dry_run():
    default = inspect.signature(dispatch_follow_up_email).parameters["dry_run"].default
    assert default is True


def test_api_payload_defaults_to_dry_run():
    assert FollowUpDispatchIn().dry_run is True
