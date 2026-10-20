"""GET /api/jobs via FastAPI TestClient, with API key auth."""
from tests.conftest import TEST_API_KEY


def _seed_job(client, title="Backend Engineer", company="Acme", score=80.0):
    from app import db as db_module
    from app.models import JobPosting

    session = db_module.SessionLocal()
    try:
        job = JobPosting(
            source="greenhouse",
            external_id=title.replace(" ", "-").lower(),
            title=title,
            company=company,
            location="Remote",
            remote=True,
            description="A great job.",
            url="https://example.com/job",
            match_score=score,
        )
        session.add(job)
        session.commit()
    finally:
        session.close()


def test_jobs_requires_api_key(client):
    resp = client.get("/api/jobs", headers={"X-API-Key": ""})
    assert resp.status_code == 401


def test_jobs_rejects_wrong_api_key(client):
    resp = client.get("/api/jobs", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


def test_list_jobs_empty(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_returns_seeded_job(client):
    _seed_job(client, title="Senior Backend Engineer", score=90.0)
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Senior Backend Engineer"
    assert data[0]["match_score"] == 90.0


def test_list_jobs_sorted_by_match_score_desc(client):
    _seed_job(client, title="Low Match", score=20.0)
    _seed_job(client, title="High Match", score=95.0)

    resp = client.get("/api/jobs?sort_by=match_score&order=desc")
    data = resp.json()
    assert len(data) == 2
    assert data[0]["title"] == "High Match"
    assert data[1]["title"] == "Low Match"


def test_list_jobs_filter_by_min_score(client):
    _seed_job(client, title="Low Match", score=20.0)
    _seed_job(client, title="High Match", score=95.0)

    resp = client.get("/api/jobs?min_score=50")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "High Match"
