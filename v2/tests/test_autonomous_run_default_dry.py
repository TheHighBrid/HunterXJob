"""POST /api/autonomous/run: a missing `live` means a dry run (Bot 03).

The runner is replaced by a recorder, so nothing runs, no browser starts and no
network is touched. Only the AutonomousConfig the endpoint builds is inspected.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

import app.autonomous_loop as autonomous_loop
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.flags import ensure_flags, set_flag
from app.main import app

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}
URL = "/api/autonomous/run"


class _RecordingRunner:
    configs: ClassVar[list] = []

    def __init__(self, db, settings, config):
        _RecordingRunner.configs.append(config)

    def run(self):
        return SimpleNamespace(
            started_at="t0", finished_at="t1", aborted_by_kill_switch=False, candidates_considered=0,
            shortlisted=0, submitted_live=0, verified_dry_runs=0, skipped_duplicates=0, errors=[],
        )

    def run_job(self, job):  # pragma: no cover - must never be reached from this endpoint
        raise AssertionError("submit path reached from /api/autonomous/run test")


def _client(monkeypatch, settings_live: bool, flag_live: bool) -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        ensure_flags(db)
        set_flag(db, "allow_live_submission", flag_live, "test")
        db.commit()

    def _get_db():
        with Session() as session:
            yield session

    _RecordingRunner.configs = []
    monkeypatch.setattr(autonomous_loop, "AutonomousRunner", _RecordingRunner)
    settings = Settings(_env_file=None, api_key=KEY, allow_live_submission=settings_live)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _run(client: TestClient, query: str = ""):
    response = client.post(URL + query, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert len(_RecordingRunner.configs) == 1
    return _RecordingRunner.configs[0], response.json()


def test_missing_live_is_dry_run_when_fully_armed(monkeypatch):
    """Env allow_live_submission=True and the DB flag on (the .env startup path): no `live` must stay dry."""
    config, body = _run(_client(monkeypatch, settings_live=True, flag_live=True))
    assert config.allow_live_submission is False, "a request without `live` was built as a LIVE run"
    assert body["submitted_live"] == 0


def test_missing_live_with_settings_off_is_dry_run(monkeypatch):
    """allow_live_submission=False in settings already blocks it (main.py `and current.allow_live_submission`)."""
    config, _ = _run(_client(monkeypatch, settings_live=False, flag_live=True))
    assert config.allow_live_submission is False


def test_explicit_live_false_is_dry_run(monkeypatch):
    config, _ = _run(_client(monkeypatch, settings_live=True, flag_live=True), "?live=false")
    assert config.allow_live_submission is False


def test_explicit_live_true_is_still_gated_by_settings_and_flag(monkeypatch):
    armed, _ = _run(_client(monkeypatch, settings_live=True, flag_live=True), "?live=true")
    assert armed.allow_live_submission is True
    blocked, _ = _run(_client(monkeypatch, settings_live=True, flag_live=False), "?live=true")
    assert blocked.allow_live_submission is False
