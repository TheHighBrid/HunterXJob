# HunterXJob architecture (v2)

Updated 2026-10-01 for v2 0.4.0. What is ready and what isn't: [ROADMAP_STATUS.md](ROADMAP_STATUS.md).

## 1. What this is

HunterXJob is a self-hosted job-hunting assistant for one person. It is **not**
fully autonomous. It finds postings, scores them, drafts materials, and
*dry-runs* applications against the employer's real form. It stops and asks the
owner at every boundary it can't handle safely.

**Live submission is locked.** No adapter is `certified_autonomous`, and the
v2 pipeline has no submit code path. Dry-runs fill a form plan in memory, record
`dry_run` evidence, and never click submit. A future certified adapter would
have to submit for real and collect confirmation evidence before an application
could be marked `submitted`. Neither the API nor the phone can unlock this (see §6).

It has two parts:

| Part | Where | Role |
|---|---|---|
| **v2 server** | `v2/` (Python 3.12, FastAPI, SQLAlchemy, SQLite) | Does all the work. Runs as a systemd user service on a small Linux VM (primary) or under Termux on the phone (fallback). See [DEPLOY_VM.md](DEPLOY_VM.md). |
| **Mobile app** | `mobile/` (Expo / React Native, TypeScript) | Remote control for the v2 server: status, kill switch, scheduler, jobs, review queue, settings, reports. Reaches the server over Tailscale (or a TLS tunnel) with the API key. It does no automation itself. |

`backend/` is the **legacy v1 engine**. It is kept as a donor for ideas and
code (PDF rendering, IMAP polling, Playwright adapters), but nothing depends
on it any more: the mobile app talks only to v2. It will be deleted in a later
milestone once anything worth keeping has been ported. Don't build new
features there.

## 2. Pipeline

```
discover ─▶ normalize ─▶ deterministic gate ─┬─▶ reject
                                             ├─▶ review ──(owner)──▶ shortlist
                                             └─▶ eligible ─▶ score (rules + optional local AI)
                                                               │
                                       shortlist ◀─────────────┘ (score ≥ MIN_MATCH_SCORE)
                                           │
                                 prepare materials (local AI)  ── owner approves ──▶ ready_to_apply
                                                                                       │
                                     dry-run: fetch the REAL form (read-only), plan every field
                                     against the answer vault, validate; never submit
                                                                                       │
                                          ┌──────── validated (dry_run evidence) ◀─────┤
                                          └──────── needs_review (review task) ◀───────┘
```

* **State machine** (`app/state_machine.py`): every stage change goes through
  `assert_transition`, and illegal transitions raise. Each change is written to `pipeline_events`.
* **Deterministic core first** (`app/decisioning.py`): a six-dimension weighted
  score with vetoes and review flags runs before any AI. The AI only adjusts the
  score of jobs that pass. AI failures fall back to the rules alone.
* **Answer vault** (`app/answer_vault.py`, `app/vault_store.py`): stored
  answers with provenance and scope. Sensitive, legal, demographic and
  work-authorization fields are never inferred from the résumé. They fail closed and go to review.
* **Discovery** (`app/discovery.py`): read-only public board feeds for
  Greenhouse, Lever and Ashby, normalized into one record shape with a
  canonical id `{ats}:{board}:{job id}`.
* **Dedup** (`app/dedup.py`): company/title/location/description fingerprints
  (SHA-256 + simhash). The same role posted on several boards or locations is
  linked (`job_links`, stage `duplicate`), never deleted. A duplicate guard
  stops a role from being prepared or dry-run twice. If the primary is
  rejected, withdrawn or closed, its copies are released and gated on their own.
* **Liveness** (`app/job_liveness.py`): conservative open/closed checks at the
  posting's source. Two "gone" results at least 30 minutes apart close a job;
  transient errors never do (exponential backoff, plus a review task after
  repeated failures). Every check is stored in `liveness_checks`. Prepare and
  dry-run require a recent `live` result.
* **Real forms** (`app/greenhouse_form.py`, `app/lever_form.py`,
  `app/ashby_form.py`, `app/real_forms.py`): Greenhouse forms come from the
  public boards API, Lever forms from the public apply page, and Ashby forms
  from Ashby's public job-posting query (sent as GET). Each platform has
  optional headless-browser verification (`*_BROWSER_VERIFY`) that aborts
  every non-GET request (Ashby's allowlisted read queries are re-issued as
  GETs). If the form can't be fetched, a review task is opened. There is never
  a sample-form fallback.
* **Review queue** (`app/review_queue.py`, `app/review_actions.py`): CAPTCHA,
  login walls, MFA, assessments, missing legal or sensitive answers, ambiguous
  questions, form-fetch failures and decision reviews become tasks with reason codes.

## 3. Continuous run

`app/cycle.py` runs discover → liveness → score → prepare → dry-run on an interval inside
the API process. It is off by default (`CONTINUOUS_RUN_ENABLED`).

* Gates, checked at the start and again during the cycle:
  * The kill switch, re-checked per step and per item. If it trips mid-cycle, the cycle is marked `aborted`.
  * The `scheduler_paused` flag.
  * Quiet hours in `TIMEZONE`.
  * `AUTOMATION_ENABLED` for dry-runs.
  * Per-cycle caps, plus daily dry-run and application caps.
* Cycles never approve anything. They only dry-run applications the owner already approved.
* If a dry-run result ever reports anything other than `submitted=False`, the
  kill switch engages and the cycle fails (`LiveSubmissionLockViolation`).
* A file lock stops cycles overlapping. Every cycle, including skipped ones, is
  recorded in `scheduler_cycles`.
* The same background thread runs periodic SQLite backups.

## 4. Storage

SQLite in WAL mode with `synchronous=FULL`. Schema migrations are versioned in
`app/migrations.py`; an existing database is backed up before migrating. Backups
use the SQLite online backup API, are integrity-checked, saved with mode 600,
and pruned to the retention count.

| Table | Purpose |
|---|---|
| `jobs` | Postings with stage, scores, eligibility reason, platform, board, canonical id, fingerprints, duplicate link and liveness state |
| `job_links` | Duplicate links between postings (method, score, evidence); undoable |
| `liveness_checks` | Every liveness observation: trigger, outcome, signal, HTTP status, action taken |
| `applications` | One per job: stage, mode (`dry_run`), materials, validation/blocker JSON, idempotency key |
| `pipeline_events` | Audit trail of every stage change (with the scoring report payload) |
| `answer_policies` | The answer vault |
| `review_tasks` | Owner work items with reason codes |
| `submission_evidence` | `dry_run` evidence today; real confirmations in future |
| `feature_flags` | Kill switch, pause, per-adapter flags, live-submission flags (all default off) |
| `scheduler_cycles` | Continuous-run ledger |
| `setting_overrides` | Phone-edited values for the safe settings subset |
| `profile_facts` | Verified-facts candidate profile: one fact per row with category, data, `verified`, source and provenance |
| `application_materials` | Versioned résumé/cover-letter drafts per application: status (`draft`/`approved`/`rejected`/`superseded`), text, content/profile/PDF/DOCX SHA-256 hashes, generator and decision note |
| `schema_version` | Applied migrations |

## 5. API

FastAPI. The schema snapshot is committed at `v2/openapi.json` (`./hunterx openapi`),
and the mobile app's TypeScript types are generated from it
(`mobile/src/api/schema.d.ts`). CI checks that the server, the snapshot and the
generated types agree.

Authentication (`app/security.py`): every route except `GET /api/health` needs
`X-API-Key` (or `Authorization: Bearer`). The key must be at least 32 characters
and is compared in constant time. With no key the API answers 503, except in
`LOCAL_DEV_MODE` with a loopback `HOST`, where only direct loopback requests
without proxy headers are accepted. Interactive docs are served only in local dev mode.

Phone remote-control routes (`app/remote_api.py`):

| Route | Purpose |
|---|---|
| `GET /api/auth/check` | Key check for the app's connection test |
| `GET /api/settings`, `PATCH /api/settings` | Read the configuration (no secrets); change the safe subset |
| `GET /api/reports/summary` | Counts: jobs, scores, dry-runs, submissions (always 0), review, cycles, 7-day history |
| `GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/form` | Job list (search, stage filter), detail with score breakdown, form status and blockers, read-only live form check |
| `GET /api/review-tasks`, `GET /api/review-tasks/{id}` | Review queue and detail with allowed actions |
| `POST /api/review-tasks/{id}/approve` / `reject` / `resolve` | Owner decisions |
| `GET /api/scheduler/status`, `GET /api/scheduler/cycles` | Scheduler state, caps, quiet hours, recent cycles |
| `POST /api/scheduler/pause` / `resume` / `run` | Pause or resume scheduled cycles; start one now (same gates) |
| `GET /api/kill-switch`, `POST /api/kill-switch` | Kill switch |
| `GET /api/flags`, `PUT /api/flags/{key}` | Feature flags (policy below) |
| `GET /api/backups` | Backup names, sizes and times (never contents or paths) |

Profile and materials routes (`app/materials_api.py`):

| Route | Purpose |
|---|---|
| `GET /api/profile` | All facts with verified/unverified counts, readiness and what is missing |
| `POST /api/profile/import` | Import YAML/JSON or plain résumé text as facts (résumé text always unverified) |
| `POST /api/profile/facts`, `PUT /api/profile/facts/{id}` | Add or edit a fact (edits un-verify unless `verified: true`) |
| `POST /api/profile/facts/{id}/verify` / `remove` | Verify/unverify, remove |
| `GET /api/jobs/{id}/materials`, `POST /api/jobs/{id}/materials/generate` | Versions for a job, generate blockers; create new drafts |
| `GET /api/materials/{id}`, `GET /api/materials/{id}/file/{pdf\|docx}` | Text preview, facts used, guard/LLM report; download |
| `POST /api/materials/{id}/approve` / `reject` | Owner decision on one version (never submits) |

Materials pipeline: `profile.py` (facts, import, readiness) → `materials.py`
(deterministic selection and ordering from verified facts and the job text) →
optional `material_llm.py` rewording → `truth_guard.py` (every line must map
to a verified fact; numbers, dates and names must match exactly) →
`render.py` (ReportLab PDF, minimal DOCX) → `material_store.py` (versioned
drafts, approvals, integrity and staleness checks, evidence entries) →
`material_workflow.py` (stage moves and the `materials_review` task).
`profile_vault.py` turns verified facts into answer-vault records (contact,
current role, per-country work authorization, employment/education history
rows, spoken-language proficiency); explicit vault answers override them.
`vault_store.py` also lets a stored `availability_date` (or a similar key) fill
`start_date` when that key is unset. `background_check_consent` is never
derived from the profile.

Operator routes in `app/main.py` (discovery, scoring, materials, manual
dry-run, résumé facts, answers, adapters, events) are unchanged and need the same key.

## 6. Safety invariants

These are enforced on the server and covered by `v2/tests/test_remote_api.py`,
`test_safety_runtime.py` and `test_cycle.py`:

1. **No route can unlock live submission.**
   * `PATCH /api/settings` accepts only the editable subset and returns 422 for
     anything else, including `allow_live_submission`, `application_mode`, `api_key`
     and `continuous_run_enabled`.
   * `PUT /api/flags/allow_live_submission` and `.../unattended_mode` can turn those flags off, never on (403).
   * Unknown flags return 404.
2. **No route can record a real submission.** A test sends hostile payloads to
   every mutating route, then checks that no application or job reached
   `submitted`/`confirmed`/`submission_uncertain` and that the only evidence kinds are `dry_run`,
   `materials_draft` and `materials_approved` (none of which can be marked sufficient; only `submission` can).
   A route-inventory test fails if a new mutating route is added without updating that sweep.
3. **Approving never applies.**
   * What approval does depends on the job:
     * A `review` job is shortlisted.
     * A blocked application (one with an approved résumé) goes back to `ready_to_apply` for the next dry-run.
     * A shortlisted application or one with an approved résumé is approved.
     * A `materials_review` task approves the pending drafts as-is (integrity and staleness checked first).
   * Approval is refused while the kill switch is engaged, for excluded
     employers, and for anything already in a submission stage.
4. **Kill switch.** Engaging is always allowed; disengaging needs `confirm: true` (428 otherwise).
5. **Settings view has no secrets.** It never includes the API key, local service URLs or the database path. Public board slugs are listed (and editable, validated as slugs); generic feed URLs are reported only as a count.
6. **Dry-runs are checked.** If a dry-run claims a submission, the kill switch engages.
   Form fetchers only ever send GETs (for Ashby, allowlisted read-only queries). They never call an apply endpoint and never follow redirects.
   A closed, suspect or unconfirmed posting is never prepared or dry-run, and a duplicate role never twice.
7. **Truthful materials.** Résumés, cover letters and profile answers use only
   verified facts. Generated materials are drafts until approved per version;
   unapproved, tampered or stale (a used fact was un-verified) versions are never
   attached, and the dry-run evidence records exactly which approved version would be.

The editable settings subset is: `automation_enabled`, the daily dry-run and
application caps, `min_match_score`, quiet hours, cycle interval and per-cycle
caps, the targeting lists, and the Greenhouse/Lever/Ashby board lists. These overrides are stored in `setting_overrides`
and layered over `.env` at startup. Everything else needs `.env` and a restart.

## 7. Mobile app

Expo Router with tabs for Dashboard, Jobs, Review, Reports and Settings, plus a
Connection modal and two pushed screens: **Profile** (facts by category,
verify/unverify, edit, paste-import, readiness) and **Materials** for a job
(latest résumé and cover-letter versions, status, hashes, text preview,
approve/reject, regenerate).

* **Connection:** the server URL has no built-in default, only a placeholder. The
  URL is kept in AsyncStorage and the API key in `expo-secure-store` (Android
  Keystore). The connection test checks the URL, reachability, API key and
  minimum server version, and warns about plain `http://` to a public address.
* **Typed client** (`src/api/client.ts`): uses types generated from the OpenAPI
  snapshot. A unit test checks that every endpoint the client calls exists in the snapshot.
* **No demo data:** errors are shown as errors, so the phone never shows made-up state.
* **Tests:** jest-expo unit tests (URL rules, client error mapping, connection
  test, kill-switch request rules, settings diffing, API contract), plus `tsc`.

## 8. Hosting and network

The primary target is an always-free Linux VM running the systemd user service
with Chromium/Playwright installed (so `GREENHOUSE_BROWSER_VERIFY=true` works).
The phone reaches it over **Tailscale** (recommended), so the API is never open
to the internet; Cloudflare Tunnel or Caddy with TLS are the alternatives. The
Android app allows cleartext HTTP so that `http://<vm>.<tailnet>.ts.net:8011`
works, because Tailscale already encrypts the traffic. Use `https://` for anything that leaves the tailnet.
See [DEPLOY_VM.md](DEPLOY_VM.md).

## 9. Legacy v1 (`backend/`), donor only

v1 was designed as a "fully autonomous" engine (Greenhouse, Lever and email
submission, a generic Playwright form filler, IMAP status inference,
APScheduler, Jinja2 → PDF résumés). That design is retired. The v2 pipeline
replaced it with the bounded, dry-run-first model above. v1 stays in the repo
only as reference code and keeps its own tests so CI stays green. It is not
deployed, and the mobile app no longer calls it. The reference-repo lessons it
was built on are in [REFERENCE_REPO_SYNTHESIS.md](REFERENCE_REPO_SYNTHESIS.md).
