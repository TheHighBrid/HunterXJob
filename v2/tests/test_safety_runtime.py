from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import Base
from app.evidence import evidence_is_sufficient, record_evidence
from app.flags import ensure_flags, set_flag
from app.models import Application, Job, PipelineStage
from app.pipeline import approve_application, execute_apply
from app.scheduler import can_run_unattended, in_quiet_hours
from app.state_machine import IllegalTransition, assert_transition
from app.vault_store import upsert_answer


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ensure_flags(db)
    return db


def _job_app(db, stage=PipelineStage.ready_to_apply.value):
    job = Job(
        source="greenhouse",
        external_id="99",
        title="Fraud Analyst",
        company="Bank",
        location="Ottawa",
        url="https://boards.greenhouse.io/bank/jobs/99",
        description="Fraud work",
        stage=stage,
        platform="greenhouse",
    )
    db.add(job)
    db.commit()
    application = Application(job_id=job.id, mode="dry_run", adapter_name="greenhouse", cover_letter_text="Hi")
    db.add(application)
    db.commit()
    return job, application


def test_quiet_hours_and_uncertified_adapter_block_unattended():
    db = _db()
    settings = Settings(automation_enabled=True, application_mode="dry_run", max_applications_per_day=5)
    set_flag(db, "unattended_mode", True)
    now = datetime(2026, 9, 15, 23, 30, tzinfo=timezone.utc)
    assert in_quiet_hours(now, "23:00", "07:00") is True
    job, _ = _job_app(db)
    decision = can_run_unattended(db, settings, job, now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    assert decision.allowed is False
    assert "certified_autonomous" in decision.reason


def test_insufficient_click_is_not_confirmation():
    assert evidence_is_sufficient(confirmation_text="clicked submit", candidate_id=None, final_url="https://example.test/apply") is False
    assert evidence_is_sufficient(confirmation_text="Thank you for applying", candidate_id=None, final_url=None) is True


def test_dry_run_apply_never_marks_submitted():
    db = _db()
    settings = Settings(automation_enabled=True, application_mode="dry_run", allow_live_submission=False)
    for key, value in {
        "first_name": "Mo",
        "last_name": "Alem",
        "email": "mo@example.test",
        "phone": "555-0100",
        "resume": "/tmp/resume.pdf",
        "cover_letter": "Hello",
        "work_authorization": "Authorized to work in Canada",
    }.items():
        upsert_answer(db, key=key, value=value, source="user", sensitive=key == "work_authorization")

    job, application = _job_app(db)
    result = execute_apply(db, settings, application.id)
    db.refresh(application)
    db.refresh(job)
    assert result["submitted"] is False
    assert result["status"] == "dry_run_complete"
    assert application.stage == PipelineStage.validated.value
    assert job.stage != PipelineStage.submitted.value


def test_live_submission_flags_do_not_claim_submission_without_adapter_execution():
    db = _db()
    settings = Settings(
        automation_enabled=True,
        application_mode="autonomous",
        allow_live_submission=True,
    )
    for key, value in {
        "first_name": "Mo",
        "last_name": "Alem",
        "email": "mo@example.test",
        "phone": "555-0100",
        "resume": "/tmp/resume.pdf",
        "cover_letter": "Hello",
        "work_authorization": "Authorized to work in Canada",
    }.items():
        upsert_answer(db, key=key, value=value, source="user", sensitive=key == "work_authorization")

    job, application = _job_app(db)
    result = execute_apply(db, settings, application.id)
    db.refresh(application)
    db.refresh(job)

    assert result["status"] == "dry_run_complete"
    assert result["submitted"] is False
    assert application.stage == PipelineStage.validated.value
    assert application.evidence[-1].kind == "dry_run"
    assert application.evidence[-1].sufficient is False
    assert job.stage == PipelineStage.validated.value


def test_kill_switch_blocks_apply():
    db = _db()
    settings = Settings(automation_enabled=True)
    set_flag(db, "global_kill_switch", True)
    _, application = _job_app(db)
    try:
        execute_apply(db, settings, application.id)
        raise AssertionError("kill switch should block apply")
    except RuntimeError as exc:
        assert "kill switch" in str(exc)


def test_illegal_transition_rejected():
    try:
        assert_transition(PipelineStage.discovered.value, PipelineStage.submitted.value)
        raise AssertionError("should be illegal")
    except IllegalTransition:
        pass


def test_approve_requires_materials_to_become_ready():
    db = _db()
    job, application = _job_app(db, stage=PipelineStage.shortlisted.value)
    application.cover_letter_text = None
    db.commit()
    result = approve_application(db, application.id)
    db.refresh(job)
    assert job.stage == PipelineStage.approved.value
    assert result.stage != PipelineStage.ready_to_apply.value


def test_record_evidence_without_proof_is_uncertain():
    db = _db()
    _, application = _job_app(db)
    row = record_evidence(
        db,
        application,
        kind="submission",
        confirmation_text="clicked the button",
        final_url="https://boards.greenhouse.io/bank/jobs/99",
        adapter_name="greenhouse",
    )
    db.refresh(application)
    assert row.sufficient is False
    assert application.stage == PipelineStage.submission_uncertain.value
