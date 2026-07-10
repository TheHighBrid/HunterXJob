# HunterXJob — Architecture & Product Plan

## 1. What this is

HunterXJob is a self-hosted, free, automated job-hunting system: it finds job postings, tailors your resume, writes cover letters and outreach emails, fills out and submits applications, tracks status, monitors your inbox for replies, and sends you follow-up reports. It ships as two pieces:

- **Backend engine** (Python/FastAPI) — does all the real work: scraping/searching, AI content generation, form filling, submission, tracking, email monitoring, scheduling. Runs on your own machine, a free-tier VM, or in this repo's Docker setup.
- **Mobile dashboard** (React Native/Expo, compiled to an Android APK) — a control panel: see the job feed, review tracked applications, configure automation, upload your resume, read reports, get push-style status. It talks to the backend over a REST API; it does not run browser automation itself (a phone sandbox can't drive a real browser against LinkedIn/Greenhouse/etc.).

## 2. Lessons pulled from the 20 reference repos

- **Architecture template**: closest to `AutoApply` — FastAPI + typed models + provider-agnostic LLM layer + async task scheduling + audit trail. We use SQLite + APScheduler instead of Postgres+Celery+Redis to keep it genuinely free/zero-ops for a single user (documented upgrade path if you outgrow it).
- **Submitted vs. blocked**: from `ai-job-agent` — never mark an application "submitted" unless it actually was. CAPTCHA/blocked/error states are tracked distinctly. This is a correctness requirement, not optional.
- **Q&A answer bank**: from `GodsScion/Auto_job_applier_linkedIn` + `kcoitk`'s Upwork Q&A repo — recurring application questions ("Are you authorized to work in the US?", "Years of experience with X?") are stored as a pattern → answer bank with tone/length variants, reused and refined per application rather than regenerated from scratch every time.
- **Resume tailoring loop**: from `Resume-Builder` (ResumeHQ) — tailor against the job description, score it (keyword/skill overlap), iterate until a target match score, then render.
- **Resume rendering**: HTML/Jinja2 template → PDF via headless Chromium (Playwright), same idea as `career-ops` and `Awesome-CV`'s LaTeX approach but simpler to deploy (no LaTeX toolchain needed).
- **Job-fit scoring**: weighted multi-dimension score (skills match, seniority match, location/remote fit, salary fit) from `career-ops`'s A–F rubric, simplified to a numeric score used for auto-filtering.
- **Status inference from email**: from `job-ops` — poll the user's inbox (IMAP) for interview/rejection/offer keywords tied to a company/thread, and auto-update application status + trigger follow-up report entries.
- **Background job durability**: from `android-priority-jobqueue` — persist automation run state so a crash/restart doesn't double-submit or lose track of in-flight applications; on the Android side, any local scheduled work uses WorkManager-style persistence principles (the actual automation runs server-side, so this mainly informs the backend's job-run table design).
- **The cautionary lesson**: `Jobs_Applier_AI_Agent_AIHawk` and `darsan-in/Job-Hunter` (CareerBot) — the two projects doing exactly what "fully autonomous, no review, LinkedIn+Indeed" describes — are **both archived**. Mass unreviewed automated submission to LinkedIn/Indeed/Upwork violates those platforms' Terms of Service and is the direct cause of bans and shutdowns in this space. HunterXJob implements full automation as requested, but:
  - never uses stealth/anti-detection tooling (no `undetected-chromedriver`, no CAPTCHA-solving services) — that class of technique exists specifically to defeat a platform's anti-bot defenses, which we won't build regardless of automation mode;
  - enforces a configurable daily submission cap and inter-application delay by default;
  - keeps LinkedIn/Indeed/Upwork as **pluggable adapters behind an explicit opt-in flag**, using your own already-authenticated browser session (you log in once interactively; HunterXJob never stores or handles your password) — since I have no test accounts to validate against here, these ship as a working framework + generic-form-fill fallback rather than hand-tuned per-site selectors, and you should expect to need to adjust selectors as these sites change their DOM;
  - Greenhouse, Lever, and direct-email applications — which have stable, public, ToS-friendly surfaces — are the fully-automated, well-tested v1 path.

## 3. Scope for this build (v1) vs. roadmap

**v1 (built now):**
- Resume profile + structured resume data model, HTML→PDF rendering, tailoring against a job description with a match score
- AI cover letter + application email generation via a pluggable LLM provider (Ollama local model by default — free, no API key; OpenAI-compatible endpoint as an optional override)
- Job discovery via Greenhouse and Lever public job-board APIs (no scraping, no ToS issue) + a generic RSS/JSON job feed adapter
- Fully automated apply flow for: Greenhouse jobs, Lever jobs, and direct-email applications (compose + send with resume/cover letter attached)
- Generic Playwright-based form-filler for arbitrary company career-page ATS forms, using the Q&A answer bank, with per-field confidence scoring (unmapped/low-confidence fields abort that application as "needs_review" rather than guessing)
- Application tracking DB with full status pipeline (`discovered → queued → applied → blocked → interview → offer → rejected → withdrawn`)
- IMAP inbox polling to infer status changes from reply emails
- Scheduler: periodic job search, periodic automation run, periodic report generation
- REST API + API-key auth for the mobile app
- Mobile dashboard app (Expo/React Native): Dashboard, Job Feed, Applications, Reports, Settings screens
- Automated test suite (pytest) covering matching, rendering, adapters (mocked HTTP), API
- Docker Compose for one-command self-hosting (backend + Ollama)

**Explicitly roadmap / not fully built now** (documented in repo, not silently dropped):
- Hand-tuned LinkedIn Easy Apply / Indeed / Upwork selector adapters (scaffolded, opt-in, use-at-your-own-ToS-risk, need real-account testing you'd do yourself)
- Postgres + Celery/Redis for multi-user/high-volume scaling
- Cloud APK distribution (EAS Build) — we build a local debug APK in this environment; a signed release build needs your own keystore

## 4. Data model (core tables)

- `profile` — your identity, contact info, work auth status, links
- `resume_version` — structured resume JSON + rendered PDF path, tailored-for-job-id (nullable = base resume)
- `job_posting` — source, external_id, title, company, location, remote flag, description, url, discovered_at, match_score
- `application` — job_posting_id, resume_version_id, status, channel (greenhouse/lever/email/generic/linkedin/...), cover_letter_text, submitted_at, last_status_change, notes, thread_email_id
- `qa_answer` — question_pattern, answer_text, tone_variant, usage_count
- `automation_run` — started_at, finished_at, jobs_found, applications_submitted, applications_blocked, errors_json
- `report` — period, summary_json, generated_at
- `settings` — key/value (daily cap, delay, enabled channels, LLM provider config)

## 5. API surface (backend, consumed by mobile app)

- `GET /api/jobs` — discovered postings, filter/sort by score
- `GET/POST /api/applications` — list/create/update tracked applications
- `POST /api/automation/run` — trigger an automation cycle now
- `GET /api/reports/latest`, `GET /api/reports` — follow-up/monitoring reports
- `GET/PUT /api/settings` — automation config (caps, channels, LLM provider)
- `POST /api/resume` — upload/update base resume
- `GET /api/health`

All endpoints require an `X-API-Key` header (single-user app; key set at first run).

## 6. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy, SQLite | free, zero external services, easy to self-host |
| Scheduler | APScheduler (in-process) | no Redis/Celery needed for single-user scale |
| Browser automation | Playwright (Chromium) | modern, reliable, used by most reference repos |
| LLM | Ollama (default, local, free) with OpenAI-compatible client override | meets "must be free" requirement |
| Resume rendering | Jinja2 HTML template → Playwright PDF | no LaTeX toolchain dependency |
| Mobile | React Native + Expo, TypeScript | fastest path to a real installable Android APK |
| Packaging | Docker Compose (backend + ollama) | one-command self-host |

## 7. Safety rails (always on, regardless of automation mode)

- Configurable `MAX_APPLICATIONS_PER_DAY` (default 15) and `MIN_DELAY_BETWEEN_APPLICATIONS_SECONDS` (default 90)
- Blacklist (companies/domains never to apply to)
- Every submission attempt is logged with outcome: `submitted`, `blocked` (CAPTCHA/login wall/etc.), `needs_review` (low-confidence form fields), `failed`
- No stored plaintext passwords; LinkedIn/Indeed/Upwork adapters use a Playwright `storageState` file you generate yourself by logging in once
- Dry-run mode (`AUTOMATION_DRY_RUN=true`) available for testing without sending anything — off by default per your "fully autonomous" choice, but recommended for your first run
