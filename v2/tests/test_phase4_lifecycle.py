import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.dedup import duplicate_guard
from app.evidence import record_evidence
from app.follow_up import generate_follow_up_draft, get_due_follow_ups, process_due_follow_ups
from app.inbox_matcher import EmailIntent, EmailMessage, process_inbox_email
from app.knowledge_book import KnowledgeBook
from app.models import Application, Job, JobLink, PipelineStage, ReviewTask, SubmissionEvidence
from tests.profile_helpers import memory_db

EXAMPLE_YAML = Path(__file__).parent.parent / "examples" / "profile.example.yaml"


def test_duplicate_guard_prevents_submitting_twice_to_same_or_linked_role():
    db = memory_db()

    # Create two duplicate postings of the same role
    job_a = Job(
        id="greenhouse:acme:101",
        source="greenhouse",
        external_id="101",
        title="Backend Engineer",
        company="Acme Corp",
        url="https://boards.greenhouse.io/acme/jobs/101",
        stage=PipelineStage.ready_to_apply.value,
        canonical_id="greenhouse:acme:101",
    )
    job_b = Job(
        id="lever:acme:202",
        source="lever",
        external_id="202",
        title="Backend Engineer - Remote",
        company="Acme Corp",
        url="https://jobs.lever.co/acme/202",
        stage=PipelineStage.duplicate.value,
        duplicate_of_id=job_a.id,
        canonical_id="lever:acme:202",
    )
    db.add_all([job_a, job_b])
    db.commit()

    # Link them in JobLink
    link = JobLink(job_id=job_b.id, primary_job_id=job_a.id, status="linked", method="exact", score=1.0)
    db.add(link)

    app_a = Application(id="app-101", job_id=job_a.id, stage=PipelineStage.ready_to_apply.value)
    db.add(app_a)
    db.commit()

    # Before submission: job_a can submit
    assert duplicate_guard(db, job_a, action="submit") is None
    # Duplicate job_b cannot submit because it is a linked duplicate
    assert "duplicate of" in (duplicate_guard(db, job_b, action="submit") or "")

    # Now record a successful submission for job_a
    record_evidence(
        db,
        app_a,
        kind="submission",
        confirmation_text="Application submitted successfully. Ref: APP-999",
        candidate_id="APP-999",
        final_url="https://boards.greenhouse.io/acme/jobs/101/confirmation",
    )

    # Now neither job_a nor job_b can be submitted again
    guard_a = duplicate_guard(db, job_a, action="submit")
    assert guard_a is not None
    assert "already submitted" in guard_a

    guard_b = duplicate_guard(db, job_b, action="submit")
    assert guard_b is not None
    assert "duplicate of" in guard_b or "already submitted" in guard_b


def test_inbox_matches_confirmation_email():
    db = memory_db()

    job = Job(
        id="greenhouse:novacorp:301",
        source="greenhouse",
        external_id="301",
        title="Senior Fraud Analyst",
        company="NovaCorp",
        url="https://boards.greenhouse.io/novacorp/jobs/301",
        stage=PipelineStage.submitted.value,
    )
    app = Application(
        id="app-301",
        job_id=job.id,
        stage=PipelineStage.submitted.value,
        confirmation_json=json.dumps({"candidate_id": "NC-888"}),
    )
    db.add_all([job, app])
    db.commit()

    email = EmailMessage(
        sender="recruiting@novacorp.com",
        recipient="applicant@example.com",
        subject="Thank you for applying to NovaCorp!",
        body="Dear Applicant,\n\nWe have received your application for the Senior Fraud Analyst position (Ref: NC-888). We will review your materials shortly.",
    )

    result = process_inbox_email(db, email)
    assert result.matched is True
    assert result.application_id == app.id
    assert result.intent == EmailIntent.CONFIRMATION
    assert result.extracted_reference_id == "NC-888"


def test_inbox_matches_interview_invitation_and_opens_review_task():
    db = memory_db()

    job = Job(
        id="lever:fintech:401",
        source="lever",
        external_id="401",
        title="Disputes Specialist",
        company="FinTech Solutions",
        url="https://jobs.lever.co/fintech/401",
        stage=PipelineStage.submitted.value,
    )
    app = Application(
        id="app-401",
        job_id=job.id,
        stage=PipelineStage.submitted.value,
    )
    db.add_all([job, app])
    db.commit()

    email = EmailMessage(
        sender="talent@fintechsolutions.com",
        recipient="applicant@example.com",
        subject="FinTech Solutions - Invitation to Interview",
        body="Hi Avery,\n\nWe would like to invite you to an interview for the Disputes Specialist role! Please book a time that works for you: https://calendly.com/fintech/interview-avery",
    )

    result = process_inbox_email(db, email)
    assert result.matched is True
    assert result.application_id == app.id
    assert result.intent == EmailIntent.INTERVIEW_INVITATION
    assert "https://calendly.com/fintech/interview-avery" in result.extracted_links

    # Verify review task was created
    task = db.execute(select(ReviewTask).where(ReviewTask.application_id == app.id)).scalar_one()
    assert task.reason_code == "interview_invitation"
    assert "FinTech Solutions" in task.title
    assert "https://calendly.com" in (task.url or "")


def test_inbox_matches_rejection_email_and_updates_stage():
    db = memory_db()

    job = Job(
        id="ashby:omega:501",
        source="ashby",
        external_id="501",
        title="AML Compliance Officer",
        company="Omega Bank",
        url="https://jobs.ashbyhq.com/omega/501",
        stage=PipelineStage.submitted.value,
    )
    app = Application(
        id="app-501",
        job_id=job.id,
        stage=PipelineStage.submitted.value,
    )
    db.add_all([job, app])
    db.commit()

    email = EmailMessage(
        sender="no-reply@ashbyhq.com",
        recipient="applicant@example.com",
        subject="Your application to Omega Bank",
        body="Thank you for your interest in Omega Bank. Unfortunately, we have decided to move forward with other candidates at this time.",
    )

    result = process_inbox_email(db, email)
    assert result.matched is True
    assert result.intent == EmailIntent.REJECTION

    # Verify stages updated
    db.refresh(app)
    db.refresh(job)
    assert app.stage == PipelineStage.rejected.value
    assert job.stage == PipelineStage.rejected.value


def test_follow_up_cadence_detects_due_and_generates_draft():
    db = memory_db()
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)

    job = Job(
        id="greenhouse:metro:601",
        source="greenhouse",
        external_id="601",
        title="Fraud Operations Lead",
        company="Metro Payments",
        url="https://boards.greenhouse.io/metro/jobs/601",
        stage=PipelineStage.submitted.value,
    )
    app = Application(
        id="app-601",
        job_id=job.id,
        stage=PipelineStage.submitted.value,
    )
    db.add_all([job, app])
    db.commit()

    # Submission was 8 days ago
    eight_days_ago = datetime.now(UTC) - timedelta(days=8)
    evidence = SubmissionEvidence(
        application_id=app.id,
        kind="submission",
        sufficient=True,
        created_at=eight_days_ago,
        confirmation_text="Application submitted",
        final_url=job.url,
    )
    db.add(evidence)
    db.commit()

    # 1. Detect due follow-ups
    due = get_due_follow_ups(db)
    assert len(due) == 1
    assert due[0][0].id == app.id
    assert due[0][2] == 1  # follow-up #1

    # 2. Process and create review task
    tasks = process_due_follow_ups(db, kb.profile)
    assert len(tasks) == 1
    assert tasks[0].reason_code == "follow_up_1_due"
    assert "Metro Payments" in tasks[0].title
    assert "Avery Quinn" in tasks[0].detail
    assert "Fraud Operations Lead" in tasks[0].detail
