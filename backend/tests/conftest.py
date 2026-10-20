"""Shared pytest fixtures: temp SQLite DB, TestClient, fake LLM provider."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make `app` importable when pytest is run from the backend/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_API_KEY = "test-api-key-0123456789"


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """Point the app at a fresh temp SQLite DB + known API key for this test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("API_KEY", TEST_API_KEY)
    monkeypatch.setenv("RENDERED_DOCS_DIR", str(tmp_path / "rendered"))
    monkeypatch.setenv("PLAYWRIGHT_STORAGE_STATE_DIR", str(tmp_path / "storage_states"))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("AUTOMATION_DRY_RUN", "true")
    monkeypatch.setenv("MAX_APPLICATIONS_PER_DAY", "15")
    monkeypatch.setenv("MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS", "90")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("IMAP_HOST", "")

    from app.config import get_settings

    get_settings.cache_clear()

    from app import db as db_module

    db_module.reset_engine()
    db_module.init_db()

    yield

    get_settings.cache_clear()


@pytest.fixture()
def db_session(temp_env):
    from app import db as db_module

    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(temp_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        test_client.headers.update({"X-API-Key": TEST_API_KEY})
        yield test_client


@pytest.fixture()
def api_key_header():
    return {"X-API-Key": TEST_API_KEY}


class FakeLLMProvider:
    """A deterministic fake LLM provider for tests - no network calls."""

    def __init__(self, response: str = "This is a generated response tailored to the role."):
        self.response = response
        self.calls: list[tuple[str, str | None]] = []

    def generate(self, prompt: str, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        return self.response


@pytest.fixture()
def fake_llm():
    return FakeLLMProvider()
