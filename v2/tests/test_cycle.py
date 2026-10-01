import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import cycle as cycle_module
from app import pipeline
from app.config import Settings, get_settings
from app.cycle import CycleDeps, CycleLock, CycleRunner
from app.db import make_engine
from app.discovery import JobRecord
from app.flags import ensure_flags, is_enabled, set_flag
from app.form_engine import ControlType, FormControl
from app.greenhouse_form import FormFetchError, RealForm
from app.migrations import run_migrations
from app.models import Application, Job, PipelineEvent, PipelineStage, SchedulerCycle, SubmissionEvidence
from app.vault_store import upsert_answer

DESCRIPTION = (
    "Investigate fraud alerts, follow AML and compliance controls, document evidence, "
    "support credit-card disputes, and communicate with customers in English and French. "
    "The analyst works with banking operations and payment-risk teams across Canada."
)


def _record(external_id: str, title: str = "Bilingual Fraud Analyst") -> JobRecord:
    return JobRecord(
        source="greenhouse", external_id=external_id, title=title, company="maplebank",
        location="Ottawa, Ontario, Canada", remote=False,
        url=f"https://job-boards.greenhouse.io/maplebank/jobs/{external_id}", description=DESCRIPTION,
    )


class FakeAI:
    def __init__(self, settings=None, ok=True):
        self.ok = ok

    def health(self):
        return {"ok": self.ok}

    def evaluate_job(self, resume, job_text):
        return {"score": 90}

    def draft_materials(self, resume, job_text):
        return {"cover_letter": "Dear team", "screening_answers": {}}


class SimpleFormProvider:
    """Offline stand-in for the live Greenhouse fetch."""

    def __init__(self):
        self.fetched = []

    def fetch(self, job):
        self.fetched.append(job.id)
        controls = [
            FormControl(key=key, label=key.replace("_", " ").title(), control_type=ControlType.TEXT, required=True,
                        confidence=0.9, vault_keys=[key])
            for key in ("first_name", "last_name", "email")
        ]
        return RealForm(platform="greenhouse", source="greenhouse_api", url=job.url, title=job.title, controls=controls)


def _settings(tmp_path, **overrides):
    base = {
        "database_path": str(tmp_path / "hx.db"),
        "backup_dir": str(tmp_path / "backups"),
        "target_locations": "Ottawa,Remote Canada,Canada",
        "target_keywords": "fraud,compliance,bilingual",
        "excluded_locations": "United States,US Remote",
        "excluded_titles": "software engineer",
        "greenhouse_board_tokens": "maplebank",
        "automation_enabled": True,
        "quiet_hours_start": "00:00",
        "quiet_hours_end": "00:00",  # equal start/end disables quiet hours
        "cycle_startup_delay_seconds": 0,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'hx.db'}")
    run_migrations(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        ensure_flags(db)
        for key, value in {"first_name": "Test", "last_name": "Candidate", "email": "candidate@example.test"}.items():
            upsert_answer(db, key=key, value=value)
    # Scoring and material drafting construct LocalAI internally; keep them offline.
    monkeypatch.setattr(pipeline, "LocalAI", FakeAI)
    provider = SimpleFormProvider()
    discovered = {"records": [_record("101"), _record("102")]}

    def discover(settings, errors):
        return list(discovered["records"])

    def make_runner(**overrides):
        deps = CycleDeps(discover=discover, resume_loader=lambda: "Fraud analyst, bilingual, AML/KYC.",
                         ai_factory=FakeAI, form_provider=provider)
        return CycleRunner(_settings(tmp_path, **overrides), Session, tmp_path / "run" / "cycle.lock", deps)

    return {"Session": Session, "make_runner": make_runner, "provider": provider, "discovered": discovered, "tmp": tmp_path}


def _approve_one(Session) -> str:
    with Session() as db:
        application = db.execute(select(Application).join(Job).where(Job.stage == PipelineStage.materials_generated.value)).scalars().first()
        pipeline.approve_application(db, application.id)
        return application.id


def test_full_cycle_prepares_but_only_dry_runs_owner_approved_applications(env):
    runner = env["make_runner"]()
    first = runner.run_once("scheduled")
    assert first["status"] == "completed", first
    steps = first["steps"]
    assert steps["discover"]["added"] == 2
    assert steps["score"]["processed"] == 2
    assert len(steps["prepare"]["prepared"]) == 2
    # Nothing is approved yet, so nothing is dry-run.
    assert steps["dry_run"]["attempted"] == 0
    assert env["provider"].fetched == []

    approved = _approve_one(env["Session"])
    second = runner.run_once("scheduled")
    assert second["status"] == "completed", second
    assert second["steps"]["dry_run"]["results"] == [{"application_id": approved, "status": "dry_run_complete", "reason": None}]
    assert second["steps"]["dry_run"]["submitted"] == 0
    with env["Session"]() as db:
        assert db.execute(select(SubmissionEvidence).where(SubmissionEvidence.sufficient.is_(True))).first() is None
        stages = set(db.execute(select(Job.stage)).scalars())
        assert not stages & {PipelineStage.submitted.value, PipelineStage.confirmed.value}
        assert [c.status for c in db.execute(select(SchedulerCycle).order_by(SchedulerCycle.started_at)).scalars()] == ["completed", "completed"]


@pytest.mark.parametrize("setup, reason", [
    (lambda db: set_flag(db, "global_kill_switch", True), "global kill switch engaged"),
    (lambda db: set_flag(db, "scheduler_paused", True), "scheduler paused"),
])
def test_cycle_is_skipped_by_kill_switch_and_pause(env, setup, reason):
    with env["Session"]() as db:
        setup(db)
    result = env["make_runner"]().run_once()
    assert (result["status"], result["reason"]) == ("skipped", reason)
    assert env["provider"].fetched == []


def test_cycle_is_skipped_in_quiet_hours(env):
    now = datetime.now(UTC)
    start, end = (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")
    result = env["make_runner"](quiet_hours_start=start, quiet_hours_end=end, timezone="UTC").run_once()
    assert (result["status"], result["reason"]) == ("skipped", "quiet hours")


def test_dry_run_needs_automation_enabled(env):
    env["make_runner"]().run_once()
    _approve_one(env["Session"])
    result = env["make_runner"](automation_enabled=False).run_once()
    assert result["steps"]["dry_run"] == {"status": "skipped", "reason": "automation disabled (AUTOMATION_ENABLED=false)"}
    assert env["provider"].fetched == []


def test_daily_dry_run_cap_counts_manual_and_scheduled_attempts(env):
    env["make_runner"]().run_once()
    _approve_one(env["Session"])
    with env["Session"]() as db:
        job = db.execute(select(Job)).scalars().first()
        db.add(PipelineEvent(job_id=job.id, from_stage="ready_to_apply", to_stage="applying", message="earlier manual dry-run"))
        db.commit()
    result = env["make_runner"](max_dry_runs_per_day=1).run_once()
    assert result["steps"]["dry_run"] == {"status": "skipped", "reason": "daily dry-run cap reached"}


def test_per_cycle_dry_run_limit(env):
    env["make_runner"]().run_once()
    _approve_one(env["Session"])
    _approve_one(env["Session"])
    result = env["make_runner"](cycle_max_dry_runs=1).run_once()
    assert result["steps"]["dry_run"]["attempted"] == 1


def test_overlapping_cycles_are_refused_and_recorded(env):
    runner = env["make_runner"]()
    holder = CycleLock(env["tmp"] / "run" / "cycle.lock")
    assert holder.acquire()
    try:
        assert runner.lock.busy()
        assert runner.trigger_async() is False
        result = runner.run_once()
    finally:
        holder.release()
    assert (result["status"], result["reason"]) == ("skipped", "previous cycle still running")
    assert env["provider"].fetched == []
    assert runner.lock.busy() is False


def test_cycle_left_running_by_a_dead_process_is_marked_interrupted(env):
    with env["Session"]() as db:
        db.add(SchedulerCycle(trigger="scheduled", status="running"))
        db.commit()
    env["make_runner"]().run_once()
    with env["Session"]() as db:
        statuses = [c.status for c in db.execute(select(SchedulerCycle).order_by(SchedulerCycle.started_at)).scalars()]
    assert statuses == ["interrupted", "completed"]


def test_kill_switch_engaged_mid_cycle_aborts_remaining_steps(env):
    runner = env["make_runner"]()

    def discover_then_kill(settings, errors):
        with env["Session"]() as db:
            set_flag(db, "global_kill_switch", True, "owner pressed stop")
        return [_record("201")]

    runner.deps.discover = discover_then_kill
    result = runner.run_once()
    assert result["status"] == "aborted"
    assert result["steps"]["discover"]["status"] == "ok"
    assert all(result["steps"][name]["status"] == "skipped" for name in ("score", "prepare", "dry_run"))


def test_failing_step_is_recorded_and_others_still_run(env):
    runner = env["make_runner"]()

    def broken(settings, errors):
        raise ConnectionError("boards API down")

    runner.deps.discover = broken
    result = runner.run_once()
    assert result["status"] == "completed_with_errors"
    assert result["steps"]["discover"] == {"status": "error", "error": "ConnectionError: boards API down"}
    assert result["steps"]["dry_run"]["status"] == "ok"


def test_form_fetch_failure_goes_to_review_not_failure(env):
    env["make_runner"]().run_once()
    approved = _approve_one(env["Session"])

    class Unavailable:
        def fetch(self, job):
            raise FormFetchError("form_unavailable", "posting closed")

    runner = env["make_runner"]()
    runner.deps.form_provider = Unavailable()
    result = runner.run_once()
    assert result["steps"]["dry_run"]["results"] == [{"application_id": approved, "status": "needs_review", "reason": "form_unavailable"}]


def test_a_dry_run_claiming_submission_engages_the_kill_switch(env, monkeypatch):
    env["make_runner"]().run_once()
    _approve_one(env["Session"])
    monkeypatch.setattr(cycle_module, "execute_apply", lambda *a, **k: {"status": "dry_run_complete", "submitted": True})
    result = env["make_runner"]().run_once()
    assert result["status"] == "failed"
    assert "LiveSubmissionLockViolation" in result["error"]
    with env["Session"]() as db:
        assert is_enabled(db, "global_kill_switch") is True


def test_prepare_and_score_skip_cleanly_without_resume_or_ai(env):
    runner = env["make_runner"]()
    runner.deps.resume_loader = lambda: ""
    result = runner.run_once()
    assert result["steps"]["score"] == {"status": "skipped", "reason": "resume facts are not configured"}
    assert result["steps"]["prepare"] == {"status": "skipped", "reason": "resume facts are not configured"}

    runner = env["make_runner"]()
    runner.deps.ai_factory = lambda settings: FakeAI(ok=False)
    result = runner.run_once()
    assert result["steps"]["prepare"] == {"status": "skipped", "reason": "local AI is unavailable"}


def test_background_thread_runs_scheduled_cycle_and_stops(env):
    runner = env["make_runner"](continuous_run_enabled=True, backup_interval_hours=0)
    runner.start()
    try:
        for _ in range(200):
            with env["Session"]() as db:
                if db.execute(select(SchedulerCycle).where(SchedulerCycle.status != "running")).first():
                    break
            time.sleep(0.02)
    finally:
        runner.stop(timeout=5)
    assert not runner.alive
    with env["Session"]() as db:
        assert db.execute(select(SchedulerCycle.trigger)).scalars().first() == "scheduled"


def test_maybe_backup_respects_interval(env):
    runner = env["make_runner"](backup_interval_hours=24)
    first = runner.maybe_backup()
    assert first and first["ok"] is True
    assert runner.maybe_backup() is None
    later = datetime.now(UTC) + timedelta(hours=25)
    assert runner.maybe_backup(now=later)["ok"] is True
    assert env_backups(env) == 2
    assert env["make_runner"](backup_interval_hours=0).maybe_backup() is None


def env_backups(env):
    return len(list((env["tmp"] / "backups").glob("hunterxjob-v2-*.db")))


def test_scheduler_api_reports_status_and_cycles(env, monkeypatch):
    from app import main
    from app.runtime import get_runner

    key = "s" * 40
    runner = env["make_runner"]()
    runner.run_once("manual")
    Session = env["Session"]

    def _get_db():
        with Session() as session:
            yield session

    main.app.dependency_overrides[get_runner] = lambda: runner
    main.app.dependency_overrides[main.get_db] = _get_db
    main.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_key=key)
    try:
        client = TestClient(main.app)
        headers = {"X-API-Key": key}
        status = client.get("/api/scheduler/status", headers=headers).json()
        cycles = client.get("/api/scheduler/cycles?limit=5", headers=headers).json()
        holder = CycleLock(env["tmp"] / "run" / "cycle.lock")
        assert holder.acquire()
        try:
            busy = client.post("/api/scheduler/run", headers=headers)
        finally:
            holder.release()
    finally:
        main.app.dependency_overrides.clear()
    assert status["last_cycle"]["status"] == "completed"
    assert status["live_submission"].startswith("locked")
    assert status["limits"]["max_dry_runs_per_day"] == 10
    assert status["cycle_in_progress"] is False
    assert [c["trigger"] for c in cycles] == ["manual"]
    assert busy.status_code == 409


def test_quiet_hours_and_daily_window_use_the_configured_timezone():
    from app.scheduler import day_start_utc, in_quiet_hours, local_now

    settings = Settings(_env_file=None, timezone="America/Toronto")
    late_evening_toronto = datetime(2026, 10, 1, 3, 30, tzinfo=UTC)  # 23:30 EDT on Sep 30
    assert in_quiet_hours(local_now(settings, late_evening_toronto), "23:00", "07:00") is True
    evening_toronto = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)  # 21:00 EDT, but 01:00 UTC
    assert in_quiet_hours(local_now(settings, evening_toronto), "23:00", "07:00") is False
    assert in_quiet_hours(local_now(Settings(_env_file=None, timezone="UTC"), evening_toronto), "23:00", "07:00") is True
    noon_utc = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)  # 12:00 EDT
    assert in_quiet_hours(local_now(settings, noon_utc), "23:00", "07:00") is False
    assert day_start_utc(settings, noon_utc) == datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
    assert local_now(Settings(_env_file=None, timezone="Not/AZone"), noon_utc).utcoffset() == timedelta(0)
