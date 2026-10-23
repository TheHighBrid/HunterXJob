# HunterXJob roadmap status

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)

Updated: 2026-10-22

## Recovery gates & autonomous roadmap

Scope is set by [RECOVERY_CONTRACT.md](RECOVERY_CONTRACT.md) and expanded by the Autonomous Roadmap Blueprint.
The execution host is plain Linux and macOS at $0: developer workstation plus GitHub-hosted Ubuntu runners (the `gate1` job is pinned to `ubuntu-24.04`).
All 6 core implementation phases plus Phase 7 live production submission hardening have landed with **528 / 528 passing tests** and verified live employer submissions.

| Gate / Milestone | Status | What it proves |
|---|---|---|
| Gate 1: fixture proof on plain Linux | Passed (PR #26). `v2/scripts/gate1.py` runs in CI (`ubuntu-24.04`). 3 independent runs. | Existing v2 API, dry-run route, production form engine plus browser verification, Playwright `chromium.launch()`, repo fixtures over loopback. Checks rendered fields, planned values, trace zip hashed, ledger evidence, zero submit, zero non-GET, no leftover browser/child PIDs, no human input. |
| Gate 2: real public Greenhouse posting | Passed (PR #33). `v2/scripts/gate2.py` (`--live` manual; CI runs `--rehearse` on loopback fixtures). | Production runtime against real employer form, fake identity, dry-run only, trace plus evidence, clean shutdown, zero submit/non-GET. CAPTCHA or challenge fails closed and records findings. |
| Real-profile dry-run batch | Done (PR #34). `v2/scripts/dryrun_batch.py` (30 distinct postings across Greenhouse, Ashby, Lever). | 28 pass, 2 `blocked_by_bot_check` (HTTP 403, failed closed). Zero unverified claims, zero submits. |
| Phase 1: Interactive Browser Form Filling | Passed. `v2/app/browser_filler.py`. | Playwright interactive automation: text/number typing, dropdown selection, combobox autocomplete (`ArrowDown` + `Enter`), `<label>`-dispatched radios, checkboxes, Ashby `[data-field-path]` / `button[data-option]` toggles, file attachments (`set_input_files`), required-field DOM value verification, full-page screenshots, execution traces. |
| Phase 2: Candidate Knowledge Book & Local LLM Resolver | Passed. `v2/app/knowledge_book.py`. | Central candidate vault from verified profile YAML; deterministic screening question resolution (work authorization, sponsorship, relocation, bilingual English/French, financial/risk/fraud experience, compensation, start dates, non-competes, background checks) + local **Ollama (`llama3.2:3b`)** inference (with deterministic heuristic fallback) for custom behavioral/scenario and multiple-choice questions. |
| Phase 3: Smart CAPTCHA Bridge & Single-Session Live Submission | Passed. `v2/app/captcha_bridge.py`, `v2/app/submission_engine.py`. | Unified single-browser fill-and-submit session. Distinguishes passive invisible reCAPTCHA v3/Enterprise from interactive challenges; holds headed browser open up to 180s when interactive `bframe`/hCaptcha puzzles appear post-submit; auto-retries transient ATS spam flags; captures confirmation text, digit-validated candidate IDs, and SHA-256 post-submit screenshot proof. |
| Phase 4: Lifecycle Tracker & Deduplication | Passed. `v2/app/dedup.py`, `v2/app/inbox_matcher.py`, `v2/app/follow_up.py`. | Deduplication guard blocking submissions across platforms and requisitions; inbound email confirmation matching; 7-day and 14-day polite follow-up draft generator. |
| Phase 5: SmartRecruiters & Workday Adapters | Passed. `v2/app/smartrecruiters_form.py`, `v2/app/workday_form.py`. | Native parsing, form mapping, and interactive filling for SmartRecruiters and Workday (`*.myworkdayjobs.com`). |
| Phase 6: Fully Autonomous Loop & Root CLI | Passed. `v2/app/autonomous_loop.py`, `./hunterx autonomous-run`. | Zero-touch continuous loop from repo root or `v2/`: discover → score → tailor materials → interactive form fill → local LLM question resolution → CAPTCHA handling → live submit / verified dry-run → ledger proof → follow-up cadence. |
| Phase 7: Live Production Employer Submissions | **Passed (2026-10-22)**. Verified across 5 live applications on **Cohere**, **Wealthsimple**, and **Float** (`jobs.ashbyhq.com`). | End-to-end live autonomous submissions in production using real Chrome channel + anti-bot stealth, tailored PDF résumé upload, custom scenario question answering via local `llama3.2:3b`, post-submit confirmation banner extraction, SHA-256 `post_submit.png` proof, and Day 7 / Day 14 follow-up scheduling. |

## Honest readiness

| Capability | Status | Notes |
|---|---|---|
| Discover jobs | Ready | Greenhouse, Lever, Ashby, SmartRecruiters, Workday public boards / feeds with cross-posting dedup and liveness checks |
| Score and shortlist | Ready | Deterministic six-dimension core before optional local AI (Ollama / LLaMA) |
| Candidate Knowledge Book | Ready | Factual candidate vault loaded from YAML profile. Resolves standard contact/system fields, UUID-keyed ATS fields, and screening rules deterministically |
| Local LLM custom question answering | Ready | Wires local Ollama (`llama3.2:3b` at `127.0.0.1:11434`) with deterministic heuristic fallback for custom role-specific behavioral/scenario questions and multiple-choice options |
| Truthful materials | Ready | Guarded ATS résumé PDF (ReportLab) and personalized cover letters. Truth Guard enforces zero hallucination against verified facts |
| In-page browser form filling | Ready | Single-session Playwright automation with real Chrome channel (`channel="chrome"` in headed mode) & anti-bot stealth: inputs, combobox autocomplete, selects, `<label>`-bound radios, checkboxes, Ashby `[data-field-path]` / `button[data-option]` toggles, and file attachments (`set_input_files`) |
| CAPTCHA resolution bridge | Ready | Ignores passive invisible reCAPTCHA v3 badges, supports DOM token injection, and holds headed windows up to 180s with operator prompt when interactive post-submit reCAPTCHA `bframe` / hCaptcha challenges appear |
| Live submission engine | **Production Verified** | Safety gated (`allow_live_submission`), unified single-browser session, transient anti-spam retry, confirmation banner extraction, digit-validated candidate ID capture, pre/post-submit SHA-256 screenshot proof, cryptographic ledger |
| Deduplication & submit guard | Ready | Blocks duplicate applications across platforms, reposts, and requisition groups |
| Post-submission lifecycle | Ready | Inbound email confirmation matching & automated 7-day and 14-day polite follow-up draft generator |
| Ashby adapter | **Live Production Verified** | Real form from GraphQL query, UUID & `[data-field-path]` resolution, in-page form fill, PDF upload, live submit verified on Cohere, Wealthsimple, and Float |
| Greenhouse adapter | `human_reviewed_submit` | Real form from boards API / browser, in-page form fill, document upload, live submit ready |
| Lever adapter | `human_reviewed_submit` | Real form from apply page, in-page form fill, document upload, live submit ready |
| SmartRecruiters adapter | `human_reviewed_submit` | JobRef parsing, real form schema, in-page form fill, document upload, live submit ready |
| Workday adapter | `human_reviewed_submit` | JobRef parsing, `data-automation-id` field mapping, in-page form fill, document upload, live submit ready |
| Manual review gate | Ready | Tasks stop for review on login walls, unverified facts, or unresolved required controls |
| Feature flags / kill switch | Ready | Global emergency kill switch and per-adapter flags |
| Autonomous CLI runner | Ready | `./hunterx autonomous-run` (supports `--live`, `--headed`, `--pause`, `--limit`, `--min-score`, `--max-errors`, `--profile`) |
| Continuous run scheduler | Ready | Background discover → score → prepare → dry-run cycle with quiet hours, caps, and ledger |
| Mobile app & OpenAPI schema | Ready | React Native remote control app; committed `v2/openapi.json` |

## Live production submission achievements (2026-10-22)

Five real-world applications were autonomously tailored, filled, and submitted end-to-end with verified post-submit confirmation banners, SHA-256 screenshot proofs, and automated Day 7 / Day 14 follow-up schedules recorded in the SQLite ledger:

| Employer | Role Title | Platform | Fit Score | Confirmation Banner Extracted | Post-Submit Evidence (`SHA-256`) |
|---|---|---|---|---|---|
| **Float** | Bilingual Operations Associate, Risk | Ashby | `89.95` | `Application Success — Your application was successfully submitted!` | `9fad143e7879b8e6...` |
| **Wealthsimple** | Team Lead, Fraud Investigations | Ashby | `87.50` | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `65641aaf51cd2876...` |
| **Cohere** | Forward Deployed Engineer, Agentic Platform | Ashby | `86.20` | `Application Success — Your application has been submitted! Thank you for your interest in a career growth opportunity at Cohere!` | `264d446359157e16...` |
| **Wealthsimple** | Associate, Fraud Investigations Cards and Disputes | Ashby | `84.19` | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `3bab1d56f8049f87...` |
| **Float** | Product Operations Manager, Cards & Spend Management | Ashby | `82.30` | `Application Success — Your application was successfully submitted!` | `a56d4bee197ed1eb...` |

### Key live-form engineering breakthroughs (2026-10-22)

1. **Unified Single-Browser Session**: Previously, form filling and live submission ran across separate browser contexts, losing state on SPA forms. Unifying form fill, file upload, CAPTCHA check, submit click, and confirmation polling inside a single Playwright context eliminated state loss.
2. **Ashby UUIDs, Toggle Buttons & Controlled Radio Labels**: Ashby forms assign dynamic UUIDs to custom fields and render Yes/No buttons (`button[data-option]`) or custom-styled radio inputs where `.check(force=True)` does not trigger React state updates. Clicking the associated `<label>` element inside `[data-field-path]` containers reliably updates React form state.
3. **Invisible reCAPTCHA v3 vs. Interactive Post-Submit Challenges**: Ashby embeds an invisible reCAPTCHA Enterprise badge on load (`size=invisible`), which previously triggered false-positive pre-submit blocks. Ignoring passive invisible badges pre-submit while polling `is_captcha_challenge_visible()` post-submit (holding the headed window open up to 180s if an interactive `bframe` puzzle opens) enabled seamless submissions.
4. **Real Chrome Channel & Anti-Bot Stealth**: Launching the real macOS Chrome binary (`channel="chrome"`) in headed mode with `--disable-blink-features=AutomationControlled`, `navigator.webdriver = undefined`, and automatic 3.5s retry on transient Ashby anti-spam prompts resolved false spam rejections.
5. **Local Ollama LLM (`llama3.2:3b`) Custom Question Resolution**: Role-specific situational multiple-choice questions and open-ended behavioral textareas (e.g., coaching under pressure, handling operational risk incidents) are dynamically answered via local `llama3.2:3b` grounded in the candidate's verified profile facts.

## Safety that stays on

- CAPTCHA, anti-bot, MFA, assessments, and login walls route to review or supervised headed hold.
- Demographic / legal / work-authorization answers are never inferred from unverified text; they come from verified profile facts.
- A submit click is not confirmation. Missing evidence becomes `submission_uncertain`; candidate ID regexes require digits to prevent prose false positives.
- Continuous-run background cycles never approve applications and only dry-run ones the owner approved. A background dry-run result claiming a submission engages the kill switch.
- The phone can change only a bounded settings subset. Approving a review task at most queues another dry-run. Live-submission flags can only be turned off through the remote API.
- The API refuses requests without a configured key; it is meant to sit behind a tunnel or TLS proxy, never plain public HTTP.

## Earlier real-form dry-run findings (2026-10-01 smoke, 2026-10-04 Gate 2 and batch)

- The boards API is not always complete. Some boards' hosted forms require an employment/education history section or a phone-country picker that the API doesn't list. Browser verification catches these and stops for review, so certification dry-runs should run with `GREENHOUSE_BROWSER_VERIFY=true`.
- Greenhouse embed pages load reCAPTCHA. Dry-runs record it as `submit_boundary=captcha_detected`.
- Real pages fire analytics beacons (for example a Snowplow POST on Greenhouse). The read-only router aborts them and the report records each one.
- React forms fire several same-document navigations for one load. The checks allow history updates on the form's own origin and path and still require exactly one document load.
- 2 of 30 batch postings answered the form document with HTTP 403. They failed closed as `blocked_by_bot_check` and nothing retried them.

## Next milestones & future expansion

1. **Live Production Submissions Across Greenhouse, Lever, SmartRecruiters & Workday**: Expand live production submission verification beyond Ashby to the remaining four supported ATS adapters.
2. **Cloud CAPTCHA Solver Plugin (`2Captcha` / `CapSolver`)**: Implement production HTTP adapters for the `CaptchaSolver` protocol to solve interactive post-submit reCAPTCHA v2 / hCaptcha image challenges in headless mode without human intervention.
3. **Outbound Follow-up Email Dispatch**: Automatic sending of generated 7-day and 14-day polite check-in drafts via authenticated SMTP/OAuth after user approval.
4. **Session Continuity & Enterprise Portal Auth**: Persistent browser storage states and MFA handshake handling across multi-step corporate login portals (Workday, iCIMS, Taleo, SuccessFactors).
5. **Truth-Guarded LLM Fine-Tuning & Model Benchmarks**: Benchmark local quantized models (`llama3.2:3b`, `llama3.1:8b`, `qwen2.5:7b`) on latency and answer quality for complex situational application prompts.
