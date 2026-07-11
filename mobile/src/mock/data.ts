/**
 * Offline/demo data used when the backend is unreachable or not yet
 * configured, so the app is meaningfully explorable without a running
 * backend. Every screen that falls back to this data shows a visible
 * "offline/demo data" banner (see src/components/DemoDataBanner.tsx) —
 * it is never silently substituted.
 */
import type {
  ApplicationRecord,
  AutomationSettings,
  HealthResponse,
  JobPosting,
  Report,
} from "@/types";

const now = () => new Date();
const hoursAgo = (h: number) => new Date(now().getTime() - h * 60 * 60 * 1000).toISOString();
const daysAgo = (d: number) => hoursAgo(d * 24);
const hoursFromNow = (h: number) => new Date(now().getTime() + h * 60 * 60 * 1000).toISOString();

export const mockJobs: JobPosting[] = [
  {
    id: "1",
    source: "greenhouse",
    external_id: "gh-1001",
    title: "Backend Engineer, Platform",
    company: "Northwind Systems",
    location: "Remote (US)",
    remote: true,
    description:
      "We're looking for a backend engineer to help build our core platform APIs. You'll work with Python, FastAPI, and PostgreSQL, and partner closely with the infrastructure team on reliability and scaling.\n\nRequirements:\n- 3+ years backend experience\n- Strong Python fundamentals\n- Experience with REST API design",
    url: "https://boards.greenhouse.io/northwind/jobs/1001",
    discovered_at: hoursAgo(3),
    match_score: 88,
  },
  {
    id: "2",
    source: "lever",
    external_id: "lv-2044",
    title: "Full-Stack Developer",
    company: "Cascade Labs",
    location: "Seattle, WA",
    remote: false,
    description:
      "Join our small product team building tools for independent contractors. You'll ship features end to end across a React frontend and a Django backend.",
    url: "https://jobs.lever.co/cascadelabs/2044",
    discovered_at: hoursAgo(9),
    match_score: 71,
  },
  {
    id: "3",
    source: "generic",
    external_id: "rss-9931",
    title: "Site Reliability Engineer",
    company: "Fernwood Data",
    location: "Remote (Worldwide)",
    remote: true,
    description:
      "Own our on-call rotation, incident response process, and observability stack. Kubernetes and Terraform experience a big plus.",
    url: "https://fernwooddata.com/careers/sre",
    discovered_at: hoursAgo(20),
    match_score: 62,
  },
  {
    id: "4",
    source: "greenhouse",
    external_id: "gh-1002",
    title: "Junior Software Engineer",
    company: "Northwind Systems",
    location: "Austin, TX",
    remote: false,
    description:
      "Entry-level role on our internal tools team. Great mentorship, small team, lots of ownership from day one.",
    url: "https://boards.greenhouse.io/northwind/jobs/1002",
    discovered_at: daysAgo(2),
    match_score: 54,
  },
  {
    id: "5",
    source: "lever",
    external_id: "lv-2050",
    title: "Staff Backend Engineer",
    company: "Marrow Health",
    location: "Remote (US)",
    remote: true,
    description:
      "Lead technical design for our claims-processing pipeline. Deep Python + distributed systems experience required.",
    url: "https://jobs.lever.co/marrowhealth/2050",
    discovered_at: daysAgo(4),
    match_score: 45,
  },
];

export const mockApplications: ApplicationRecord[] = [
  {
    id: "101",
    job_posting_id: "1",
    resume_version_id: "7",
    status: "applied",
    channel: "greenhouse",
    cover_letter_text:
      "Dear Northwind Systems team,\n\nI'm excited to apply for the Backend Engineer, Platform role. My background building high-throughput FastAPI services aligns closely with what you're describing...",
    submitted_at: hoursAgo(2),
    last_status_change: hoursAgo(2),
    notes: "",
    thread_email_id: null,
    created_at: hoursAgo(3),
    job: mockJobs[0],
  },
  {
    id: "102",
    job_posting_id: "2",
    resume_version_id: "7",
    status: "needs_review",
    channel: "generic",
    cover_letter_text: null,
    submitted_at: null,
    last_status_change: hoursAgo(8),
    notes: "Two application-form fields could not be mapped with confidence (referral source, portfolio URL).",
    thread_email_id: null,
    created_at: hoursAgo(9),
    job: mockJobs[1],
  },
  {
    id: "103",
    job_posting_id: "3",
    resume_version_id: "7",
    status: "queued",
    channel: "generic",
    cover_letter_text: "Draft cover letter pending generation.",
    submitted_at: null,
    last_status_change: hoursAgo(19),
    notes: "",
    thread_email_id: null,
    created_at: hoursAgo(20),
    job: mockJobs[2],
  },
  {
    id: "104",
    job_posting_id: "4",
    resume_version_id: "6",
    status: "rejected",
    channel: "greenhouse",
    cover_letter_text:
      "Dear Hiring Team,\n\nI'd like to be considered for the Junior Software Engineer opening...",
    submitted_at: daysAgo(6),
    last_status_change: daysAgo(1),
    notes: "Rejection email received, auto-detected from inbox scan.",
    thread_email_id: "thread-88f2",
    created_at: daysAgo(7),
    job: mockJobs[3],
  },
  {
    id: "105",
    job_posting_id: "5",
    resume_version_id: "7",
    status: "interview",
    channel: "lever",
    cover_letter_text:
      "Dear Marrow Health team,\n\nI'm writing to express strong interest in the Staff Backend Engineer position...",
    submitted_at: daysAgo(10),
    last_status_change: daysAgo(2),
    notes: "Phone screen scheduled for next week.",
    thread_email_id: "thread-1a09",
    created_at: daysAgo(11),
    job: mockJobs[4],
  },
];

export const mockReports: Report[] = [
  {
    id: "501",
    period: "2026-07-10 (daily)",
    generated_at: hoursAgo(1),
    summary: {
      jobs_discovered_today: 5,
      applications_submitted_today: 1,
      applications_submitted_this_week: 3,
      pending_review_count: 1,
      applications_blocked: 0,
      highlights: [
        "Applied to Backend Engineer, Platform at Northwind Systems (match score 88).",
        "Flagged Full-Stack Developer at Cascade Labs for manual review (2 unmapped form fields).",
      ],
      errors: [],
    },
  },
  {
    id: "500",
    period: "2026-07-09 (daily)",
    generated_at: daysAgo(1),
    summary: {
      jobs_discovered_today: 8,
      applications_submitted_today: 2,
      applications_submitted_this_week: 2,
      pending_review_count: 0,
      applications_blocked: 1,
      highlights: [
        "Interview status detected for Marrow Health application from inbox scan.",
      ],
      errors: ["Lever adapter timed out fetching jobs.lever.co/fernwooddata (retried, succeeded)."],
    },
  },
  {
    id: "499",
    period: "2026-07-03 (weekly)",
    generated_at: daysAgo(7),
    summary: {
      jobs_discovered_today: 0,
      applications_submitted_today: 0,
      applications_submitted_this_week: 6,
      pending_review_count: 2,
      applications_blocked: 1,
      highlights: [
        "6 applications submitted this week across 4 companies.",
        "Rejection received from Northwind Systems (Junior Software Engineer).",
      ],
      errors: [],
    },
  },
];

export const mockHealth: HealthResponse = {
  status: "ok",
  version: "0.1.0-demo",
  llm_provider: "ollama",
  llm_model: "llama3.2",
  scheduler_running: true,
  next_scheduled_run: hoursFromNow(2),
};

export const mockSettings: AutomationSettings = {
  automation_enabled: true,
  max_applications_per_day: 15,
  min_delay_between_applications_seconds: 90,
  automation_dry_run: true,
  llm_provider: "ollama",
  llm_model: "llama3.2",
  blacklisted_companies: [],
};
