"""Typed request/response models for the phone-facing API.

These drive the OpenAPI schema (``v2/openapi.json``) that the mobile app's
TypeScript types are generated from, so keep them in sync with the views in
``app.job_views``, ``app.reports``, ``app.review_actions`` and
``app.runtime_settings``. FastAPI validates every response against them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _all_required(schema: dict[str, Any]) -> None:
    schema["required"] = sorted(schema.get("properties", {}))


class Out(BaseModel):
    """Response model: every field is always present in the JSON, so the
    schema marks all of them required (the generated TypeScript then has no
    optional fields, only explicit ``| null``)."""

    model_config = ConfigDict(json_schema_extra=_all_required)

# ------------------------------------------------------------------ health/auth


class AuthCheckOut(Out):
    ok: bool
    version: str
    auth: str


class HealthOut(BaseModel):
    ok: bool
    version: str
    auth: str
    # Only present for authenticated callers:
    mode: str | None = None
    automation_enabled: bool | None = None
    allow_live_submission: bool | None = None
    live_submission_locked: bool | None = None
    continuous_run_enabled: bool | None = None
    unattended_allowed: bool | None = None
    unattended_reason: str | None = None
    submissions_today: int | None = None
    open_review_tasks: int | None = None
    flags: dict[str, bool] | None = None
    ai: dict[str, Any] | None = None
    resume_loaded: bool | None = None


# --------------------------------------------------------------------- settings


class LiveSubmissionOut(Out):
    locked: Literal[True]
    env_allow_live_submission: bool
    reason: str


class SourcesOut(Out):
    greenhouse_boards: int
    lever_companies: int
    generic_feeds: int


class SettingsOut(Out):
    auth_mode: str
    application_mode: str
    automation_enabled: bool
    live_submission: LiveSubmissionOut
    continuous_run_enabled: bool
    cycle_interval_minutes: int
    cycle_max_score: int
    cycle_max_prepare: int
    cycle_max_dry_runs: int
    max_dry_runs_per_day: int
    max_applications_per_day: int
    min_match_score: int
    quiet_hours_start: str
    quiet_hours_end: str
    timezone: str
    target_locations: list[str]
    target_keywords: list[str]
    excluded_locations: list[str]
    excluded_titles: list[str]
    blacklisted_companies: list[str]
    sources: SourcesOut
    llm_provider: str
    llm_fast_model: str
    llm_quality_model: str
    greenhouse_browser_verify: bool
    backup_interval_hours: int
    backup_retention: int
    editable: list[str]
    overridden: list[str]


# -------------------------------------------------------------------- scheduler


class CycleOut(Out):
    id: str
    trigger: str
    status: str
    reason: str | None = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    steps: dict[str, Any] = Field(default_factory=dict)


class QuietHoursOut(Out):
    start: str
    end: str
    timezone: str
    active: bool


class LimitsOut(Out):
    cycle_max_score: int
    cycle_max_prepare: int
    cycle_max_dry_runs: int
    max_dry_runs_per_day: int
    max_applications_per_day: int


class TodayOut(Out):
    dry_runs: int
    submissions: int


class BackupStatusOut(Out):
    interval_hours: int
    latest_at: str | None = None
    last_result: dict[str, Any] | None = None


class SchedulerStatusOut(Out):
    continuous_run_enabled: bool
    scheduler_thread_alive: bool
    paused: bool
    kill_switch: bool
    automation_enabled: bool
    live_submission: str
    cycle_in_progress: bool
    current_cycle_id: str | None = None
    interval_minutes: int
    next_run_at: str | None = None
    quiet_hours: QuietHoursOut
    limits: LimitsOut
    today: TodayOut
    backup: BackupStatusOut
    last_cycle: CycleOut | None = None


class RunCycleOut(Out):
    started: bool
    reason: str | None = None
    status_url: str | None = None


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=500)


# ------------------------------------------------------------------ kill switch


class KillSwitchOut(Out):
    engaged: bool
    note: str
    updated_at: str | None = None


class KillSwitchIn(BaseModel):
    engaged: bool
    confirm: bool = False
    note: str = Field(default="", max_length=500)


class FlagIn(BaseModel):
    enabled: bool
    note: str = Field(default="", max_length=500)
    confirm: bool = False


class FlagOut(Out):
    key: str
    enabled: bool
    note: str


# ---------------------------------------------------------------------- backups


class BackupOut(Out):
    name: str
    size_bytes: int
    created_at: str


class BackupsOut(Out):
    items: list[BackupOut]
    latest_at: str | None = None
    retention: int
    interval_hours: int


# ------------------------------------------------------------------------- jobs


class JobOut(Out):
    id: str
    title: str
    company: str
    location: str
    remote: bool
    stage: str
    eligible: bool | None = None
    score: float | None = None
    url: str
    reason: str | None = None
    platform: str | None = None
    discovered_at: str | None = None
    application_id: str | None = None
    application_stage: str | None = None
    open_review_tasks: int = 0


class ScoresOut(Out):
    final: float | None = None
    deterministic: float | None = None
    ai: float | None = None


class DimensionOut(Out):
    name: str
    score: float
    weight: float
    weighted_points: float
    evidence: list[str] = Field(default_factory=list)


class DecisionOut(Out):
    decision: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    vetoes: list[str] = Field(default_factory=list)
    dimensions: list[DimensionOut] = Field(default_factory=list)


class JobApplicationOut(Out):
    id: str
    stage: str
    mode: str
    attempts: int
    last_error: str | None = None
    adapter: str | None = None
    maturity: str | None = None
    has_cover_letter: bool
    updated_at: str | None = None


class BlockedFieldOut(Out):
    key: str
    label: str | None = None
    section: str | None = None
    required: bool = False
    reason: str | None = None


class FormStatusOut(Out):
    state: Literal["no_application", "not_checked", "validated", "unavailable", "blocked", "in_progress"]
    source: str | None = None
    fields_total: int | None = None
    fields_required: int | None = None
    fields_filled: int | None = None
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    blocked_fields: list[BlockedFieldOut] = Field(default_factory=list)
    last_error: str | None = None


class ReviewTaskOut(Out):
    id: str
    reason_code: str
    status: str
    title: str
    detail: str
    url: str | None = None
    application_id: str | None = None
    job_id: str | None = None
    job_title: str | None = None
    company: str | None = None
    created_at: str | None = None
    resolved_at: str | None = None


class EventOut(Out):
    from_stage: str | None = None
    to_stage: str
    message: str
    created_at: str | None = None


class JobDetailOut(JobOut):
    description: str
    liveness: str | None = None
    scores: ScoresOut
    decision: DecisionOut | None = None
    application: JobApplicationOut | None = None
    form_status: FormStatusOut
    review_tasks: list[ReviewTaskOut]
    events: list[EventOut]


class FormFieldOut(Out):
    key: str
    label: str
    type: str
    section: str
    required: bool
    sensitive: bool
    legal: bool
    options: int
    status: str
    reason: str
    value: Any = None


class FormPreviewOut(Out):
    form: dict[str, Any]
    ready: bool
    blockers: list[str]
    fields: list[FormFieldOut]


# ----------------------------------------------------------------------- review


class ReviewJobOut(Out):
    id: str
    title: str
    company: str
    location: str
    stage: str
    score: float | None = None
    url: str
    platform: str | None = None


class ReviewApplicationOut(Out):
    id: str
    stage: str
    mode: str
    attempts: int
    last_error: str | None = None
    adapter: str | None = None
    maturity: str | None = None
    has_materials: bool


class ReviewTaskDetailOut(ReviewTaskOut):
    job: ReviewJobOut | None = None
    application: ReviewApplicationOut | None = None
    blocked_fields: list[BlockedFieldOut] = Field(default_factory=list)
    actions: list[Literal["approve", "reject", "resolve"]]
    approve_effect: str | None = None
    live_submission_locked: Literal[True]


class ReviewActionOut(Out):
    task: ReviewTaskOut
    action: str
    job_stage: str | None = None
    application_stage: str | None = None
    submitted: Literal[False]
    live_submission_locked: Literal[True]


class ReviewResolveIn(BaseModel):
    resolution: Literal["resolved", "dismissed"] = "resolved"


# ---------------------------------------------------------------------- reports


class JobsSummaryOut(Out):
    total: int
    discovered_today: int
    scored: int
    shortlisted: int
    rejected: int
    by_stage: dict[str, int]


class ApplicationsSummaryOut(Out):
    total: int
    ready_to_apply: int
    by_stage: dict[str, int]


class DryRunSummaryOut(Out):
    today: int
    completed_today: int
    completed_total: int
    daily_cap: int


class SubmissionSummaryOut(Out):
    today: int
    total: int
    daily_cap: int


class ReviewSummaryOut(Out):
    open: int
    open_by_reason: dict[str, int]
    closed_total: int


class CyclesSummaryOut(Out):
    last_24h: dict[str, int]
    last: CycleOut | None = None


class HistoryDayOut(Out):
    date: str
    discovered: int
    dry_runs: int
    cycles: int


class SummaryOut(Out):
    generated_at: str
    timezone: str
    live_submission_locked: Literal[True]
    jobs: JobsSummaryOut
    applications: ApplicationsSummaryOut
    dry_runs: DryRunSummaryOut
    submissions: SubmissionSummaryOut
    review: ReviewSummaryOut
    cycles: CyclesSummaryOut
    history: list[HistoryDayOut]
