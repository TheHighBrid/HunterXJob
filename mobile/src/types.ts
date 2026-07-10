/**
 * TypeScript types mirroring the backend's Pydantic schemas.
 *
 * The backend (backend/) is being built in parallel and, at the time this
 * client was written, only exposed config scaffolding (see
 * backend/app/config.py) — the actual Pydantic response models for
 * /api/jobs, /api/applications, /api/reports and /api/settings did not
 * exist yet. These types are derived from docs/ARCHITECTURE.md section 4
 * ("Data model") and section 5 ("API surface"), plus the settings fields
 * already defined in backend/app/config.py. Field names/shapes may need
 * small adjustments once the backend's real schemas land — see
 * mobile/README.md "API assumptions" for the full list of assumptions.
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
  id: number;
  source: JobSource;
  external_id: string;
  title: string;
  company: string;
  location: string | null;
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
  id: number;
  job_posting_id: number;
  resume_version_id: number | null;
  status: ApplicationStatus;
  channel: ApplicationChannel;
  cover_letter_text: string | null;
  submitted_at: string | null; // ISO datetime
  last_status_change: string | null; // ISO datetime
  notes: string | null;
  thread_email_id: string | null;
  created_at: string; // ISO datetime
  /**
   * Denormalized job info, assumed to be embedded by the backend so the
   * list screen doesn't need N follow-up requests. Falls back to
   * `job_posting_id` lookups against the jobs cache if absent.
   */
  job?: JobPosting;
}

export interface CreateApplicationPayload {
  job_posting_id: number;
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
  id: number;
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
  dry_run: boolean;
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
  version?: string;
  llm_provider?: string;
  llm_model?: string;
  scheduler_running?: boolean;
  /** ISO datetime of the next scheduled automation run, if the scheduler is running. */
  next_scheduled_run?: string | null;
}
