# HunterXJob roadmap status

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)

Updated: 2026-10-04

## Recovery gates (current scope)

Scope is set by [RECOVERY_CONTRACT.md](RECOVERY_CONTRACT.md). Android execution
is frozen. The execution host is plain Linux at $0: the developer box plus
GitHub-hosted Ubuntu runners (the `gate1` job is pinned to `ubuntu-24.04`).
Nothing outside these gates enters scope until both pass.

| Gate | Status | What it proves |
|---|---|---|
| Gate 1: fixture proof on plain Linux | Implemented: `v2/scripts/gate1.py`, CI job `gate1` in `v2-tests.yml`. The latest run results are in the job's `gate1-report` artifact. | Existing v2 API, then the dry-run route, then the production form engine plus browser verification, then a Playwright-owned `chromium.launch()`, then repo fixtures over loopback. Requires at least 3 independent runs. Each run checks: rendered fields match the fixture, planned values match a hand-written plan, trace zip hashed, ledger evidence, zero submit and zero non-GET (HEAD counts as non-GET), no leftover browser or child PIDs, and no human input. Not proven, and recorded in the report: real employer site, anti-bot/CAPTCHA, in-page typing, upload, and live submission. |
| Gate 2: one real public Greenhouse posting | Runner implemented: `v2/scripts/gate2.py` (local/manual `--live`; CI runs `--rehearse` on loopback fixtures only). One live dry-run done on 2026-10-04 against a public GitLab posting: pass (GET-only, zero submits, one aborted analytics POST, trace and screenshot hashed, `dry_run_review` ledger row, clean shutdown). The owner reviews the result before Gate 2 is declared passed. | Same runtime, fake identity, dry-run only, trace plus evidence plus clean shutdown, and zero submit/non-GET. A CAPTCHA or bot challenge fails closed and is recorded as a finding. Not proven: typing, upload, employer-specific answers, submission, CAPTCHA, more than one employer. |
| 30-run certification, Lever/Ashby, phone, hosting | Out of scope until Gates 1 and 2 pass | — |

Legacy, frozen and kept but not extended: the Android Termux/Ubuntu PRoot
certification setup and the resumable certification flow from PRs #21 to #25
(`docs/GREENHOUSE_CERTIFICATION.md`, `v2/scripts/prepare_proot_certification.sh`,
`v2/scripts/greenhouse_certify.py`).

## Honest readiness

| Capability | Status | Notes |
|---|---|---|
| Discover jobs | Ready | Greenhouse and Lever public APIs |
| Score and shortlist | Ready | Deterministic six-dimension core before optional AI |
| Manual review gate | Ready | `REVIEW` jobs stop before scoring and application creation |
| Answer vault | Ready | Persisted policies with provenance; sensitive fields fail closed |
| Application state machine | Ready | Illegal transitions raise |
| Audit / event ledger | Ready | Pipeline events plus submission evidence rows |
| Idempotency / duplicate guard | Ready | Unique application-per-job and idempotency key |
| Manual-review queue | Ready | Reason codes from the roadmap |
| Feature flags / kill switch | Ready | Global and per-adapter |
| Universal form engine | Ready against real Greenhouse forms (plan only) | Text, select, multi-select, file, checkbox, autocomplete; sensitive/legal/voluntary classification; exact option matching |
| Greenhouse real-form dry-run | Working (read-only) | Real form from the public boards API, with optional headless-browser verification. Fetch failures are flagged, never faked. No sample-form fallback |
| Greenhouse adapter | `human_reviewed_submit` (catalog label) | Plans against the real form. Nothing fills or submits in a browser yet. Live submit locked |
| Lever / email / generic | `dry_run` | Same safety gates |
| SmartRecruiters / Workday / iCIMS / Taleo | `detect_only` | Platform detection only |
| Government portals | Unsupported | Flag exists, adapter not implemented |
| Continuous run (v0.3) | Ready, off by default | Discover → score → prepare → dry-run on an interval inside the API process. Kill switch, pause flag, quiet hours, per-cycle and daily caps; no overlap; every cycle in the `scheduler_cycles` ledger; `GET /api/scheduler/status` |
| API authentication (v0.3) | Ready | `X-API-Key` on every endpoint except health; refuses requests when no key is set, except loopback-only local dev mode |
| Schema migrations and backups (v0.3) | Ready | `schema_version` table, pre-migration backup, `./hunterx backup` with retention, periodic backups, WAL |
| Unattended hosting (v0.3) | On hold (recovery contract) | systemd user service and Termux:Boot scripts exist. No cloud VM or phone hosting until Gates 1 and 2 pass |
| Phone remote-control API (v0.4) | Ready | Settings (safe subset, no secrets), reports summary, jobs with score and form status, review approve/reject/resolve, scheduler pause/resume/run, kill switch, backups list. No route can unlock live submission or record a submission. Schema committed as `v2/openapi.json` |
| Mobile app on v2 (v2.0.0) | Ready | Configurable server URL (Tailscale), API key in secure store, connection test, Dashboard, Jobs, Review, Reports and Settings screens. Types generated from the OpenAPI schema; jest unit tests |
| v1 `backend/` | Legacy / donor | Nothing depends on it, including the mobile app. Kept for reference; deletion deferred to a later milestone |
| Unattended live submit | Blocked | Requires `certified_autonomous` + live gate + unattended flag. Continuous run only dry-runs |

## Safety that stays on

- CAPTCHA, anti-bot, MFA, assessments, and login walls route to review.
- Demographic / legal / work-authorization answers are never inferred from the résumé.
- A submit click is not confirmation. Missing evidence becomes `submission_uncertain`.
- No adapter is `certified_autonomous` in this release. Unattended mode cannot submit.
- Continuous-run cycles never approve applications and only dry-run ones the owner approved. A result claiming a submission engages the kill switch.
- The phone can change only a bounded settings subset. Approving a review task at most queues another dry-run. Live-submission flags can only be turned off through the API.
- The API refuses requests without a configured key; it is meant to sit behind a tunnel or TLS proxy, never plain public HTTP.

## Real-form dry-run findings (2026-10-01 smoke)

- The boards API is not always complete. Some boards' hosted forms require an employment/education history section or a phone-country picker that the API doesn't list. Browser verification catches these and stops for review, so certification dry-runs should run with `GREENHOUSE_BROWSER_VERIFY=true`.
- Most real forms carry employer-specific questions, legal attestations, and verbose yes/no options. They stop for review until the owner answers them per question.
- Greenhouse embed pages load reCAPTCHA. Dry-runs record it as `submit_boundary=captcha_detected`, so any future live submit would need a manual handoff.

## After the recovery gates (not in scope yet)

1. Greenhouse live dry-runs across employer boards (the 30-run certification). Only after Gates 1 and 2.
2. Supervised real submissions with owner approval.
3. Session continuity after a manual CAPTCHA or MFA.
4. Inbox-derived confirmation matching.
5. Workday / SmartRecruiters / government adapters beyond detect-only.
