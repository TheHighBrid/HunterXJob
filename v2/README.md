# HunterXJob v2

Android-first, zero-cost, local-first job hunting system with bounded autonomy.

## Design contract

> Current scope and execution host follow [docs/RECOVERY_CONTRACT.md](../docs/RECOVERY_CONTRACT.md). Android execution is frozen; the host is plain Linux at $0 (developer box plus GitHub Actions). Gate 1 (`scripts/gate1.py`) proves the dry-run path on Linux with Playwright-owned Chromium against repo fixtures, Gate 2 (`scripts/gate2.py`) proved it once on a real public Greenhouse posting, and `scripts/dryrun_batch.py` ran it read-only over 30 real Greenhouse, Lever and Ashby postings. Both gates have passed; status is in [docs/ROADMAP_STATUS.md](../docs/ROADMAP_STATUS.md).

- Legacy, on hold under the recovery contract: a small cloud Linux VM running the systemd user service ([docs/DEPLOY_VM.md](../docs/DEPLOY_VM.md)), and Termux (or Ubuntu `proot-distro`) on non-root Android. Neither is a supported execution host: the gates have passed, but hosting stays out of scope until the owner opens it in the recovery contract. The phone is a client only.
- Runs on any local Python 3.12 on Linux.
- Uses SQLite, FastAPI, and optional Ollama. No paid API is required.
- Every pipeline stage is durable and resumable.
- Deterministic eligibility runs before local AI.
- `REVIEW` jobs never reach scoring or application creation.
- Submission defaults to `dry_run`. Live submit stays locked until an adapter is `certified_autonomous`.

## Pipeline

`discovered -> eligible|review|rejected -> scored -> shortlisted -> approved -> materials_generated -> ready_to_apply -> applying -> form_filled -> validated -> needs_review|submission_uncertain|submitted -> confirmed`

## Safety layer

- Provenance-aware answer vault
- Form-control engine with confidence and handoff reasons
- Adapter registry with maturity levels
- Submission evidence ledger
- Scheduler caps, quiet hours, and kill switches
- Global and per-platform feature flags

## Quick start

```bash
cd HunterXJob/v2
./hunterx install      # venv, dependencies, .env, and a random API_KEY
./hunterx doctor       # must pass before you expose or schedule anything
./hunterx start
```

Open `http://127.0.0.1:8011`, paste the `API_KEY` from `.env` into the key box, and save.

## Commands

```bash
./hunterx install | start | stop | restart | status | logs | test
./hunterx serve            # foreground server (used by systemd / Termux:Boot)
./hunterx doctor           # installation, auth, network bind, schema, backups, browser
./hunterx cycle            # run one discover -> liveness -> score -> prepare -> dry-run cycle now
./hunterx dedup-scan       # fingerprint older postings and link duplicates (never deletes)
./hunterx liveness         # run due read-only liveness checks for queued postings now
./hunterx backup [--keep N] | backups
./hunterx migrate          # apply schema migrations (also automatic on start)
./hunterx api-key          # print a new random API key
./hunterx version
./hunterx openapi [--check] # write (or verify) the committed API schema v2/openapi.json
./hunterx profile import FILE | import-resume FILE | list [--unverified] | verify KEYS | unverify KEYS
./hunterx materials generate APP_ID | list APP_ID | approve MID | reject MID | preview ...
./hunterx service-install  # systemd user unit (Linux VM)
./hunterx boot-install     # Termux:Boot script (Android fallback)
```

## API authentication

Every endpoint except `GET /api/health` and the static dashboard page needs `X-API-Key: <API_KEY>` (or `Authorization: Bearer <API_KEY>`). The mobile app already sends `X-API-Key`.

- `API_KEY` comes from `.env`, must be at least 32 characters, and is compared in constant time. `./hunterx install` generates one if it is empty.
- No key: every protected request gets `503`. The only exception is `LOCAL_DEV_MODE=true` with `HOST=127.0.0.1`, where direct loopback requests are allowed and anything carrying proxy headers (`X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, ...) is refused. Never use local dev mode behind a reverse proxy or tunnel.
- `/api/health` is open for liveness checks and returns only `ok`, `version` and the auth mode unless the caller is authenticated.
- `/docs` and `/openapi.json` are only served in local dev mode.
- `./hunterx doctor` fails when auth is not configured and warns if `.env` is readable by other users or `HOST` is not loopback.

## Continuous run

Off by default (`CONTINUOUS_RUN_ENABLED=false`). When on, the API process runs a cycle every `CYCLE_INTERVAL_MINUTES` (minimum 5):

1. **discover** from `GREENHOUSE_BOARD_TOKENS` / `LEVER_COMPANIES` / `ASHBY_ORGS` (one failing board does not stop the others). New postings are fingerprinted and linked to an existing posting of the same role (see [Discovery sources](#discovery-sources-dedup-and-liveness));
2. **liveness**: re-check up to `CYCLE_MAX_LIVENESS` queued postings that are due (read-only GETs);
3. **score** up to `CYCLE_MAX_SCORE` new jobs (needs résumé facts; uses the local model only if it is reachable);
4. **prepare** up to `CYCLE_MAX_PREPARE` shortlisted jobs by drafting materials with the local model. You still approve each application; the cycle never approves;
5. **dry-run** up to `CYCLE_MAX_DRY_RUNS` applications you approved (`ready_to_apply`), against the real form, never submitting. Each posting is confirmed live first (prepare and dry-run are skipped for postings that aren't).

Gates, checked every cycle:

- skipped while the global kill switch is engaged, while the `scheduler_paused` flag is on (`PUT /api/flags/scheduler_paused`), or during quiet hours (`QUIET_HOURS_*` in `TIMEZONE`);
- the kill switch is re-checked before each step and each dry-run, and stops the cycle (`aborted`);
- dry-runs need `AUTOMATION_ENABLED=true`, stop at the daily application cap, and count against `MAX_DRY_RUNS_PER_DAY` (manual and scheduled attempts together);
- live submission stays locked: cycles call the same dry-run code as the API, which never submits. If a result ever claimed a submission the cycle engages the kill switch and fails;
- cycles never overlap: an in-process lock plus an `flock` on `run/cycle.lock`, so `./hunterx cycle` and the service cannot run at the same time. Overlaps are recorded as `skipped`.

Every cycle, including skips, is a row in `scheduler_cycles` with per-step counts and errors; a cycle left `running` by a crash is marked `interrupted` by the next one.

- `GET /api/scheduler/status`: on/off, paused, kill switch, in-progress cycle, next run, quiet hours, limits, today's counts, last cycle, last backup.
- `GET /api/scheduler/cycles?limit=20`: the ledger.
- `POST /api/scheduler/run`: start one cycle now (same gates). The response says whether it started, and why not if it didn't (`409` if a cycle is already running).
- `POST /api/scheduler/pause` / `POST /api/scheduler/resume`: set or clear the `scheduler_paused` flag.

## Phone remote control (API)

The mobile app (`../mobile`) is a remote control for these routes. All of them need the API key. Full schema: `openapi.json`, regenerated with `./hunterx openapi` and checked by `tests/test_openapi_snapshot.py`.

| Route | What it does |
|---|---|
| `GET /api/auth/check` | Confirms the key; returns the server version |
| `GET /api/settings` | Current configuration. Never includes secrets: no API key, local service URLs or database path. Public board slugs are listed; generic feed URLs only as a count. `live_submission.locked` is always `true` |
| `PATCH /api/settings` | Changes the safe subset (below). Unknown or locked keys get `422` |
| `GET /api/reports/summary` | Jobs discovered/scored, dry-runs, submissions (always 0), review tasks, cycles, 7-day history |
| `GET /api/jobs?q=&stage=&offset=&limit=` | Job list with scores, open review counts, ATS source, liveness and duplicate links |
| `GET /api/jobs/{id}` | Score breakdown, form status and blockers, application, review tasks, timeline, linked postings, liveness history |
| `GET /api/jobs/{id}/form` | Read-only preview of the live form and fill plan |
| `POST /api/jobs/{id}/check-liveness` | Check the posting at its source now (read-only GET). Same closing policy as the cycle |
| `POST /api/jobs/{id}/unlink-duplicate` | Owner says a linked posting is not a duplicate: it goes back to `discovered` and is scored on its own. `409` if it isn't linked |
| `GET /api/review-tasks?status=open\|closed\|all`, `GET /api/review-tasks/{id}` | Review queue; the detail lists allowed actions and what approval would do |
| `POST /api/review-tasks/{id}/approve` | Shortlists a `review` job, approves a prepared application, or re-queues a blocked one for the next **dry-run**. Refused (`409`) while the kill switch is engaged, for blacklisted companies, or once an application is in a submission stage. Never submits |
| `POST /api/review-tasks/{id}/reject` | Closes the task and rejects (or withdraws) the job |
| `POST /api/review-tasks/{id}/resolve` | Closes the task as `resolved` or `dismissed` without changing the job |
| `GET /api/kill-switch`, `POST /api/kill-switch` | Engaging is always allowed. Disengaging needs `"confirm": true` (`428` otherwise) |
| `GET /api/flags`, `PUT /api/flags/{key}` | `allow_live_submission` and `unattended_mode` can only be turned **off** (`403` when enabling). Unknown flags get `404` |
| `GET /api/backups` | Backup names, sizes and times only |

Settings the phone can change (stored in the `setting_overrides` table and applied over `.env` at startup):
- `automation_enabled` (dry-runs only)
- `max_applications_per_day` (0–50), `max_dry_runs_per_day`, `min_match_score`
- `quiet_hours_start` / `quiet_hours_end` (`HH:MM`)
- `cycle_interval_minutes`, `cycle_max_score`, `cycle_max_prepare`, `cycle_max_dry_runs`
- `target_locations`, `target_keywords`, `excluded_locations`, `excluded_titles`, `blacklisted_companies`
- job sources: `greenhouse_board_tokens`, `lever_companies`, `ashby_orgs` (each a list of public board slugs: letters, digits, `.`, `_`, `-`; at most 60)

Everything else needs `.env` and a restart: `APPLICATION_MODE`, `ALLOW_LIVE_SUBMISSION`, `CONTINUOUS_RUN_ENABLED`, `API_KEY`, generic feed URLs, the AI model and the database.

`CORS_ORIGINS` (comma-separated, empty by default) lets a browser front end such as the Expo web preview call the API. The native app doesn't need it.

## Backups and migrations

- The schema is versioned (`schema_version` table, `app/migrations.py`). Pending migrations run on start (and with `./hunterx migrate`). An existing database is backed up before migrating. A database written by a newer version is refused rather than modified.
- SQLite runs in WAL mode with `synchronous=FULL`.
- `./hunterx backup` writes a consistent, integrity-checked copy to `BACKUP_DIR` (`hunterxjob-v2-<UTC timestamp>.db`, mode 600) and keeps the newest `BACKUP_RETENTION`. While the API runs it also backs up every `BACKUP_INTERVAL_HOURS` (0 disables).
- Restore: stop the service, copy a backup over `DATABASE_PATH` (and delete any `-wal`/`-shm` files next to it), start again.

## Running unattended

- **Linux VM (primary):** `./hunterx service-install`, then `systemctl --user enable --now hunterxjob` and `sudo loginctl enable-linger "$USER"`. Full guide, including Chromium for `GREENHOUSE_BROWSER_VERIFY=true` and how to reach the API from the phone without exposing it: [docs/DEPLOY_VM.md](../docs/DEPLOY_VM.md).
- **Termux (fallback):** install the Termux:Boot app, run `./hunterx boot-install`, and exempt Termux from battery optimisation. Playwright/Chromium does not run on plain Termux, so browser verification is unavailable there.

## Safety modes

- `review`: generate and fill, then wait.
- `dry_run`: validate and capture artifacts, never submit.
- `autonomous`: would submit only when every gate passes. No adapter is certified for that yet.

Default is `dry_run`. `ALLOW_LIVE_SUBMISSION` and `unattended_mode` stay false.

## Usable now

1. Load and verify your profile (see [Profile and application materials](#profile-and-application-materials)).
2. `PUT /api/resume-facts` (free-text summary used only for AI scoring).
3. `POST /api/answers` for explicit policies. Work authorization must be `user` or `policy`.
4. Set Greenhouse board tokens / Lever slugs / Ashby org slugs in `.env` or on the phone (Settings → Job sources). `examples/boards.canada.example.env` is a starter list of public Canadian-relevant boards; it is an example only and nothing reads it.
5. `POST /api/discovery/run` then `POST /api/scoring/run`.
6. Generate materials, approve them, then `POST /api/applications/{id}/apply` for a dry-run.
7. Watch `/api/review-tasks` instead of hoping the bot guessed.

## Profile and application materials

Résumés, cover letters and profile-derived form answers come only from a **verified-facts profile**. Every fact (one contact field, one employer, one degree, one skill, one achievement, ...) records its `source`, its `provenance` (for example `profile.yaml#employment[1]`) and whether you have confirmed it (`verified`). Unverified facts are never used anywhere.

### 1. Load the profile

```bash
cp examples/profile.example.yaml data/profile.yaml    # made-up sample; replace every value with your own
./hunterx profile import data/profile.yaml            # facts start UNVERIFIED (top-level `verified: false`)
./hunterx profile import-resume ~/resume.pdf          # optional: .pdf, .docx, .txt or .md -> UNVERIFIED drafts
./hunterx profile list --unverified
```

The YAML/JSON file has these sections (see `examples/profile.example.yaml`): `contact` (first_name, last_name, email, phone, location, linkedin_url, website_url, github_url), `summary`, `work_authorization` (one entry per country: ISO-2 `country`, `authorized`, `requires_sponsorship`), `employment` (employer, title, location, `start`/`end` as `YYYY-MM`, `current`, nested `achievements` with exact `metrics` and `skills`), `education` (institution, degree, field_of_study, dates), `skills` (name, aliases), `certifications` (name, issuer, date), `languages` (name, proficiency) and `projects`.

Résumé import is heuristic: it reads sections, date ranges and bullets into draft facts and prints warnings for anything it could not place. It never overwrites a fact you already verified. Check every draft.

### 2. Verify facts

```bash
./hunterx profile verify contact:first_name contact:last_name contact:email skill:sql   # keys (or ids) from `profile list`
./hunterx profile unverify skill:sql
```

Or use the phone app (**Settings → Profile & verified facts**): verify/unverify, edit (an edited fact becomes unverified unless you choose *Save and verify*), remove, or paste YAML/JSON/résumé text to import. Setting `verified: true` in your own YAML file and re-importing also verifies, because that file is your own attestation. Materials need at least a verified first and last name, an email and one employment or education fact.

### 3. Generate, review, approve

```bash
./hunterx materials generate <application_id>    # new DRAFT résumé + cover letter (PDF, DOCX, text)
./hunterx materials list <application_id>
./hunterx materials approve <material_id> [--note "..."]
./hunterx materials reject <material_id>
./hunterx materials preview --title "Fraud Analyst" --company "Example Bank" --description-file job.txt --out /tmp/preview
```

- Generation is deterministic: skills that match the job description come first, achievements are ranked by skill and keyword overlap (at most four per role), and employment is listed newest first. Employers, titles, dates, degrees, metrics and skills are copied verbatim from verified facts. Nothing is invented.
- The **truthfulness guard** checks every output line against the facts it cites: structural fields must equal the fact exactly, every number must appear in the source fact, capitalized names must exist in the verified profile, and skills, credentials, months or spelled-out numbers that are not in your facts are rejected. If the deterministic output itself fails the guard, generation stops.
- The scheduler cycle generates drafts automatically for shortlisted/approved jobs once the profile is ready, and opens a `materials_review` task. Drafts are never attached. Approve them per version on the phone (**Job → Résumé & cover letter**) or approve the review task, which approves the pending drafts as-is.
- An approved résumé is required before a dry-run. The cover letter is optional, but only an approved one is ever used. The dry-run evidence records which approved version (id, version, content hash, file hashes) would be attached. Each draft and approval writes `materials_draft` / `materials_approved` entries with hashes to the evidence ledger. If a file or its content changes on disk, or a fact it used is no longer verified, the version is refused and you must regenerate.
- Files are stored per job and version under `MATERIALS_DIR` (default `./data/materials`), and can be downloaded with `GET /api/materials/{id}/file/pdf|docx`.

PDF rendering uses ReportLab with the Bitstream Vera fonts that ship inside the ReportLab wheel. It needs no browser and produces a one-column, ATS-friendly layout with a real, extractable text layer; output is byte-identical for the same input. DOCX output is a minimal hand-built document (no extra dependency). On Termux, Pillow (a ReportLab dependency) may need `pkg install python-pillow` or the libjpeg/zlib headers before `pip install`.

### Optional LLM rewording (off by default)

Set `MATERIALS_LLM_ENABLED=true`, `MATERIALS_LLM_PROVIDER=ollama|openai`, `MATERIALS_LLM_BASE_URL`, `MATERIALS_LLM_MODEL` and (for OpenAI-compatible servers) `MATERIALS_LLM_API_KEY`. The model may only reword the summary and bullets of the facts it is given. Its JSON reply must keep the same ids, and every reworded line goes through the truthfulness guard. On any rejection, error or timeout the deterministic template is used, and the material records the result as `accepted`, `rejected`, `error` or `disabled`.

### Profile answers on real forms

Verified facts also feed the answer vault: `first_name`, `last_name`, `email`, `phone`, `location`, link fields, `current_company`, `current_title`, `work_authorization_{cc}` / `sponsorship_{cc}` (Yes/No per country) and Greenhouse employment/education history rows (`employment_0_company`, `employment_0_title`, `employment_0_start_month` = month name, `employment_0_start_year`, `education_0_school`, `education_0_degree`, ...). Answers you store explicitly in the vault always override profile-derived ones. A history field whose row or section can't be identified unambiguously, or a dropdown whose options don't contain the exact value, goes to review.

## Discovery sources, dedup and liveness

All discovery is read-only GETs against public job-board APIs. Redirects are not followed, and board slugs are validated (letters, digits, `.`, `_`, `-`) before they go into a URL:

| Source | Setting | Feed |
|---|---|---|
| Greenhouse | `GREENHOUSE_BOARD_TOKENS` | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` |
| Lever | `LEVER_COMPANIES` | `api.lever.co/v0/postings/{slug}?mode=json` |
| Ashby | `ASHBY_ORGS` | `api.ashbyhq.com/posting-api/job-board/{org}` (unlisted postings are skipped) |

Every source goes through the same normalization, eligibility gate, scoring and targeting. The lists can be edited on the phone (Settings → Job sources). `examples/boards.canada.example.env` is a starter list of public boards with Canadian postings, checked on 2026-10-01. It is an example only: nothing loads it, and nothing is enabled by default.

**Canonical identity.** Each posting gets `canonical_id = {ats}:{board}:{job id}` (for example `ashby:cohere:5f1c…`). It is unique in the database, so re-discovery updates the row instead of adding one.

**Cross-board duplicates.** Each posting is fingerprinted from its normalized company, title and location, plus a SHA-256 and a 64-bit simhash of the normalized description. A new posting is linked to an earlier one from the same company when:
- the descriptions are identical, or
- the titles are near-identical (≥ 0.92 similarity) and differ only by place words such as "(France)" vs "(Middle East)", and the description simhashes are within 6 bits, or
- the title and location match exactly.

The same role posted in another location is linked too. Nothing is deleted. The duplicate moves to stage `duplicate` with `duplicate_of_id` set, and a `job_links` row records the method and evidence. The primary is the posting that already has an application or got further; otherwise it is the earliest discovered. Preparing materials and dry-running are refused for any posting whose group already has a prepared or dry-run application, so the same role is never prepared or dry-run twice. A link never outlives its primary: if the primary is rejected (for example by the location gate), withdrawn or closed, its copies are released (link kept as `released`), gated on their own, and re-grouped among themselves. That way a Toronto copy of a role is never hidden behind a rejected New York posting. `./hunterx dedup-scan` fingerprints older rows and links them. `POST /api/jobs/{id}/unlink-duplicate` undoes a link.

**Liveness.** A posting is checked at its own source:
- Greenhouse and Lever: the posting API (404/410 means gone).
- Ashby: its public job-posting query (`jobPosting: null` means gone).
- Other sources: the posting page, where only explicit "no longer accepting applications"-style text counts.

The policy is deliberately conservative:

- The first "gone" result marks the posting `suspect`. It is closed only if a second check, at least `LIVENESS_CONFIRM_MINUTES` (30) later, also says gone. Closing moves the job to `closed` and auto-closes its open review tasks with the reason. Postings already in an application stage are never moved; the check is recorded as `close_blocked`.
- Timeouts, network errors, 5xx, 429, unexpected redirects and unparseable responses count as `unknown` and are never a reason to close. The next check backs off exponentially from `LIVENESS_BACKOFF_MINUTES` up to `LIVENESS_BACKOFF_MAX_HOURS`. After `LIVENESS_REVIEW_AFTER_FAILURES` inconclusive checks in a row, one `liveness_review` task is opened.
- A closed posting that is found live again is reopened at `discovered`.
- Every check is stored in `liveness_checks` (outcome, signal, HTTP status, action) as evidence.
- Queued postings are re-checked every `LIVENESS_RECHECK_HOURS`. A posting that disappears from its board feed only gets an immediate check scheduled; it is never closed from the feed alone.
- Before preparing, a live result younger than `LIVENESS_MAX_AGE_PREPARE_HOURS` (6) is reused; otherwise the posting is checked. Before every dry-run, the posting is checked unless a live result is younger than `LIVENESS_MAX_AGE_DRY_RUN_MINUTES` (30). Any result other than `live` defers the step (`liveness_suspect` / `liveness_unknown`) or ends it (`liveness_closed`).

## Real-form dry-runs (Greenhouse, Lever, Ashby)

A dry-run plans against the **employer's real application form**, never a sample:

- The form comes from the public boards API: `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true`. That covers standard fields, custom questions, location questions, EEOC compliance questions, demographic questions, and GDPR consent flags. Only GET is used, only against that host, and redirects are not followed.
- Optional browser verification (`GREENHOUSE_BROWSER_VERIFY=true`, needs `pip install -e '.[browser]'` plus `python -m playwright install chromium`) loads the posting's embed page in headless Chromium. All non-GET requests are aborted, nothing is clicked or typed, and no files are uploaded. Required fields that are on the page but missing from the API (for example employment/education history or the phone-country picker) are added and stop the plan for review. `GREENHOUSE_BROWSER_FALLBACK=true` builds the form from the page when the API is temporarily failing. Comboboxes read only from the page always go to review, because their options can't be checked without interacting.
- **Lever:** the public apply page `GET https://jobs.lever.co/{company}/{posting id}/apply` is server-rendered. The standard fields, the posting's custom question cards and its surveys (EEO/demographic) are read from the HTML, including the question definitions Lever embeds in hidden template inputs. Marketing-consent checkboxes are never checked. hCaptcha is recorded as the submit boundary. With `LEVER_BROWSER_VERIFY=true`, the same page is also loaded in headless Chromium with every non-GET request aborted, and the rendered fields are compared.
- **Ashby:** the application form comes from Ashby's public job-posting GraphQL query, sent as a GET (`jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting`). Only allowlisted read queries are ever sent, and any document that isn't a query is refused before it leaves the process. Field types, required flags, select options and survey (EEO) questions map onto the same form engine. With `ASHBY_BROWSER_VERIFY=true`, the hosted application page is loaded in headless Chromium: the page's own allowlisted read-only GraphQL POSTs are re-issued as GETs, and every other non-GET request is aborted. Nothing is clicked, typed or uploaded.
- All three platforms use the same answer vault, profile facts and review rules. Scoped answers work as `lever:{company}` / `ashby:{org}` (optionally with `:{job id}`). No apply endpoint is ever called.
- If the form can't be fetched (posting closed, network error, unsupported platform), the application goes to review with `form_unavailable` or `form_fetch_failed`. Nothing is faked. Email and generic postings don't have a real-form fetcher, so their dry-runs are refused.
- `GET /api/jobs/{job_id}/form` previews the real form and the fill plan without changing any state. Sensitive and legal values are redacted.

Answer-vault keys for real forms:

- Standard Greenhouse fields use their own names: `first_name`, `last_name`, `email`, `phone`, `resume`, `cover_letter`, `location`, `preferred_name`.
- Recognized custom questions map to shared keys: `linkedin_url`, `website_url`, `github_url`, `current_company`, `current_title`, `salary_expectation`, `willing_to_relocate`, `referral_source`, `country_of_residence`, `time_zone`, `pronouns`.
- Work-authorization and sponsorship keys always name a country: `work_authorization_ca`, `work_authorization_us`, `sponsorship_ca`, … or `*_residence_country` when the question asks about where you live. If a question doesn't say which country, there is no shared key and it goes to review. A single global "Yes" is never reused across countries.
- Any question can be answered on its own by storing its field name (for example `question_64283527`). This works globally or scoped to `greenhouse:{board}` / `greenhouse:{board}:{job_id}`. Employer-specific, conditional, and legal/attestation questions can only be answered this way.
- EEOC and demographic questions are filled only from explicit answers (`gender`, `race_ethnicity`, `veteran_status`, `disability`, …) or from an explicit `voluntary_self_identification=decline` policy. That policy picks the form's single "decline to answer" option. These answers are never inferred.
- A select is filled only when the stored answer exactly matches one of the listed options (ignoring case and whitespace). Otherwise it goes to review.

Smoke-test live postings without the database. This is read-only and submits nothing:

```bash
.venv/bin/python scripts/greenhouse_smoke.py --browser-verify brex:8795500002 https://job-boards.greenhouse.io/gitlab/jobs/8556658002
```

## Tests

```bash
PYTHONPATH=. python -m pytest -q
```
