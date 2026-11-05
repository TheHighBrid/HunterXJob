"""Tests for the HunterXJob Autofill Extension API and portable bundle."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import app
from app.models import AnswerPolicy
from profile_helpers import load_example_profile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def client_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings(_env_file=None, api_key=KEY)

    with Session() as session:
        load_example_profile(session, verified=True)
        session.add(AnswerPolicy(
            key="salary_expectation",
            value="60000",
            source="user",
            scope="global",
            confidence=1.0,
        ))
        session.add(AnswerPolicy(
            key="work_authorization_CA",
            value="Yes",
            source="user",
            scope="global",
            confidence=1.0,
        ))
        session.commit()

    def _get_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app, client=("127.0.0.1", 50000))
    app.dependency_overrides.clear()


def test_autofill_bundle_endpoint(client_env):
    """Verify that /api/autofill/bundle returns full candidate facts and answer vault policies."""
    response = client_env.get("/api/autofill/bundle", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()

    assert "profile" in data
    assert "vault" in data
    assert "version" in data

    profile = data["profile"]
    assert "contact" in profile
    assert profile["contact"]["first_name"]
    assert profile["contact"]["email"]

    vault = data["vault"]
    assert "salary_expectation" in vault
    assert "work_authorization_CA" in vault
    assert vault["work_authorization_CA"]["value"] == "Yes"


def test_autofill_evaluate_endpoint(client_env):
    """Verify field evaluation on common job application questions."""
    payload = {
        "url": "https://jobs.gc.ca/apply/123",
        "platform": "gc_jobs",
        "fields": [
            {
                "id": "fname",
                "name": "firstName",
                "label": "First Name",
                "control_type": "text",
                "options": [],
            },
            {
                "id": "work_auth",
                "name": "work_auth",
                "label": "Are you legally authorized to work in Canada?",
                "control_type": "radio",
                "options": ["Yes", "No"],
            },
            {
                "id": "unknown_field",
                "name": "random_question",
                "label": "What is your favorite astronomy telescope model?",
                "control_type": "text",
                "options": [],
            },
        ],
    }

    response = client_env.post("/api/autofill/evaluate", json=payload, headers=HEADERS)
    assert response.status_code == 200
    result = response.json()

    assert result["fields_evaluated"] == 3
    assert result["fields_matched"] >= 2

    answers = {a["field_id"]: a for a in result["answers"]}

    # First name
    assert answers["fname"]["value"]
    assert "verified_profile" in answers["fname"]["source"]

    # Work Auth in Canada
    assert answers["work_auth"]["value"] == "Yes"
    assert answers["work_auth"]["matched_option"] == "Yes"

    # Unknown field requires review
    assert answers["unknown_field"]["review_required"] is True


def test_autofill_log_application(client_env):
    """Verify that applied jobs can be logged to the SQLite database via the extension."""
    payload = {
        "title": "AML Investigator",
        "company": "Bank of Canada",
        "url": "https://careers.bankofcanada.ca/job/101",
        "platform": "generic",
        "location": "Ottawa, ON",
        "fields_filled_count": 12,
        "notes": "Reviewed and submitted via HunterXJob Portable Extension",
    }

    response = client_env.post("/api/autofill/log-application", json=payload, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "logged"
    assert "job_id" in data
    assert "application_id" in data


def test_bundled_extension_integrity():
    """Verify that extension manifest and data bundles exist and are well-formed."""
    ext_dir = Path(__file__).resolve().parent.parent.parent / "extension"
    assert ext_dir.exists(), "extension directory must exist"

    manifest_path = ext_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert "commands" in manifest
    assert "autofill-active-form" in manifest["commands"]

    # Check default bundled profile
    profile_path = ext_dir / "data" / "default_profile.json"
    assert profile_path.exists()
    pdata = json.loads(profile_path.read_text(encoding="utf-8"))
    assert "contact" in pdata
    assert pdata["contact"]["email"] == "mohamed@melato.ca"

    # Check default bundled vault
    vault_path = ext_dir / "data" / "default_vault.json"
    assert vault_path.exists()
    vdata = json.loads(vault_path.read_text(encoding="utf-8"))
    assert len(vdata) >= 50
