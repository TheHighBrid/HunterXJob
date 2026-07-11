"""Create/list/patch application via TestClient."""


def _seed_job(client) -> str:
    from app import db as db_module
    from app.models import JobPosting

    session = db_module.SessionLocal()
    try:
        job = JobPosting(
            source="greenhouse",
            external_id="job-1",
            title="Backend Engineer",
            company="Acme",
            url="https://example.com/job/1",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id
    finally:
        session.close()


def test_create_application(client):
    job_id = _seed_job(client)
    resp = client.post(
        "/api/applications",
        json={"job_posting_id": job_id, "channel": "greenhouse", "status": "discovered"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["job_posting_id"] == job_id
    assert data["status"] == "discovered"
    assert data["channel"] == "greenhouse"


def test_create_application_rejects_unknown_job(client):
    resp = client.post(
        "/api/applications",
        json={"job_posting_id": "does-not-exist", "channel": "greenhouse"},
    )
    assert resp.status_code == 404


def test_list_applications(client):
    job_id = _seed_job(client)
    client.post("/api/applications", json={"job_posting_id": job_id, "channel": "email"})

    resp = client.get("/api/applications")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["channel"] == "email"


def test_list_applications_filter_by_status(client):
    job_id = _seed_job(client)
    client.post(
        "/api/applications",
        json={"job_posting_id": job_id, "channel": "email", "status": "queued"},
    )
    client.post(
        "/api/applications",
        json={"job_posting_id": job_id, "channel": "email", "status": "applied"},
    )

    resp = client.get("/api/applications?status=queued")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "queued"


def test_patch_application_updates_status(client):
    job_id = _seed_job(client)
    create_resp = client.post(
        "/api/applications", json={"job_posting_id": job_id, "channel": "greenhouse"}
    )
    application_id = create_resp.json()["id"]

    patch_resp = client.patch(
        f"/api/applications/{application_id}",
        json={"status": "queued", "notes": "ready to submit"},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["status"] == "queued"
    assert data["notes"] == "ready to submit"


def test_patch_unknown_application_404(client):
    resp = client.patch("/api/applications/does-not-exist", json={"status": "queued"})
    assert resp.status_code == 404


def test_get_application_requires_api_key(client):
    resp = client.get("/api/applications", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
