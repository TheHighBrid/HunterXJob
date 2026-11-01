# HunterXJob roadmap status

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)

Updated: 2026-10-05

## Recovery gates (current scope)

Scope is set by [RECOVERY_CONTRACT.md](RECOVERY_CONTRACT.md). Android execution
is frozen. The execution host is plain Linux at $0: the developer box plus
GitHub-hosted Ubuntu runners (the `gate1` job is pinned to `ubuntu-24.04`).
Gates 1 and 2 have both passed and are in `main`. Anything beyond them
(certification, supervised submission, phone or cloud hosting) still needs the
owner to open it in the recovery contract.

| Gate | Status | What it proves |
|---|---|---|
| Gate 1: fixture proof on plain Linux | Passed (PR #26). `v2/scripts/gate1.py` runs in the CI job `gate1` in `v2-tests.yml` on every push; the latest results are in the job's `gate1-report` artifact. | Existing v2 API, then the dry-run route, then the production form engine plus browser verification, then a Playwright-owned `chromium.launch()`, then repo fixtures over loopback. Requires at least 3 independent runs. Each run checks: rendered fields match the fixture, planned values match a hand-written plan, trace zip hashed, ledger evidence, zero submit and zero non-GET (HEAD counts as non-GET), no leftover browser or child PIDs, and no human input. Not proven, and recorded in the report: real employer site, anti-bot/CAPTCHA, in-page typing, upload, and live submission. |
| Gate 2: one real public Greenhouse posting | Passed (PR #33, merged after owner review). `v2/scripts/gate2.py` (local/manual `--live`; CI runs `--rehearse` on loopback fixtures only). One live dry-run on 2026-10-04 against a public GitLab posting: pass (GET-only, zero submits, one aborted analytics POST, trace and screenshot hashed, `dry_run_review` ledger row, clean shutdown). | Same runtime, fake identity, dry-run only, trace plus evidence plus clean shutdown, and zero submit/non-GET. A CAPTCHA or bot challenge fails closed and is recorded as a finding. Not proven: typing, upload, employer-specific answers, submission, CAPTCHA, more than one employer. |
| Real-profile dry-run batch | Done once (PR #34). `v2/scripts/dryrun_batch.py`, local/manual `--live` only; CI runs `--rehearse`. 30 distinct postings (17 Greenhouse, 11 Ashby, 2 Lever, 18 employers): 28 pass, 2 `blocked_by_bot_check` (HTTP 403, failed closed). Every run passed the safety checks: zero submits, zero uploads, zero non-GET requests sent, every planned answer backed by a verified fact. About 56% of form fields were planned automatically. Artifacts and personal data stay outside the repo. | The Gate 2 code path holds with the owner's real profile across three platforms. Not proven: typing, upload, submission, CAPTCHA, the scheduler and discovery paths. |
| Formal certification, supervised submission, phone, hosting | Not opened yet; needs an owner decision in the recovery contract | — |

Legacy, frozen and kept but not extended: the Android Termux/Ubuntu PRoot
certification setup and the resumable certification flow from PRs #21 to #25
(`docs/GREENHOUSE_CERTIFICATION.md`, `v2/scripts/prepare_proot_certification.sh`,
`v2/scripts/greenhouse_certify.py`).

## Honest readiness

| Capability | Status | Notes |
|---|---|---|
| Discover jobs | Ready | Greenhouse, Lever and Ashby public board APIs, with cross-posting dedup and liveness checks |
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
| Lever / Ashby | `dry_run` | Read-only real-form dry-runs (Lever public apply page; Ashby public GraphQL queries re-issued as GET). Covered by the real-profile batch |
| Email / generic | `dry_run` | Same safety gates |
| Truthful materials | Ready | Verified profile, guarded résumé and cover letter, approval workflow. For finance-domain postings, roles at financial institutions lead the experience list (PR #35); other postings stay reverse-chronological |
| Answer matching on real forms | Landed (PRs #36 and #40) | Classifiers for common screening questions (start date, employment status, background-check consent, languages, education, Canadian citizenship, Ottawa commute) on Greenhouse, Lever and Ashby forms. The vault fills `language_proficiency` from the verified profile and `start_date` from a stored availability date; background-check consent needs an explicit answer. Their effect on real-form coverage is not measured yet |
| Real-form re-run (same 10 postings) | Measured 2026-10-06 on main `18f8ad9` | Real batch: 56.5% on a 10-posting re-run, 48.6% for the same 10 on Oct 4 (+7.9 points). The % is planned ÷ (planned + review). Zero submissions and zero answers without a verified fact or owner answer behind them. 7 runs improved and 3 were unchanged (Coinbase, Samsara, Wealthsimple). New fills: how-did-you-hear, start date, salary, background check, relocation, language, current company and US work authorization. The 4 check failures (Brex, OpenAI, Neo, Jobber) came from `batch_checks.py` still treating salary, start date and relocation as never-answer topics; the check now passes those only when the value exactly matches the owner's stored answer. Not comparable with the earlier 56% (different denominator, 28 runs). Known defects still open: start dates planned into date pickers (OpenAI, Neo), Brex's referral-name field filled as how-did-you-hear, and PolicyMe's French-fluency question getting the joined language list |
| Answer-matching coverage on test forms | Measured offline (PR #42) | `v2/scripts/fixture_answer_coverage.py` plans the 6 sanitized fixture forms in the repo (3 Greenhouse, 1 Lever, 2 Ashby) with the made-up example profile plus example policy answers. 132 controls: 58 filled, 41 sent to review, 33 skipped, so 43.9% planned. Saved in `v2/reports/fixture_answer_coverage.json`. This uses test forms and example answers, so it is not comparable with the 56% from the real-profile batch |
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

## Real-form dry-run findings (2026-10-01 smoke, 2026-10-04 Gate 2 and batch)

- The boards API is not always complete. Some boards' hosted forms require an employment/education history section or a phone-country picker that the API doesn't list. Browser verification catches these and stops for review, so certification dry-runs should run with `GREENHOUSE_BROWSER_VERIFY=true`.
- Most real forms carry employer-specific questions, legal attestations, and verbose yes/no options. They stop for review until the owner answers them per question.
- Greenhouse embed pages load reCAPTCHA. Dry-runs record it as `submit_boundary=captcha_detected`, so any future live submit would need a manual handoff.
- Real pages fire analytics beacons (for example a Snowplow POST on Greenhouse). The read-only router aborts them and the report records each one.
- React forms fire several same-document navigations for one load. The checks allow history updates on the form's own origin and path and still require exactly one document load.
- Real forms nearly always stop for review on employer questions, so a review stop writes a `dry_run_review` ledger row with the trace and screenshot hashes. It never counts as a completed dry-run.
- 2 of 30 batch postings answered the form document with HTTP 403. They failed closed as `blocked_by_bot_check` and nothing retried them.

## Next, after the recovery gates (each needs the owner to open it)

1. Raise the share of fields planned automatically on real forms.
   - Real batch: 56.5% on a 10-posting re-run, 48.6% for the same 10 on Oct 4.
   - Test fixtures (offline regression baseline, not a real-form number): 43.9% planned on 6 forms (PR #42).
   - Next: fix the three defects above, then re-run the same 10 postings to compare.
2. Supervised real submissions with owner approval.
3. Session continuity after a manual CAPTCHA or MFA.
4. Inbox-derived confirmation matching.
5. Workday / SmartRecruiters / government adapters beyond detect-only.
