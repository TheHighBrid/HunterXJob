# HunterXJob v2

Android-first, zero-cost, local-first job hunting system with bounded autonomy.

## Design contract

- Primary host: a small Linux VM (e.g. Oracle Cloud Always Free, Ubuntu 24.04) running the systemd user service, with the Android phone as the remote control through the mobile app. See [docs/DEPLOY_VM.md](../docs/DEPLOY_VM.md).
- Fallback: Termux (or Ubuntu `proot-distro`) on non-root Android, or any local Python 3.12.
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
./hunterx cycle            # run one discover -> score -> prepare -> dry-run cycle now
./hunterx backup [--keep N] | backups
./hunterx migrate          # apply schema migrations (also automatic on start)
./hunterx api-key          # print a new random API key
./hunterx version
./hunterx openapi [--check] # write (or verify) the committed API schema v2/openapi.json
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

1. **discover** from `GREENHOUSE_BOARD_TOKENS` / `LEVER_COMPANIES` (one failing board does not stop the others);
2. **score** up to `CYCLE_MAX_SCORE` new jobs (needs résumé facts; uses the local model only if it is reachable);
3. **prepare** up to `CYCLE_MAX_PREPARE` shortlisted jobs by drafting materials with the local model. You still approve each application; the cycle never approves;
4. **dry-run** up to `CYCLE_MAX_DRY_RUNS` applications you approved (`ready_to_apply`), against the real form, never submitting.

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
| `GET /api/settings` | Current configuration. Never includes secrets: no API key, local service URLs or database path, and job sources only as counts. `live_submission.locked` is always `true` |
| `PATCH /api/settings` | Changes the safe subset (below). Unknown or locked keys get `422` |
| `GET /api/reports/summary` | Jobs discovered/scored, dry-runs, submissions (always 0), review tasks, cycles, 7-day history |
| `GET /api/jobs?q=&stage=&offset=&limit=` | Job list with scores and open review counts |
| `GET /api/jobs/{id}` | Score breakdown, form status and blockers, application, review tasks, timeline |
| `GET /api/jobs/{id}/form` | Read-only preview of the live form and fill plan |
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

Everything else needs `.env` and a restart: `APPLICATION_MODE`, `ALLOW_LIVE_SUBMISSION`, `CONTINUOUS_RUN_ENABLED`, `API_KEY`, job sources, the AI model and the database.

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

1. `PUT /api/resume-facts`
2. `POST /api/answers` for explicit policies. Work authorization must be `user` or `policy`.
3. Set Greenhouse board tokens / Lever slugs in `.env`.
4. `POST /api/discovery/run` then `POST /api/scoring/run`.
5. Generate materials, approve, then `POST /api/applications/{id}/apply` for a dry-run.
6. Watch `/api/review-tasks` instead of hoping the bot guessed.

## Real-form dry-runs (Greenhouse)

A dry-run plans against the **employer's real application form**, never a sample:

- The form comes from the public boards API: `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true`. That covers standard fields, custom questions, location questions, EEOC compliance questions, demographic questions, and GDPR consent flags. Only GET is used, only against that host, and redirects are not followed.
- Optional browser verification (`GREENHOUSE_BROWSER_VERIFY=true`, needs `pip install -e '.[browser]'` plus `python -m playwright install chromium`) loads the posting's embed page in headless Chromium. All non-GET requests are aborted, nothing is clicked or typed, and no files are uploaded. Required fields that are on the page but missing from the API (for example employment/education history or the phone-country picker) are added and stop the plan for review. `GREENHOUSE_BROWSER_FALLBACK=true` builds the form from the page when the API is temporarily failing. Comboboxes read only from the page always go to review, because their options can't be checked without interacting.
- If the form can't be fetched (posting closed, network error, unsupported platform), the application goes to review with `form_unavailable` or `form_fetch_failed`. Nothing is faked. Lever, email, and generic postings don't have a real-form fetcher yet, so their dry-runs are refused.
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
