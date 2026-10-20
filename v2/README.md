# HunterXJob v2

Local-first, truthful, zero-touch autonomous job discovery, application, and tracking engine.

## Core Architecture

HunterXJob v2 bridges from planning and dry-runs to **full agentic autonomy**:

1. **Discovery & Dedup**: Ingests live job postings from Greenhouse, Lever, Ashby, SmartRecruiters, and Workday. Normalizes them, verifies liveness, and blocks duplicates.
2. **Fit Scoring**: Evaluates candidate profile against requirements using a deterministic six-dimension core and optional local AI.
3. **Truth Guard & Material Tailoring**: Personalizes ATS-friendly résumé PDFs and truthful cover letters without hallucinations—every statement is backed by verified candidate facts.
4. **Interactive Browser Automation**: Uses Playwright to interactively fill form inputs, fuzzy-match dropdowns, toggle checkboxes, attach generated documents (`set_input_files`), and record DOM proofs.
5. **CAPTCHA Bridge**: Detects reCAPTCHA v2/v3, hCaptcha, and Cloudflare Turnstile, injecting solved tokens into the DOM with supervised fallback.
6. **Live Submission Engine**: Gated live submission execution with confirmation screen extraction, candidate ID capture, and cryptographic SHA-256 evidence recording.
7. **Post-Submission Lifecycle**: Inbound email matching and automated 7-day and 14-day follow-up cadence scheduling.

---

## Pipeline Stages

```text
discovered -> eligible|review|rejected -> scored -> shortlisted -> approved ->
materials_generated -> ready_to_apply -> applying -> form_filled -> validated ->
needs_review|submission_uncertain|submitted -> confirmed
```

---

## Quick Start

```bash
cd HunterXJob/v2
./hunterx install      # venv, dependencies, .env, and a random API_KEY
./hunterx doctor       # checks python, virtualenv, playwright, chromium, db, and secrets
./hunterx start        # starts the local web server on port 8011
```

Open `http://127.0.0.1:8011`, paste your `API_KEY` from `.env` into the key box, and click **Save key**.

---

## CLI Commands

```bash
./hunterx install | start | stop | restart | status | logs | test
./hunterx doctor                             # verifies installation, auth, schema, chromium, and env
./hunterx autonomous-run                     # run autonomous loop in verified dry-run mode
./hunterx autonomous-run --live              # run autonomous loop in live submission mode
./hunterx autonomous-run --headed            # run with visible browser window for observation
./hunterx cycle                              # run one discover -> liveness -> score -> prepare cycle
./hunterx dedup-scan                         # fingerprint older postings and link duplicates
./hunterx liveness                           # run due read-only liveness checks
./hunterx backup [--keep N] | backups        # manage timestamped SQLite backups
./hunterx migrate                            # apply schema migrations (also automatic on start)
./hunterx api-key                            # generate/print a random API key
./hunterx openapi [--check]                  # write or verify committed v2/openapi.json schema
./hunterx profile import FILE                # import candidate facts into Knowledge Book
./hunterx profile list [--unverified]        # inspect facts in Knowledge Book
./hunterx profile verify KEYS                # verify candidate facts
./hunterx materials generate APP_ID          # draft tailored résumé + cover letter
./hunterx materials approve MID              # approve draft materials
```

---

## Autonomous Runner (`./hunterx autonomous-run`)

The autonomous engine orchestrates the entire discovery-to-submission lifecycle:

```bash
# Verified dry-run (fills forms, attaches documents, validates DOM without submitting)
./hunterx autonomous-run --limit 5 --min-score 70.0

# Live submission mode (submits application, captures confirmation receipt & candidate ID)
./hunterx autonomous-run --live --limit 3 --min-score 75.0

# Headed visual observation mode
./hunterx autonomous-run --headed --limit 1
```

### Options:
* `--live`: Enables live submission (requires `ALLOW_LIVE_SUBMISSION=true` in `.env`).
* `--limit N`: Maximum applications to process in this run (default: 10).
* `--min-score SCORE`: Minimum fit score threshold (default: 65.0).
* `--profile PATH`: Optional path to custom candidate profile YAML.
* `--headed`: Launches visible browser window for interactive observation.

---

## Supported ATS Platforms

| Platform | Domain | Adapter Maturity | In-Page Form Fill | Document Upload | Live Submit |
|---|---|---|---|---|---|
| **Greenhouse** | `boards.greenhouse.io`, `job-boards.greenhouse.io` | `human_reviewed_submit` | Yes | Yes (PDF/DOCX) | Yes |
| **Lever** | `jobs.lever.co` | `human_reviewed_submit` | Yes | Yes (PDF/DOCX) | Yes |
| **Ashby** | `jobs.ashbyhq.com` | `human_reviewed_submit` | Yes | Yes (PDF/DOCX) | Yes |
| **SmartRecruiters** | `jobs.smartrecruiters.com`, `careers.smartrecruiters.com` | `human_reviewed_submit` | Yes | Yes (PDF/DOCX) | Yes |
| **Workday** | `*.myworkdayjobs.com` | `human_reviewed_submit` | Yes | Yes (PDF/DOCX) | Yes |

---

## API Routes & Endpoints

Every endpoint except `GET /api/health` and the static dashboard requires the header:
`X-API-Key: <API_KEY>` (or `Authorization: Bearer <API_KEY>`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Service health, version, auth mode, AI status, and daily submission count |
| `POST` | `/api/autonomous/run` | Triggers a fully autonomous execution cycle |
| `GET` | `/api/autonomous/status` | Returns autonomy readiness, flags, daily caps, and available adapters |
| `POST` | `/api/discovery/run` | Fetches open postings from configured board tokens |
| `POST` | `/api/scoring/run` | Scores pending jobs against candidate resume facts |
| `GET` | `/api/jobs` | Queries jobs with scores, duplicate links, and pipeline stages |
| `GET` | `/api/jobs/{id}` | Detailed job info, score breakdown, and real-form fill plan |
| `GET` | `/api/applications` | Lists all applications with attached materials and status |
| `POST` | `/api/applications/{id}/generate` | Generates tailored résumé and cover letter drafts |
| `POST` | `/api/applications/{id}/approve` | Approves prepared application materials |
| `POST` | `/api/applications/{id}/apply` | Executes application apply / dry-run |
| `GET` | `/api/review-tasks` | Retrieves open review tasks (voluntary EEOC, custom questions) |
| `POST` | `/api/review-tasks/{id}/approve` | Resolves review task and approves action |
| `POST` | `/api/kill-switch` | Engages global emergency kill-switch |
| `GET` | `/api/flags` | Returns current feature flags and adapter toggles |
| `GET` | `/api/events/{job_id}` | Returns cryptographic event ledger and submission receipts |

---

## Candidate Knowledge Book & Material Generation

Résumés, cover letters, and form answers are derived exclusively from verified facts.

1. **Load Profile**:
   ```bash
   cp examples/profile.example.yaml my_profile.yaml
   ./hunterx profile import my_profile.yaml
   ./hunterx profile list
   ```
2. **Truth Guard**: Every line of generated text is checked against verified facts:
   - Employer names, titles, and dates must match verified records.
   - Numerical metrics must originate from approved achievement bullets.
   - Any unverified claim triggers a review task and stops submission.
3. **PDF & DOCX Rendering**: Résumé PDFs are compiled with ReportLab into clean, ATS-compliant single-column layouts with real text layers.

---

## Tests & Verification

Run the full pytest suite (528 tests):
```bash
.venv/bin/python -m pytest
```

Run Recovery Gate 1 (deterministic 3-run loopback proof):
```bash
.venv/bin/python scripts/gate1.py --runs 3
```

Run Recovery Gate 2 rehearsal:
```bash
.venv/bin/python scripts/gate2.py --rehearse
```
