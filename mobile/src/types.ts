/**
 * TypeScript types mirroring the backend's Pydantic schemas
 * (backend/app/schemas.py). IDs are UUID strings (SQLAlchemy String primary
 * keys with a uuid4 default), not numbers.
 */

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

/** Where a job posting or application channel came from. */
export type JobSource =
  | "greenhouse"
  | "lever"
  | "generic"
  | "linkedin"
  | "indeed"
  | "upwork"
  | (string & {});

export interface JobPosting {
  id: string;
  source: JobSource;
  external_id: string;
  title: string;
  company: string;
  location: string;
  remote: boolean;
  description: string;
  url: string;
  discovered_at: string; // ISO datetime
  /** 0-100 job-fit score, null if not yet scored. */
  match_score: number | null;
}

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

export type ApplicationStatus =
  | "discovered"
  | "queued"
  | "applied"
  | "blocked"
  | "needs_review"
  | "interview"
  | "offer"
  | "rejected"
  | "withdrawn";

export const APPLICATION_STATUSES: ApplicationStatus[] = [
  "discovered",
  "queued",
  "applied",
  "blocked",
  "needs_review",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

export type ApplicationChannel =
  | "greenhouse"
  | "lever"
  | "email"
  | "generic"
  | "linkedin"
  | (string & {});

export interface ApplicationRecord {
  id: string;
  job_posting_id: string;
  resume_version_id: string | null;
  status: ApplicationStatus;
  channel: ApplicationChannel;
  cover_letter_text: string | null;
  submitted_at: string | null; // ISO datetime
  last_status_change: string; // ISO datetime
  notes: string;
  thread_email_id: string | null;
  created_at: string; // ISO datetime
  /**
   * The backend does not embed job info on the application record (see
   * GET /api/applications in backend/app/routers/applications.py) — only
   * `job_posting_id`. Detail/list screens resolve this by looking the id up
   * in the jobs cache (src/store/dataCache.ts) instead.
   */
  job?: JobPosting;
}

export interface CreateApplicationPayload {
  job_posting_id: string;
  channel?: ApplicationChannel;
  notes?: string;
}

export interface PatchApplicationPayload {
  status?: ApplicationStatus;
  notes?: string;
}

// ---------------------------------------------------------------------------
// Reports
// ---------------------------------------------------------------------------

export interface ReportSummary {
  jobs_discovered_today: number;
  applications_submitted_today: number;
  applications_submitted_this_week: number;
  pending_review_count: number;
  applications_blocked: number;
  highlights: string[];
  errors: string[];
  [key: string]: unknown;
}

export interface Report {
  id: string;
  period: string;
  generated_at: string; // ISO datetime
  summary: ReportSummary;
}

// ---------------------------------------------------------------------------
// Settings / automation config
// ---------------------------------------------------------------------------

export interface AutomationSettings {
  automation_enabled: boolean;
  max_applications_per_day: number;
  min_delay_between_applications_seconds: number;
  automation_dry_run: boolean;
  /** Read-only, reported by the backend from its own LLM_PROVIDER config. */
  llm_provider: string;
  /** Read-only, reported by the backend (Ollama model name or OpenAI-compatible model). */
  llm_model: string;
  blacklisted_companies: string[];
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: "ok" | "degraded" | (string & {});
  version: string;
  llm_provider: string;
  llm_model: string;
  scheduler_running: boolean;
  /** ISO datetime of the next scheduled automation run, if the scheduler is running. */
  next_scheduled_run: string | null;
}
