"""Phone API for the profile and per-job materials."""

import pytest
from fastapi.testclient import TestClient
from profile_helpers import EXAMPLE_PROFILE
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import Settings, get_settings
from app.db import Base
from app.flags import ensure_flags
from app.models import Application, Job, PipelineStage, ProfileFact, SubmissionEvidence

KEY = "m" * 40
AUTH = {"X-API-Key": KEY}


@pytest.fixture
def env(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, api_key=KEY, materials_dir=str(tmp_path / "materials"))

    def _get_db():
        with Session() as session:
            yield session

    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[main.get_db] = _get_db
    with Session() as db:
        ensure_flags(db)
        job = Job(source="greenhouse", external_id="5", title="Fraud Analyst", company="Maplebank", location="Ottawa",
                  url="https://job-boards.greenhouse.io/maplebank/jobs/5", description="AML, KYC and SQL for card disputes",
                  stage=PipelineStage.shortlisted.value, platform="greenhouse")
        db.add(job)
        db.commit()
        db.add(Application(job_id=job.id, stage=PipelineStage.shortlisted.value))
        db.commit()
        job_id = job.id
    yield {"client": TestClient(main.app), "Session": Session, "job_id": job_id}
    main.app.dependency_overrides.clear()


def _import(client, verified):
    content = EXAMPLE_PROFILE.read_text(encoding="utf-8").replace("verified: false", f"verified: {str(verified).lower()}", 1)
    return client.post("/api/profile/import", headers=AUTH, json={"format": "yaml", "content": content, "filename": "me.yaml"})


def test_profile_routes_need_the_key(env):
    assert env["client"].get("/api/profile").status_code == 401
    assert env["client"].post("/api/profile/import", json={"format": "yaml", "content": "x"}).status_code == 401


def test_profile_import_verify_edit_and_remove(env):
    client = env["client"]
    body = _import(client, verified=False).json()
    assert body["created"] == 36 and body["verified"] == 0
    profile = client.get("/api/profile", headers=AUTH).json()
    assert profile["ready"] is False and profile["unverified"] == 36 and profile["missing"]
    email = next(f for f in profile["facts"] if f["key"] == "contact:email")
    assert email["provenance"] == "me.yaml#contact.email" and email["source"] == "profile_file"

    verified = client.post(f"/api/profile/facts/{email['id']}/verify", headers=AUTH, json={"verified": True}).json()
    assert verified["verified"] is True and verified["verified_at"]
    edited = client.put(f"/api/profile/facts/{email['id']}", headers=AUTH,
                        json={"data": {"field": "email", "value": "avery.q@example.com"}}).json()
    assert edited["verified"] is False and edited["provenance"] == "edited by owner"
    bad = client.put(f"/api/profile/facts/{email['id']}", headers=AUTH, json={"data": {"field": "ssn", "value": "1"}})
    assert bad.status_code == 422

    created = client.post("/api/profile/facts", headers=AUTH,
                          json={"category": "skill", "data": {"name": "Tableau"}, "verified": True}).json()
    assert created["key"] == "skill:tableau" and created["verified"] is True
    assert client.post("/api/profile/facts", headers=AUTH, json={"category": "skill", "data": {"name": "Tableau"}}).status_code == 422
    assert client.post(f"/api/profile/facts/{created['id']}/remove", headers=AUTH).json() == {"removed": created["id"]}
    assert client.post(f"/api/profile/facts/{created['id']}/remove", headers=AUTH).status_code == 404


def test_pasted_resume_text_is_imported_unverified(env):
    text = "Avery Quinn\navery.quinn@example.com\n\nEXPERIENCE\nAnalyst | Northwind Credit Union\n2020 - Present\n- Did things\n"
    body = env["client"].post("/api/profile/import", headers=AUTH, json={"format": "resume_text", "content": text}).json()
    assert body["created"] >= 4 and body["verified"] == 0
    with env["Session"]() as db:
        assert {row.source for row in db.execute(select(ProfileFact)).scalars()} == {"resume_text"}
        assert not db.execute(select(ProfileFact).where(ProfileFact.verified.is_(True))).first()


def test_materials_preview_approve_and_download(env):
    client, job_id = env["client"], env["job_id"]
    before = client.get(f"/api/jobs/{job_id}/materials", headers=AUTH).json()
    assert before["can_generate"] is False and before["items"] == [] and before["llm_enabled"] is False
    assert client.post(f"/api/jobs/{job_id}/materials/generate", headers=AUTH).status_code == 409

    _import(client, verified=True)
    generated = client.post(f"/api/jobs/{job_id}/materials/generate", headers=AUTH).json()
    assert generated["job_stage"] == "materials_generated" and generated["approved"] == {}
    items = {item["kind"]: item for item in generated["items"]}
    assert {item["status"] for item in items.values()} == {"draft"}

    detail = client.get(f"/api/materials/{items['resume']['id']}", headers=AUTH).json()
    assert "Northwind Credit Union" in detail["text"] and "employment:northwind" in detail["facts_used"]
    assert detail["llm_report"] == {"status": "disabled"} and detail["guard"]["status"] == "passed"
    pdf = client.get(f"/api/materials/{items['resume']['id']}/file/pdf", headers=AUTH)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert client.get(f"/api/materials/{items['resume']['id']}/file/pdf").status_code == 401
    assert client.get(f"/api/materials/{items['resume']['id']}/file/exe", headers=AUTH).status_code == 422

    review = client.get("/api/review-tasks", headers=AUTH).json()
    task = client.get(f"/api/review-tasks/{review[0]['id']}", headers=AUTH).json()
    assert task["reason_code"] == "materials_review" and {m["kind"] for m in task["materials"]} == {"resume", "cover_letter"}

    approved = client.post(f"/api/materials/{items['resume']['id']}/approve", headers=AUTH, json={"note": "ok"}).json()
    assert approved["material"]["status"] == "approved" and approved["submitted"] is False
    assert approved["job_stage"] == "materials_generated"  # the cover letter is still pending
    rejected = client.post(f"/api/materials/{items['cover_letter']['id']}/reject", headers=AUTH).json()
    assert rejected["materials_ready"] is True and rejected["job_stage"] == "ready_to_apply"
    assert client.post(f"/api/materials/{items['cover_letter']['id']}/approve", headers=AUTH).status_code == 409

    after = client.get(f"/api/jobs/{job_id}/materials", headers=AUTH).json()
    assert after["approved"] == {"resume": items["resume"]["id"]}
    assert client.get(f"/api/jobs/{job_id}", headers=AUTH).json()["application"]["materials_ready"] is True
    with env["Session"]() as db:
        assert not db.execute(select(SubmissionEvidence).where(SubmissionEvidence.sufficient.is_(True))).first()


def test_review_task_approval_approves_pending_drafts(env):
    client, job_id = env["client"], env["job_id"]
    _import(client, verified=True)
    client.post(f"/api/jobs/{job_id}/materials/generate", headers=AUTH)
    task_id = client.get("/api/review-tasks", headers=AUTH).json()[0]["id"]
    body = client.post(f"/api/review-tasks/{task_id}/approve", headers=AUTH).json()
    assert body["action"] == "approve_application" and body["job_stage"] == "ready_to_apply" and body["submitted"] is False
    statuses = {item["kind"]: item["status"] for item in client.get(f"/api/jobs/{job_id}/materials", headers=AUTH).json()["items"]}
    assert statuses == {"resume": "approved", "cover_letter": "approved"}
