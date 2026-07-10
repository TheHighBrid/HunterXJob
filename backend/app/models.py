"""SQLAlchemy ORM models matching docs/ARCHITECTURE.md section 4 (data model)."""
from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


class ApplicationStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    APPLIED = "applied"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ApplicationChannel(str, enum.Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    EMAIL = "email"
    GENERIC = "generic"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    UPWORK = "upwork"


class Profile(Base):
    """The single user's identity, contact info, work-auth status, and links."""

    __tablename__ = "profile"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    work_authorized: Mapped[bool] = mapped_column(Boolean, default=True)
    requires_sponsorship: Mapped[bool] = mapped_column(Boolean, default=False)
    linkedin_url: Mapped[str] = mapped_column(String, default="")
    portfolio_url: Mapped[str] = mapped_column(String, default="")
    github_url: Mapped[str] = mapped_column(String, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ResumeVersion(Base):
    """Structured resume JSON + rendered PDF path. tailored_for_job_id nullable = base resume."""

    __tablename__ = "resume_version"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    resume_json: Mapped[str] = mapped_column(Text)  # JSON-encoded structured resume
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)
    tailored_for_job_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("job_posting.id"), nullable=True
    )
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    tailored_for_job: Mapped["JobPosting | None"] = relationship(
        foreign_keys=[tailored_for_job_id]
    )


class JobPosting(Base):
    """A discovered job posting."""

    __tablename__ = "job_posting"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String)  # greenhouse | lever | generic_feed | ...
    external_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    company: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String, default="")
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String, default="")
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class Application(Base):
    """A tracked application against a job posting."""

    __tablename__ = "application"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    job_posting_id: Mapped[str] = mapped_column(String, ForeignKey("job_posting.id"))
    resume_version_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("resume_version.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, default=ApplicationStatus.DISCOVERED.value)
    channel: Mapped[str] = mapped_column(String, default=ApplicationChannel.GENERIC.value)
    cover_letter_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_status_change: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    notes: Mapped[str] = mapped_column(Text, default="")
    thread_email_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    job_posting: Mapped["JobPosting"] = relationship(foreign_keys=[job_posting_id])
    resume_version: Mapped["ResumeVersion | None"] = relationship(foreign_keys=[resume_version_id])


class QAAnswer(Base):
    """Q&A answer bank: question_pattern -> reusable answer, refined per application."""

    __tablename__ = "qa_answer"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    question_pattern: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    tone_variant: Mapped[str] = mapped_column(String, default="neutral")
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AutomationRun(Base):
    """A record of a single automation-cycle execution."""

    __tablename__ = "automation_run"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    applications_submitted: Mapped[int] = mapped_column(Integer, default=0)
    applications_blocked: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")


class Report(Base):
    """A periodic (daily/weekly) summary report."""

    __tablename__ = "report"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    period: Mapped[str] = mapped_column(String)  # e.g. "daily", "weekly"
    summary_json: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class SettingsKV(Base):
    """Key/value store for runtime-configurable settings (caps, channels, LLM config)."""

    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
