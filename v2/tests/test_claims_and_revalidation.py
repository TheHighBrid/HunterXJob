from __future__ import annotations

import json
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from profile_helpers import load_example_profile

from app import main
from app.autonomous_material_verification import verify_job_materials_claims, inspect_resume_artifact
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.manual_review_policy_revalidation import revalidate_open_tasks, revalidate_single_task
from app.models import AnswerPolicy, Application, Job, PipelineStage, ReviewTask

KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}


@pytest.fixture
def env(tmp_path: Path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    settings = Settings(_env_file=None, api_key=KEY, artifact_dir=str(tmp_path / "artifacts"))

    with Session() as db:
        load_example_profile(db, verified=True)

    def _get_db():
        with Session() as session:
            yield session

    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[get_db] = _get_db
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    yield {"Session": Session, "client": client, "settings": settings, "tmp": tmp_path}
    main.app.dependency_overrides.clear()


def test_claims_verification_logic(env):
    Session = env["Session"]
    settings = env["settings"]
    with Session() as db:
        job = Job(
            id="job-claims-1",
            source="test",
            external_id="job-claims-1",
            url="https://example.com/job-claims-1",
            title="Fraud Analyst",
            company="Wealthsimple",
            location="Remote, Canada",
            description="We are seeking an AML and Fraud Detection specialist.",
            stage=PipelineStage.ready_to_apply.value,
        )
        db.add(job)
        db.commit()

        report = verify_job_materials_claims(db, job, artifact_dir=settings.artifact_dir)
        assert report["job_id"] == "job-claims-1"
        assert report["truth_grounded"] is True
        assert report["unsupported_count"] == 0
        assert report["claims_total"] > 0
        assert report["profile_facts_grounding_rate"] == 1.0
        assert report["material_hash"]


def test_claims_verification_api_endpoint(env):
    Session = env["Session"]
    client = env["client"]

    with Session() as db:
        job = Job(
            id="job-api-claims",
            source="test",
            external_id="job-api-claims",
            url="https://example.com/job-api-claims",
            title="Risk Operations Associate",
            company="FinTech Inc",
            location="Ottawa, ON",
            description="Seeking skills in Risk Analysis and AML compliance.",
            stage=PipelineStage.shortlisted.value,
        )
        db.add(job)
        db.commit()

    resp = client.get("/api/jobs/job-api-claims/claims-verification", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == "job-api-claims"
    assert data["status"] == "verified"
    assert data["truth_grounded"] is True
    assert len(data["verified_claims"]) > 0


def test_batch_review_actions(env):
    Session = env["Session"]
    client = env["client"]

    with Session() as db:
        j1 = Job(id="j-b-1", source="test", external_id="j-b-1", url="https://example.com/1", title="Role 1", company="Co 1", stage=PipelineStage.review.value)
        j2 = Job(id="j-b-2", source="test", external_id="j-b-2", url="https://example.com/2", title="Role 2", company="Co 2", stage=PipelineStage.review.value)
        db.add_all([j1, j2])
        t1 = ReviewTask(id="task-b-1", job_id="j-b-1", title="Task 1", reason_code="decision_review", status="open")
        t2 = ReviewTask(id="task-b-2", job_id="j-b-2", title="Task 2", reason_code="decision_review", status="open")
        db.add_all([t1, t2])
        db.commit()

    # Batch resolve
    resp = client.post("/api/review-tasks/batch-resolve", headers=HEADERS, json={"task_ids": ["task-b-1"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["succeeded"] == 1
    assert body["failed"] == 0

    with Session() as db:
        task1 = db.get(ReviewTask, "task-b-1")
        assert task1.status == "resolved"

    # Batch approve remaining
    resp = client.post("/api/review-tasks/batch-approve", headers=HEADERS, json={"task_ids": ["task-b-2"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["succeeded"] == 1

    with Session() as db:
        task2 = db.get(ReviewTask, "task-b-2")
        assert task2.status == "approved"


def test_review_revalidation_service_and_endpoint(env):
    Session = env["Session"]
    client = env["client"]

    with Session() as db:
        job = Job(id="j-reval-1", source="test", external_id="j-reval-1", url="https://example.com/reval1", title="Dispute Agent", company="CardCo", stage=PipelineStage.needs_review.value)
        db.add(job)
        task = ReviewTask(
            id="task-reval-1",
            job_id="j-reval-1",
            title="Question Review",
            reason_code="legal_answer_missing",
            detail="legal.work_authorization_canada needs answer",
            status="open",
        )
        db.add(task)
        ans = AnswerPolicy(key="legal.work_authorization_canada", value="Yes", source="user")
        db.add(ans)
        db.commit()

    resp = client.post("/api/review-tasks/revalidate", headers=HEADERS, json={"task_ids": ["task-reval-1"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["examined"] == 1
    assert data["resolved"] == 1
    assert data["promoted_to_ready"] == 1

    with Session() as db:
        updated_task = db.get(ReviewTask, "task-reval-1")
        assert updated_task.status == "resolved"
        updated_job = db.get(Job, "j-reval-1")
        assert updated_job.stage == PipelineStage.ready_to_apply.value
