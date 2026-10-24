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
