# HunterXJob v2

Android-first, zero-cost, local-first job hunting system with bounded autonomy.

## Design contract

- Runs inside Ubuntu `proot-distro` on non-root Android, or any local Python 3.12.
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
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env
./hunterx doctor
./hunterx start
```

Open `http://127.0.0.1:8011`.

## Commands

```bash
./hunterx install
./hunterx start
./hunterx stop
./hunterx status
./hunterx doctor
./hunterx test
```

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
