from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset (SQLite hands back naive UTC datetimes)."""
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


class PipelineStage(str, enum.Enum):
    discovered = "discovered"
    normalized = "normalized"
    eligible = "eligible"
    review = "review"
    scored = "scored"
    shortlisted = "shortlisted"
    approved = "approved"
    preparing = "preparing"
    materials_generated = "materials_generated"
    materials_reviewed = "materials_reviewed"
    ready_to_apply = "ready_to_apply"
    applying = "applying"
    form_filled = "form_filled"
    validated = "validated"
    needs_review = "needs_review"
    submission_uncertain = "submission_uncertain"
    submitted = "submitted"
    confirmed = "confirmed"
    rejected = "rejected"
    withdrawn = "withdrawn"
    failed = "failed"
    # Linked to an earlier posting of the same role (see app.dedup); never prepared or dry-run.
    duplicate = "duplicate"
    # The posting was confirmed closed or removed (see app.job_liveness).
    closed = "closed"


TERMINAL_STAGES = frozenset({
    PipelineStage.rejected.value,
    PipelineStage.withdrawn.value,
    PipelineStage.failed.value,
    PipelineStage.confirmed.value,
    PipelineStage.closed.value,
})


class AdapterMaturity(str, enum.Enum):
    unsupported = "unsupported"
    detect_only = "detect_only"
    dry_run = "dry_run"
    human_reviewed_submit = "human_reviewed_submit"
    certified_autonomous = "certified_autonomous"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_external"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    location: Mapped[str] = mapped_column(String(255), default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(50), default=PipelineStage.discovered.value, index=True)
    eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    eligibility_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deterministic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    liveness: Mapped[str | None] = mapped_column(String(20), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # Canonical identity: "<ats>:<board>:<job id>" (see app.dedup.canonical_id).
    board: Mapped[str | None] = mapped_column(String(120), nullable=True)
    canonical_id: Mapped[str | None] = mapped_column(String(300), nullable=True, unique=True, index=True)
    # Fuzzy-duplicate fingerprints (see app.dedup).
    dedup_key: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    description_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description_simhash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duplicate_of_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Liveness bookkeeping (see app.job_liveness).
    liveness_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    liveness_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    liveness_failures: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    liveness_next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped[Application | None] = relationship(back_populates="job", uselist=False)


class JobLink(Base):
    """A duplicate link between two postings of the same role. Nothing is deleted."""

    __tablename__ = "job_links"
    __table_args__ = (UniqueConstraint("job_id", "primary_job_id", name="uq_job_link"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    primary_job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    method: Mapped[str] = mapped_column(String(40))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="linked", index=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LivenessCheck(Base):
    """Evidence for every liveness observation of a posting."""

    __tablename__ = "liveness_checks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(30), default="cycle")
    outcome: Mapped[str] = mapped_column(String(20))
    signal: Mapped[str] = mapped_column(String(80), default="")
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(30), default="none")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True, index=True)
    stage: Mapped[str] = mapped_column(String(50), default=PipelineStage.shortlisted.value, index=True)
    mode: Mapped[str] = mapped_column(String(20), default="dry_run")
    tailored_resume_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=lambda: uuid.uuid4().hex)
    adapter_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    adapter_maturity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    submitted_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    job: Mapped[Job] = relationship(back_populates="application")
    evidence: Mapped[list[SubmissionEvidence]] = relationship(back_populates="application")
    review_tasks: Mapped[list[ReviewTask]] = relationship(back_populates="application")


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    from_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(50), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnswerPolicy(Base):
    __tablename__ = "answer_policies"
    __table_args__ = (UniqueConstraint("key", "scope", name="uq_answer_key_scope"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="user")
    scope: Mapped[str] = mapped_column(String(120), default="global", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped[Application | None] = relationship(back_populates="review_tasks")


class SubmissionEvidence(Base):
    __tablename__ = "submission_evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    sufficient: Mapped[bool] = mapped_column(Boolean, default=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    adapter_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    application: Mapped[Application] = relationship(back_populates="evidence")


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SchedulerCycle(Base):
    """Ledger of continuous-run cycles (one row per cycle, including skips)."""

    __tablename__ = "scheduler_cycles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettingOverride(Base):
    """Runtime overrides for the safe, phone-editable subset of settings (see app.runtime_settings)."""

    __tablename__ = "setting_overrides"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProfileFact(Base):
    """One fact about the candidate (see app.profile).

    Only ``verified`` facts are ever used in generated materials or form
    answers. Facts parsed from a résumé start unverified.
    """

    __tablename__ = "profile_facts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    data_json: Mapped[str] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    provenance: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApplicationMaterial(Base):
    """A versioned, generated résumé or cover letter for one application (see app.material_store)."""

    __tablename__ = "application_materials"
    __table_args__ = (UniqueConstraint("application_id", "kind", "version", name="uq_material_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    generator: Mapped[str] = mapped_column(String(20), default="template")
    content_json: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    profile_sha256: Mapped[str] = mapped_column(String(64))
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    docx_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    docx_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SchemaVersion(Base):
    """Applied schema migrations (see app.migrations)."""

    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
