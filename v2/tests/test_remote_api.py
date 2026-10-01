"""Phone remote-control API: auth, behaviour and safety invariants."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from profile_helpers import approve_drafts, load_example_profile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main, real_forms
from app.backup import BACKUP_PREFIX, BACKUP_SUFFIX
from app.config import Settings, get_settings
from app.cycle import CycleRunner
from app.db import Base
from app.flags import ensure_flags, is_enabled, set_flag
from app.greenhouse_form import FormFetchError
from app.material_workflow import generate_for_application
from app.models import (
    Application,
    ApplicationMaterial,
    FeatureFlag,
    Job,
    PipelineEvent,
    PipelineStage,
    ProfileFact,
    ReviewTask,
    SchedulerCycle,
    SubmissionEvidence,
)
from app.runtime import get_runner
from app.runtime_settings import apply_stored_overrides

KEY = "r" * 40
AUTH = {"X-API-Key": KEY}
SUBMISSION_STAGES = {PipelineStage.submitted.value, PipelineStage.confirmed.value, PipelineStage.submission_uncertain.value}


class FakeRunner(CycleRunner):
    """Real status reporting; run-now only counts instead of starting a thread."""

    triggered = 0

    def trigger_async(self):
        self.triggered += 1
        return True


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(_env_file=None, api_key=KEY, backup_dir=str(tmp_path / "backups"), blacklisted_companies="EvilCorp",
                        materials_dir=str(tmp_path / "materials"))
    runner = FakeRunner(settings, Session, tmp_path / "run" / "cycle.lock")

    def _get_db():
        with Session() as session:
            yield session

    monkeypatch.setattr(main.LocalAI, "health", lambda self: {"ok": False, "error": "offline in tests"})
    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[main.get_db] = _get_db
    main.app.dependency_overrides[get_runner] = lambda: runner
    with Session() as db:
        ensure_flags(db)
    yield {"client": TestClient(main.app, client=("203.0.113.9", 50000)), "Session": Session,
           "settings": settings, "runner": runner, "tmp": tmp_path}
    main.app.dependency_overrides.clear()


def _job(db, title="Fraud Analyst", company="Acme", stage=PipelineStage.discovered.value, **extra):
    job = Job(source="greenhouse", external_id=f"{title}-{company}-{stage}", title=title, company=company,
              location="Ottawa, ON", url=f"https://boards.greenhouse.io/{company.lower()}/jobs/1",
              stage=stage, platform="greenhouse", **extra)
    db.add(job)
    db.commit()
    return job


def _application(db, job, stage, **extra):
    application = Application(job_id=job.id, stage=stage, mode="dry_run", **extra)
    db.add(application)
    db.commit()
    return application


def _approved_materials(db, settings, application):
    """Owner-approved résumé + cover letter from the made-up example profile."""
    load_example_profile(db)
    generate_for_application(db, settings, application.id)
    return approve_drafts(db, application.id)


def _task(db, job, application=None, reason="decision_review"):
    task = ReviewTask(job_id=job.id, application_id=application.id if application else None,
                      reason_code=reason, title=f"Review {job.title}", detail="Blockers: legal_answer_missing")
    db.add(task)
    db.commit()
    return task


# ------------------------------------------------------------------------- auth


def _api_routes():
    for route in main.app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/") and route.path != "/api/health":
            for method in sorted(route.methods):
                yield method, route.path


def _concrete(path):
    for name in ("job_id", "task_id", "application_id", "fact_id", "material_id"):
        path = path.replace("{" + name + "}", "x")
    return path.replace("{key}", "global_kill_switch").replace("{fmt}", "pdf")


def test_every_api_route_requires_the_key(env):
    client = env["client"]
    routes = list(_api_routes())
    assert len(routes) >= 30
    for method, path in routes:
        response = client.request(method, _concrete(path), json={})
        assert response.status_code == 401, (method, path, response.status_code)
        bad = client.request(method, _concrete(path), json={}, headers={"X-API-Key": "nope" * 10})
        assert bad.status_code == 401, (method, path)


def test_auth_check_and_health(env):
    client = env["client"]
    assert client.get("/api/auth/check").status_code == 401
    body = client.get("/api/auth/check", headers=AUTH).json()
    assert body["ok"] is True and body["auth"] == "api_key"
    health = client.get("/api/health", headers=AUTH).json()
    assert health["live_submission_locked"] is True and health["allow_live_submission"] is False


# --------------------------------------------------------------------- settings


def test_settings_view_never_contains_secrets(env):
    env["settings"].greenhouse_board_tokens = "acme,globex"
    response = env["client"].get("/api/settings", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    text = response.text
    assert KEY not in text
    assert env["settings"].ollama_base_url not in text
    assert "database" not in text
    assert body["live_submission"]["locked"] is True
    assert body["sources"]["greenhouse_boards"] == 2
    assert "allow_live_submission" not in body["editable"]
    assert "application_mode" not in body["editable"]


def test_settings_patch_applies_and_persists(env):
    client = env["client"]
    response = client.patch("/api/settings", headers=AUTH, json={
        "max_dry_runs_per_day": 4,
        "quiet_hours_start": "22:30",
        "blacklisted_companies": [" Initech ", "EvilCorp", "Initech"],
        "automation_enabled": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["max_dry_runs_per_day"] == 4
    assert body["blacklisted_companies"] == ["Initech", "EvilCorp"]
    assert set(body["overridden"]) == {"max_dry_runs_per_day", "quiet_hours_start", "blacklisted_companies", "automation_enabled"}
    assert env["settings"].blacklisted_company_list == ["Initech", "EvilCorp"]
    # A restart re-applies the stored overrides over a fresh environment.
    fresh = Settings(_env_file=None, api_key=KEY)
    with env["Session"]() as db:
        apply_stored_overrides(db, fresh)
    assert fresh.max_dry_runs_per_day == 4 and fresh.quiet_hours_start == "22:30" and fresh.automation_enabled is True


@pytest.mark.parametrize("payload", [
    {"allow_live_submission": True},
    {"application_mode": "autonomous"},
    {"api_key": "x" * 40},
    {"continuous_run_enabled": True},
    {"local_dev_mode": True},
    {"max_dry_runs_per_day": 5, "allow_live_submission": True},
    {"max_dry_runs_per_day": 1000},
    {"max_applications_per_day": -1},
    {"quiet_hours_start": "25:00"},
    {"target_keywords": ["fraud, aml"]},
])
def test_settings_patch_refuses_unsafe_or_invalid_changes(env, payload):
    before = env["settings"].model_dump()
    response = env["client"].patch("/api/settings", headers=AUTH, json=payload)
    assert response.status_code == 422
    assert env["settings"].model_dump() == before
    assert env["settings"].allow_live_submission is False


# ------------------------------------------------------------ flags/kill switch


def test_live_submission_flags_can_only_be_turned_off(env):
    client = env["client"]
    for key in ("allow_live_submission", "unattended_mode"):
        refused = client.put(f"/api/flags/{key}", headers=AUTH, json={"enabled": True, "confirm": True})
        assert refused.status_code == 403
        assert client.put(f"/api/flags/{key}", headers=AUTH, json={"enabled": False}).status_code == 200
    assert client.put("/api/flags/made_up", headers=AUTH, json={"enabled": True}).status_code == 404
    with env["Session"]() as db:
        assert not is_enabled(db, "allow_live_submission")
        assert not is_enabled(db, "unattended_mode")
        assert db.get(FeatureFlag, "made_up") is None


def test_kill_switch_engage_always_disengage_needs_confirm(env):
    client = env["client"]
    assert client.get("/api/kill-switch", headers=AUTH).json()["engaged"] is False
    engaged = client.post("/api/kill-switch", headers=AUTH, json={"engaged": True})
    assert engaged.status_code == 200 and engaged.json()["engaged"] is True
    assert client.post("/api/kill-switch", headers=AUTH, json={"engaged": False}).status_code == 428
    assert client.put("/api/flags/global_kill_switch", headers=AUTH, json={"enabled": False}).status_code == 428
    assert client.get("/api/kill-switch", headers=AUTH).json()["engaged"] is True
    released = client.post("/api/kill-switch", headers=AUTH, json={"engaged": False, "confirm": True, "note": "checked"})
    assert released.status_code == 200 and released.json() == {**released.json(), "engaged": False, "note": "checked"}


def test_scheduler_pause_resume_and_run_now(env):
    client = env["client"]
    paused = client.post("/api/scheduler/pause", headers=AUTH)
    assert paused.status_code == 200 and paused.json()["paused"] is True
    assert paused.json()["live_submission"].startswith("locked")
    resumed = client.post("/api/scheduler/resume", headers=AUTH, json={"note": "back on"})
    assert resumed.json()["paused"] is False
    run = client.post("/api/scheduler/run", headers=AUTH)
    assert run.status_code == 202 and run.json()["started"] is True
    assert env["runner"].triggered == 1


# ----------------------------------------------------------------- jobs/reports


def test_jobs_list_filters_and_detail(env):
    with env["Session"]() as db:
        scored = _job(db, title="AML Investigator", stage=PipelineStage.ready_to_apply.value, final_score=81.5,
                      deterministic_score=78.0, ai_score=84.0, eligible=True)
        db.add(PipelineEvent(job_id=scored.id, from_stage="discovered", to_stage="eligible", message="ok",
                             payload_json=json.dumps({"decision": "shortlist", "matched_keywords": ["aml"], "review_flags": [],
                                                      "vetoes": [], "dimensions": [{"name": "keywords", "score": 90.0, "weight": 0.4,
                                                                                    "weighted_points": 36.0, "evidence": ["aml"]}]})))
        db.commit()
        application = _application(db, scored, PipelineStage.needs_review.value, cover_letter_text="hi",
                                   validation_json=json.dumps({"ok": False, "form": {"source": "greenhouse_api", "fields_total": 12},
                                                               "blockers": ["legal_answer_missing"],
                                                               "blocked_fields": [{"key": "q1", "label": "Sponsorship?", "section": "legal",
                                                                                   "required": True, "reason": "legal answer missing"}]}))
        _task(db, scored, application, reason="legal_answer_missing")
        _job(db, title="Collections Agent", company="Globex", final_score=40.0)
        job_id = scored.id
    client = env["client"]
    rows = client.get("/api/jobs", headers=AUTH).json()
    assert [row["title"] for row in rows] == ["AML Investigator", "Collections Agent"]
    assert rows[0]["open_review_tasks"] == 1 and rows[0]["application_stage"] == "needs_review"
    assert [r["title"] for r in client.get("/api/jobs?q=globex", headers=AUTH).json()] == ["Collections Agent"]
    assert client.get("/api/jobs?stage=ready_to_apply", headers=AUTH).json()[0]["id"] == job_id
    detail = client.get(f"/api/jobs/{job_id}", headers=AUTH).json()
    assert detail["scores"] == {"final": 81.5, "deterministic": 78.0, "ai": 84.0}
    assert detail["decision"]["dimensions"][0]["name"] == "keywords"
    assert detail["form_status"]["state"] == "blocked"
    assert detail["form_status"]["blocked_fields"][0]["key"] == "q1"
    assert detail["form_status"]["fields_total"] == 12
    assert detail["events"][0]["created_at"].endswith("+00:00")
    assert client.get("/api/jobs/missing", headers=AUTH).status_code == 404


def test_reports_summary_counts(env):
    now = datetime.now(UTC)
    with env["Session"]() as db:
        a = _job(db, title="A", stage=PipelineStage.validated.value, final_score=70.0)
        _job(db, title="B", stage=PipelineStage.rejected.value, final_score=20.0)
        app_a = _application(db, a, PipelineStage.validated.value)
        db.add(SubmissionEvidence(application_id=app_a.id, kind="dry_run"))
        db.add(PipelineEvent(job_id=a.id, from_stage="ready_to_apply", to_stage="applying", message="dry-run"))
        db.add(SchedulerCycle(trigger="scheduled", status="completed", started_at=now - timedelta(hours=1), finished_at=now))
        db.add(SchedulerCycle(trigger="scheduled", status="skipped", reason="quiet hours", started_at=now - timedelta(hours=2)))
        db.commit()
        _task(db, a)
    body = env["client"].get("/api/reports/summary", headers=AUTH).json()
    assert body["live_submission_locked"] is True
    assert body["jobs"]["total"] == 2 and body["jobs"]["scored"] == 2 and body["jobs"]["rejected"] == 1
    assert body["jobs"]["discovered_today"] == 2
    assert body["dry_runs"]["completed_total"] == 1 and body["dry_runs"]["today"] == 1
    assert body["submissions"] == {"today": 0, "total": 0, "daily_cap": env["settings"].max_applications_per_day}
    assert body["review"]["open"] == 1 and body["review"]["open_by_reason"] == {"decision_review": 1}
    assert body["cycles"]["last_24h"] == {"completed": 1, "skipped": 1}
    assert len(body["history"]) == 7 and body["history"][-1]["discovered"] == 2


def test_backups_list_names_only(env):
    backup_dir = env["tmp"] / "backups"
    backup_dir.mkdir()
    for stamp in ("20260930T010000Z", "20261001T010000Z"):
        (backup_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}").write_bytes(b"x" * 10)
    (backup_dir / "notes.txt").write_text("ignored")
    body = env["client"].get("/api/backups", headers=AUTH).json()
    assert [item["name"] for item in body["items"]] == [
        f"{BACKUP_PREFIX}20261001T010000Z{BACKUP_SUFFIX}", f"{BACKUP_PREFIX}20260930T010000Z{BACKUP_SUFFIX}"]
    assert body["items"][0]["size_bytes"] == 10
    assert str(backup_dir) not in json.dumps(body)


# ----------------------------------------------------------------------- review


def test_approving_a_decision_review_shortlists_without_submitting(env):
    with env["Session"]() as db:
        job = _job(db, stage=PipelineStage.review.value, final_score=55.0)
        task = _task(db, job)
        task_id, job_id = task.id, job.id
    client = env["client"]
    detail = client.get(f"/api/review-tasks/{task_id}", headers=AUTH).json()
    assert detail["actions"] == ["approve", "reject", "resolve"]
    assert "Shortlists" in detail["approve_effect"]
    result = client.post(f"/api/review-tasks/{task_id}/approve", headers=AUTH)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["submitted"] is False and body["live_submission_locked"] is True
    assert body["job_stage"] == "shortlisted" and body["task"]["status"] == "approved"
    with env["Session"]() as db:
        assert db.execute(select(Application).where(Application.job_id == job_id)).scalar_one().stage == "shortlisted"
        assert not db.execute(select(SubmissionEvidence)).scalars().all()
    assert client.post(f"/api/review-tasks/{task_id}/approve", headers=AUTH).status_code == 409


def test_approving_a_blocked_application_only_requeues_a_dry_run(env):
    with env["Session"]() as db:
        job = _job(db, stage=PipelineStage.needs_review.value)
        application = _application(db, job, PipelineStage.needs_review.value, cover_letter_text="unapproved legacy text")
        task_id = _task(db, job, application, reason="legal_answer_missing").id
    # Legacy text is not an approved material: nothing to requeue yet.
    assert env["client"].post(f"/api/review-tasks/{task_id}/approve", headers=AUTH).status_code == 409
    with env["Session"]() as db:
        _approved_materials(db, env["settings"], db.get(Application, application.id))
    body = env["client"].post(f"/api/review-tasks/{task_id}/approve", headers=AUTH).json()
    assert body["action"] == "requeue"
    assert body["job_stage"] == body["application_stage"] == "ready_to_apply"
    assert body["submitted"] is False
    with env["Session"]() as db:
        kinds = set(db.execute(select(SubmissionEvidence.kind)).scalars())
        assert kinds == {"materials_draft", "materials_approved"}


def test_approval_respects_the_kill_switch_and_exclusions(env):
    with env["Session"]() as db:
        job = _job(db, stage=PipelineStage.review.value)
        task_id = _task(db, job).id
        evil = _job(db, company="EvilCorp", stage=PipelineStage.review.value)
        evil_task = _task(db, evil).id
        set_flag(db, "global_kill_switch", True)
    client = env["client"]
    refused = client.post(f"/api/review-tasks/{task_id}/approve", headers=AUTH)
    assert refused.status_code == 409 and "kill switch" in refused.json()["detail"]
    with env["Session"]() as db:
        set_flag(db, "global_kill_switch", False)
    assert client.post(f"/api/review-tasks/{evil_task}/approve", headers=AUTH).status_code == 409
    assert client.post(f"/api/review-tasks/{task_id}/approve", headers=AUTH).status_code == 200


def test_reject_and_resolve(env):
    with env["Session"]() as db:
        job = _job(db, stage=PipelineStage.review.value)
        reject_id = _task(db, job).id
        other = _job(db, title="Other", stage=PipelineStage.review.value)
        resolve_id = _task(db, other).id
        other_id = other.id
    client = env["client"]
    rejected = client.post(f"/api/review-tasks/{reject_id}/reject", headers=AUTH).json()
    assert rejected["job_stage"] == "rejected" and rejected["task"]["status"] == "rejected"
    assert client.post(f"/api/review-tasks/{resolve_id}/resolve", headers=AUTH, json={"resolution": "approved"}).status_code == 422
    resolved = client.post(f"/api/review-tasks/{resolve_id}/resolve", headers=AUTH, json={"resolution": "dismissed"}).json()
    assert resolved["task"]["status"] == "dismissed"
    with env["Session"]() as db:
        assert db.get(Job, other_id).stage == "review"
    assert client.get("/api/review-tasks", headers=AUTH).json() == []
    assert len(client.get("/api/review-tasks?status=closed", headers=AUTH).json()) == 2


# --------------------------------------------------------------- safety sweep

MUTATING_ROUTES = {
    ("PUT", "/api/resume-facts"),
    ("POST", "/api/answers"),
    ("POST", "/api/discovery/run"),
    ("POST", "/api/scoring/run"),
    ("POST", "/api/applications/{application_id}/generate"),
    ("POST", "/api/applications/{application_id}/approve"),
    ("POST", "/api/applications/{application_id}/apply"),
    ("PATCH", "/api/settings"),
    ("POST", "/api/review-tasks/{task_id}/approve"),
    ("POST", "/api/review-tasks/{task_id}/reject"),
    ("POST", "/api/review-tasks/{task_id}/resolve"),
    ("POST", "/api/scheduler/run"),
    ("POST", "/api/scheduler/pause"),
    ("POST", "/api/scheduler/resume"),
    ("POST", "/api/kill-switch"),
    ("PUT", "/api/flags/{key}"),
    ("POST", "/api/profile/import"),
    ("POST", "/api/profile/facts"),
    ("PUT", "/api/profile/facts/{fact_id}"),
    ("POST", "/api/profile/facts/{fact_id}/verify"),
    ("POST", "/api/profile/facts/{fact_id}/remove"),
    ("POST", "/api/jobs/{job_id}/materials/generate"),
    ("POST", "/api/materials/{material_id}/approve"),
    ("POST", "/api/materials/{material_id}/reject"),
}

HOSTILE_BODIES = [
    {},
    {"allow_live_submission": True, "application_mode": "autonomous", "enabled": True, "confirm": True, "engaged": False},
    {"enabled": True, "confirm": True, "note": "unlock"},
    {"automation_enabled": True, "max_dry_runs_per_day": 100},
    {"resolution": "resolved"},
    {"engaged": True},
    {"verified": True, "data": {"employer": "Invented Corp", "title": "CEO"}, "category": "employment"},
    {"format": "yaml", "content": "verified: true\nemployment: [{employer: X, title: Y}]"},
    {"note": "approve everything"},
]


def test_mutating_route_inventory_is_reviewed():
    """Adding a mutating route must come with a review of the safety sweep below."""
    found = {(m, p) for m, p in _api_routes() if m != "GET"}
    assert found == MUTATING_ROUTES


def test_no_route_can_unlock_live_submission_or_record_a_submission(env, monkeypatch):
    def unavailable(self, job):
        raise FormFetchError("form_unavailable", "offline in tests")

    monkeypatch.setattr(real_forms.LiveFormProvider, "fetch", unavailable)
    monkeypatch.setattr(main, "read_resume_facts", lambda: "")
    monkeypatch.setattr(main, "discover_all", lambda settings, errors=None: [])
    monkeypatch.setattr(main, "RESUME_FACTS_PATH", env["tmp"] / "resume_facts.txt")
    with env["Session"]() as db:
        review_job = _job(db, title="Review", stage=PipelineStage.review.value)
        ready = _job(db, title="Ready", stage=PipelineStage.ready_to_apply.value)
        app_ready = _application(db, ready, PipelineStage.ready_to_apply.value, cover_letter_text="draft")
        blocked = _job(db, title="Blocked", stage=PipelineStage.needs_review.value)
        app_blocked = _application(db, blocked, PipelineStage.needs_review.value, cover_letter_text="draft")
        _approved_materials(db, env["settings"], app_blocked)
        generate_for_application(db, env["settings"], app_ready.id)
        ids = {
            "task_id": [_task(db, review_job).id, _task(db, blocked, app_blocked, "legal_answer_missing").id],
            "application_id": [app_ready.id, app_blocked.id],
            "key": ["allow_live_submission", "unattended_mode", "global_kill_switch", "adapter.workday"],
            "job_id": [ready.id, blocked.id, review_job.id],
            "material_id": list(db.execute(select(ApplicationMaterial.id)).scalars()),
            "fact_id": list(db.execute(select(ProfileFact.id)).scalars())[:3],
        }
    client = TestClient(main.app, client=("203.0.113.9", 50000), raise_server_exceptions=False)
    for method, path in sorted(MUTATING_ROUTES):
        params = [name for name in ids if "{" + name + "}" in path] or [None]
        values = ids[params[0]] if params[0] else [None]
        for value in values:
            url = path.replace("{" + params[0] + "}", value) if params[0] else path
            for body in HOSTILE_BODIES:
                response = client.request(method, url, headers=AUTH, json=body)
                assert response.status_code < 500, (method, url, body, response.text)
    # Settings changes through the API were limited to the safe subset.
    assert env["settings"].allow_live_submission is False
    assert env["settings"].application_mode == "dry_run"
    with env["Session"]() as db:
        assert not is_enabled(db, "allow_live_submission")
        assert not is_enabled(db, "unattended_mode")
        assert not db.execute(select(Application).where(Application.stage.in_(SUBMISSION_STAGES))).scalars().all()
        assert not db.execute(select(Job).where(Job.stage.in_(SUBMISSION_STAGES))).scalars().all()
        kinds = set(db.execute(select(SubmissionEvidence.kind)).scalars())
        assert kinds <= {"dry_run", "materials_draft", "materials_approved"}
        assert not db.execute(select(SubmissionEvidence).where(SubmissionEvidence.sufficient.is_(True))).first()
