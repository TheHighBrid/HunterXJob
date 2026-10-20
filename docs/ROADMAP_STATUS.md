# HunterXJob roadmap status

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)

Updated: 2026-10-20

## Recovery gates & autonomous roadmap

Scope is set by [RECOVERY_CONTRACT.md](RECOVERY_CONTRACT.md) and expanded by the Autonomous Roadmap Blueprint.
The execution host is plain Linux and macOS at $0: developer workstation plus GitHub-hosted Ubuntu runners (the `gate1` job is pinned to `ubuntu-24.04`).
All 6 implementation phases of the autonomous application pipeline have landed with 528 / 528 passing tests.

| Gate / Milestone | Status | What it proves |
|---|---|---|
| Gate 1: fixture proof on plain Linux | Passed (PR #26). `v2/scripts/gate1.py` runs in CI (`ubuntu-24.04`). 3 independent runs. | Existing v2 API, dry-run route, production form engine plus browser verification, Playwright `chromium.launch()`, repo fixtures over loopback. Checks rendered fields, planned values, trace zip hashed, ledger evidence, zero submit, zero non-GET, no leftover browser/child PIDs, no human input. |
| Gate 2: real public Greenhouse posting | Passed (PR #33). `v2/scripts/gate2.py` (`--live` manual; CI runs `--rehearse` on loopback fixtures). | Production runtime against real employer form, fake identity, dry-run only, trace plus evidence, clean shutdown, zero submit/non-GET. CAPTCHA or challenge fails closed and records findings. |
| Real-profile dry-run batch | Done (PR #34). `v2/scripts/dryrun_batch.py` (30 distinct postings across Greenhouse, Ashby, Lever). | 28 pass, 2 `blocked_by_bot_check` (HTTP 403, failed closed). Zero unverified claims, zero submits. |
| Phase 1: Interactive Browser Form Filling | Passed. `v2/app/browser_filler.py`. | Playwright interactive automation: text/number typing, dropdown selection, radios/checkboxes, file attachments (`set_input_files`), DOM value verification, full-page screenshots, execution traces. |
| Phase 2: Candidate Knowledge Book | Passed. `v2/app/knowledge_book.py`. | Central candidate vault from verified profile YAML; deterministic screening question resolution (work authorization, sponsorship, relocation, non-competes, background checks). |
| Phase 3: CAPTCHA Bridge & Live Submission | Passed. `v2/app/captcha_bridge.py`, `v2/app/submission_engine.py`. | Dual-mode CAPTCHA detection (reCAPTCHA, hCaptcha, Turnstile) with token injection and supervised fallback. Live submit engine with DOM confirmation extraction, candidate ID capture, and cryptographic ledger. |
| Phase 4: Lifecycle Tracker & Deduplication | Passed. `v2/app/dedup.py`, `v2/app/inbox_matcher.py`, `v2/app/follow_up.py`. | Deduplication guard blocking submissions across platforms and requisitions; inbound email confirmation matching; 7-day and 14-day polite follow-up draft generator. |
| Phase 5: SmartRecruiters & Workday Adapters | Passed. `v2/app/smartrecruiters_form.py`, `v2/app/workday_form.py`. | Native parsing, form mapping, and interactive filling for SmartRecruiters and Workday (`*.myworkdayjobs.com`). |
| Phase 6: Fully Autonomous Loop & CLI | Passed. `v2/app/autonomous_loop.py`, `./hunterx autonomous-run`. | Zero-touch continuous loop: discover → score → tailor materials → interactive form fill → CAPTCHA resolution → live submit / verified dry-run → ledger proof → follow-up cadence. |

## Honest readiness

| Capability | Status | Notes |
|---|---|---|
| Discover jobs | Ready | Greenhouse, Lever, Ashby, SmartRecruiters, Workday public boards / feeds with cross-posting dedup and liveness checks |
| Score and shortlist | Ready | Deterministic six-dimension core before optional local AI (Ollama / LLaMA) |
| Candidate Knowledge Book | Ready | Factual candidate vault loaded from YAML profile. Resolves screening questions deterministically |
| Truthful materials | Ready | Guarded ATS résumé PDF (ReportLab) and personalized cover letters. Truth Guard enforces zero hallucination against verified facts |
| In-page browser form filling | Ready | Playwright interactive automation: inputs, selects, radios, checkboxes, and file attachments (`set_input_files`) |
| CAPTCHA resolution bridge | Ready | Dual-mode detection for reCAPTCHA v2/v3, hCaptcha, Turnstile with DOM token injection and supervised fallback |
| Live submission engine | Ready | Safety gated (`allow_live_submission`), confirmation text extraction, candidate ID capture, pre/post-submit DOM hashing, cryptographic ledger |
| Deduplication & submit guard | Ready | Blocks duplicate applications across platforms, reposts, and requisition groups |
| Post-submission lifecycle | Ready | Inbound email confirmation matching & automated 7-day and 14-day polite follow-up draft generator |
| Greenhouse adapter | `human_reviewed_submit` | Real form from boards API / browser, in-page form fill, document upload, live submit |
| Lever adapter | `human_reviewed_submit` | Real form from apply page, in-page form fill, document upload, live submit |
| Ashby adapter | `human_reviewed_submit` | Real form from GraphQL query, in-page form fill, document upload, live submit |
| SmartRecruiters adapter | `human_reviewed_submit` | JobRef parsing, real form schema, in-page form fill, document upload, live submit |
| Workday adapter | `human_reviewed_submit` | JobRef parsing, `data-automation-id` field mapping, in-page form fill, document upload, live submit |
| Manual review gate | Ready | Tasks stop for review on ambiguous questions, login walls, or unverified facts |
| Feature flags / kill switch | Ready | Global emergency kill switch and per-adapter flags |
| Autonomous CLI runner | Ready | `./hunterx autonomous-run` (supports `--live`, `--headed`, `--limit`, `--min-score`) |
| Continuous run scheduler | Ready | Background discover → score → prepare → dry-run cycle with quiet hours, caps, and ledger |
| Mobile app & OpenAPI schema | Ready | React Native remote control app; committed `v2/openapi.json` |

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

## Next milestones & future expansion

1. **Outbound Follow-up Email Dispatch**: Automatic sending of generated 7-day and 14-day polite check-in drafts via authenticated SMTP/OAuth after user approval.
2. **Further ATS Expansion**: Additional adapters for enterprise legacy portals (iCIMS, Taleo, SuccessFactors).
3. **Session Continuity & MFA Handshake**: Seamless token persistence across complex multi-step corporate login portals.
4. **Offline Local LLM Evaluation**: In-depth benchmark scoring comparing local quantized models (e.g. LLaMA 3.2 3B vs 8B) for nuanced cover letter phrasing under Truth Guard.
