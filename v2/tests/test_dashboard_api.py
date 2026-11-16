from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import Settings, get_settings
from app.db import Base, get_db

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def client_env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings(_env_file=None, api_key=KEY)

    def _get_db():
        with Session() as session:
            yield session

    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[get_db] = _get_db
    yield TestClient(main.app, client=("127.0.0.1", 50000))
    main.app.dependency_overrides.clear()


def test_dashboard_renders_html(client_env):
    response = client_env.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "HunterXJob" in response.text
    assert "Command Center" in response.text
    assert "Review Tasks" in response.text
    assert "Evidence Ledger" in response.text
    assert "Knowledge Book" in response.text
    assert "ATS Platform Adapters & Health Matrix" in response.text
    assert "Truth Guard Claims Inspector" in response.text
    assert "Re-evaluate Against Vault" in response.text
    assert "Batch Approve" in response.text
    assert "select-all-tasks" in response.text


def test_certification_dossier_endpoint(client_env):
    response = client_env.get("/api/certification/dossier", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "evidence_ledger" in data
    assert "coverage_matrix" in data
    assert "milestones" in data
    assert data["candidate"] == "Mohamed Alem"
    assert len(data["evidence_ledger"]["records"]) >= 9


def test_vault_catalog_endpoint(client_env):
    response = client_env.get("/api/vault/catalog", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "catalog_size" in data
    assert data["catalog_size"] > 0
    assert "items" in data
    assert len(data["items"]) == data["catalog_size"]
    assert "candidate" in data
    assert data["candidate"]["name"] == "Mohamed Alem"


def test_inbox_matcher_endpoint(client_env):
    payload = {
        "sender": "recruiting@cohere.com",
        "subject": "Thank you for applying to Cohere",
        "body": "Hi Mohamed, thank you for applying to Cohere! We have received your application.",
    }
    response = client_env.post("/api/inbox/match", json=payload, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert "intent" in data
    assert data["intent"] == "confirmation"


def test_follow_ups_due_endpoint(client_env):
    response = client_env.get("/api/follow-ups/due", headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_job_artifacts_endpoints(client_env, tmp_path):
    from pathlib import Path
    from app.config import Settings, get_settings

    # Create dummy artifact directory for job-123
    job_dir = tmp_path / "job_test-123"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "post_submit.png").write_bytes(b"\x89PNG\r\n\x1a\nDummyPNG")

    settings = Settings(_env_file=None, api_key=KEY, artifact_dir=str(tmp_path))
    main.app.dependency_overrides[get_settings] = lambda: settings

    # List artifacts
    res = client_env.get("/api/jobs/test-123/artifacts", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "artifacts" in data
    assert any(a["name"] == "post_submit.png" for a in data["artifacts"])

    # Fetch artifact file
    res_file = client_env.get("/api/jobs/test-123/artifacts/post_submit.png", headers=HEADERS)
    assert res_file.status_code == 200
    assert res_file.headers.get("content-type") == "image/png"


def test_export_system_diagnostics(client_env):
    res = client_env.get("/api/diagnostics/export", headers=HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert "hunterx_version" in data
    assert "system" in data
    assert "posture" in data
    assert "database" in data
    assert "profile" in data
    assert "certification_dossier_summary" in data
    assert data["posture"]["live_submission_locked"] is True


def test_answers_crud_endpoints(client_env):
    # Create answer
    res = client_env.post("/api/answers", json={
        "key": "custom_question_123",
        "value": "Yes, extensive experience",
        "scope": "global",
        "note": "Unit test answer",
    }, headers=HEADERS)
    assert res.status_code == 200
    ans_id = res.json()["id"]

    # List answers
    res_list = client_env.get("/api/answers", headers=HEADERS)
    assert res_list.status_code == 200
    items = res_list.json()
    assert any(a["id"] == ans_id and a["key"] == "custom_question_123" for a in items)

    # Delete answer
    res_del = client_env.delete(f"/api/answers/{ans_id}", headers=HEADERS)
    assert res_del.status_code == 200
    assert res_del.json()["deleted"] is True

    # Check not found
    res_del2 = client_env.delete(f"/api/answers/{ans_id}", headers=HEADERS)
    assert res_del2.status_code == 404

