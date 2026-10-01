# HunterXJob roadmap status

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)

Updated: 2026-10-01

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
| Unattended hosting (v0.3) | Ready | systemd user service on a Linux VM (primary, see `docs/DEPLOY_VM.md`); Termux:Boot fallback |
| Unattended live submit | Blocked | Requires `certified_autonomous` + live gate + unattended flag. Continuous run only dry-runs |

## Safety that stays on

- CAPTCHA, anti-bot, MFA, assessments, and login walls route to review.
- Demographic / legal / work-authorization answers are never inferred from the résumé.
- A submit click is not confirmation. Missing evidence becomes `submission_uncertain`.
- No adapter is `certified_autonomous` in this release. Unattended mode cannot submit.
- Continuous-run cycles never approve applications and only dry-run ones the owner approved. A result claiming a submission engages the kill switch.
- The API refuses requests without a configured key; it is meant to sit behind a tunnel or TLS proxy, never plain public HTTP.

## Real-form dry-run findings (2026-10-01 smoke)

- The boards API is not always complete. Some boards' hosted forms require an employment/education history section or a phone-country picker that the API doesn't list. Browser verification catches these and stops for review, so certification dry-runs should run with `GREENHOUSE_BROWSER_VERIFY=true`.
- Most real forms carry employer-specific questions, legal attestations, and verbose yes/no options. They stop for review until the owner answers them per question.
- Greenhouse embed pages load reCAPTCHA. Dry-runs record it as `submit_boundary=captcha_detected`, so any future live submit would need a manual handoff.

## What still needs a real device

1. Greenhouse live dry-runs against actual employer boards (30 browser-verified real-form plans across employers for certification).
2. Supervised real submissions with owner approval.
3. Session continuity after a manual CAPTCHA or MFA.
4. Inbox-derived confirmation matching.
5. Workday / SmartRecruiters / government adapters beyond detect-only.

Those are operational certification steps, not missing software gates.
