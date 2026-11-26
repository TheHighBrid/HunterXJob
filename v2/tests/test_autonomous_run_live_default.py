"""Red tests (Bot 02): /api/autonomous/run must treat a missing `live` as dry run.

Worst case: env allow_live_submission=True AND the DB flag on (the .env auto-flip
path in main.py startup). A request without `live` must still build the runner with
allow_live_submission=False. An explicit live=true keeps today's behaviour (control).
The runner is replaced by a spy, so nothing runs, launches a browser, or hits the network.
"""
from __future__ import annotations

from types import SimpleNamespace

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


class _SpyRunner:
    configs: list = []

    def __init__(self, db, settings, config):
        _SpyRunner.configs.append(config)

    def run(self):
        return SimpleNamespace(
            started_at="t0", finished_at="t1", aborted_by_kill_switch=False,
            candidates_considered=0, shortlisted=0, submitted_live=0,
            verified_dry_runs=0, skipped_duplicates=0, errors=[],
        )


@pytest.fixture
def live_armed_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings(_env_file=None, api_key=KEY, allow_live_submission=True)
    with Session() as db:
        ensure_flags(db)
        set_flag(db, "allow_live_submission", True, "test: worst-case armed")
        db.commit()

    def _get_db():
        with Session() as session:
            yield session

    _SpyRunner.configs = []
    monkeypatch.setattr(autonomous_loop, "AutonomousRunner", _SpyRunner)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app, client=("127.0.0.1", 50000))
    app.dependency_overrides.clear()


def test_missing_live_is_dry_run_even_when_armed(live_armed_client):
    res = live_armed_client.post("/api/autonomous/run", headers=HEADERS)
    assert res.status_code == 200, res.text
    assert len(_SpyRunner.configs) == 1
    assert _SpyRunner.configs[0].allow_live_submission is False, "missing `live` ran LIVE"


def test_explicit_live_false_is_dry_run(live_armed_client):
    live_armed_client.post("/api/autonomous/run?live=false", headers=HEADERS)
    assert _SpyRunner.configs[0].allow_live_submission is False


def test_explicit_live_true_still_honoured_when_armed(live_armed_client):
    """Control: the fix must not break the deliberate, fully-armed live path."""
    live_armed_client.post("/api/autonomous/run?live=true", headers=HEADERS)
    assert _SpyRunner.configs[0].allow_live_submission is True
