"""Pydantic v2 request/response schemas."""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------
class ProfileBase(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    work_authorized: bool = True
    requires_sponsorship: bool = False
    linkedin_url: str = ""
    portfolio_url: str = ""
    github_url: str = ""
    summary: str = ""


class ProfileCreate(ProfileBase):
    pass


class ProfileOut(ProfileBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: dt.datetime
    updated_at: dt.datetime


# --------------------------------------------------------------------------
# Resume
# --------------------------------------------------------------------------
class ResumeUploadRequest(BaseModel):
    resume_json: dict[str, Any] = Field(..., description="Structured resume data")
    is_base: bool = True
    tailored_for_job_id: str | None = None


class ResumeVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    pdf_path: str | None
    tailored_for_job_id: str | None
    match_score: float | None
    is_base: bool
    created_at: dt.datetime
    resume_json: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# Job posting
# --------------------------------------------------------------------------
class JobPostingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source: str
    external_id: str
    title: str
    company: str
    location: str
    remote: bool
    description: str
    url: str
    discovered_at: dt.datetime
    match_score: float | None


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------
class ApplicationCreate(BaseModel):
    job_posting_id: str
    resume_version_id: str | None = None
    channel: str = "generic"
    status: str = "discovered"
    cover_letter_text: str | None = None
    notes: str = ""


class ApplicationUpdate(BaseModel):
    status: str | None = None
    channel: str | None = None
    cover_letter_text: str | None = None
    notes: str | None = None
    resume_version_id: str | None = None
    thread_email_id: str | None = None


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_posting_id: str
    resume_version_id: str | None
    status: str
    channel: str
    cover_letter_text: str | None
    submitted_at: dt.datetime | None
    last_status_change: dt.datetime
    notes: str
    thread_email_id: str | None
    created_at: dt.datetime


# --------------------------------------------------------------------------
# QA answer
# --------------------------------------------------------------------------
class QAAnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    question_pattern: str
    answer_text: str
    tone_variant: str
    usage_count: int


class QAAnswerCreate(BaseModel):
    question_pattern: str
    answer_text: str
    tone_variant: str = "neutral"


# --------------------------------------------------------------------------
# Automation run
# --------------------------------------------------------------------------
class AutomationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    started_at: dt.datetime
    finished_at: dt.datetime | None
    jobs_found: int
    applications_submitted: int
    applications_blocked: int
    errors_json: str


class AutomationRunTriggerResponse(BaseModel):
    message: str
    run_id: str | None = None


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    period: str
    summary: dict[str, Any]
    generated_at: dt.datetime


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
class SettingsOut(BaseModel):
    max_applications_per_day: int
    min_delay_between_applications_seconds: int
    blacklisted_companies: list[str]
    automation_dry_run: bool
    automation_enabled: bool
    llm_provider: str
    llm_model: str


class SettingsUpdate(BaseModel):
    max_applications_per_day: int | None = None
    min_delay_between_applications_seconds: int | None = None
    blacklisted_companies: list[str] | None = None
    automation_dry_run: bool | None = None
    automation_enabled: bool | None = None
    llm_provider: str | None = None


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    version: str
    scheduler_running: bool
    next_scheduled_run: dt.datetime | None
    llm_provider: str
    llm_model: str
