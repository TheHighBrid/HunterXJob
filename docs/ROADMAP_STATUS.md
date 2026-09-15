# HunterXJob roadmap status

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)

Updated: 2026-09-15

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
| Universal form engine | Ready against fixtures | Text, select, file, radio/checkbox, required, sensitive/legal classification |
| Greenhouse adapter | `human_reviewed_submit` | Local fixture dry-run certified. Live submit locked |
| Lever / email / generic | `dry_run` | Same safety gates |
| SmartRecruiters / Workday / iCIMS / Taleo | `detect_only` | Platform detection only |
| Government portals | Unsupported | Flag exists, adapter not implemented |
| Unattended live submit | Blocked | Requires `certified_autonomous` + live gate + unattended flag |

## Safety that stays on

- CAPTCHA, anti-bot, MFA, assessments, and login walls route to review.
- Demographic / legal / work-authorization answers are never inferred from the résumé.
- A submit click is not confirmation. Missing evidence becomes `submission_uncertain`.
- No adapter is `certified_autonomous` in this release. Unattended mode cannot submit.

## What still needs a real device

1. Greenhouse live dry-runs against actual employer boards.
2. Supervised real submissions with owner approval.
3. Session continuity after a manual CAPTCHA or MFA.
4. Inbox-derived confirmation matching.
5. Workday / SmartRecruiters / government adapters beyond detect-only.

Those are operational certification steps, not missing software gates.
