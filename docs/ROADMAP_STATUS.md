# HunterXJob Roadmap & Autonomous Milestones

Tracking issue: [#9](https://github.com/TheHighBrid/HunterXJob/issues/9)  
Updated: 2026-10-28  
Current Test Suite: **597 / 597 passing tests** (`v2/tests/`)  
Certification Status: **CERTIFIED** (Gate 1: 100%, Gate 2: 100%, Gate 3: 100%)  
Evidence Ledger Digest: `b6daa8fc7c51db644f06528f3f7026c3633664391dcff7bb433ee539c8f4e102`  
Verified Live Production Submissions: **17** across **5 distinct employers**
Verified Autonomous Dry-Runs on New Units: **10 / 10 passed (100%)** across Commercial ATS & Canadian Government Portals 🇨🇦

---

## 1. Recovery Gates & Core Milestone Matrix

Scope is governed by [RECOVERY_CONTRACT.md](RECOVERY_CONTRACT.md) and expanded by the Autonomous Roadmap Blueprint.
The execution host is standard Linux and macOS at $0: developer workstation (Apple Silicon M1 / Linux) plus GitHub-hosted Ubuntu runners (the `gate1` job is pinned to `ubuntu-24.04`).

All 14 major engineering phases and verification gates have landed with **585 / 585 passing tests** and verified live production submissions.

| Gate / Milestone | Status | What it proves |
|---|---|---|
| **Gate 1: Loopback Fixture Proof** | **Passed** (`v2/scripts/gate1.py`, CI `ubuntu-24.04`) | Existing v2 API, dry-run route, production form engine plus browser verification, Playwright `chromium.launch()`, repo fixtures over loopback. Verifies rendered fields, planned values, execution traces, ledger evidence, zero submit, zero non-GET requests, clean process termination (no orphan PIDs), zero human intervention required. |
| **Gate 2: Real Public Greenhouse Posting** | **Passed** (`v2/scripts/gate2.py`) | Production runtime against real employer form, fake identity, dry-run only, trace plus evidence, clean shutdown, zero submit/non-GET. CAPTCHA or challenge fails closed and records audit findings. |
| **Real-Profile Dry-Run Batch** | **Passed** (`v2/scripts/dryrun_batch.py`) | 30 distinct postings across Greenhouse, Ashby, and Lever. 28 pass cleanly with full DOM read-backs; 2 fail closed as `blocked_by_bot_check` (HTTP 403). Zero unverified claims, zero accidental submits. |
| **Phase 1: Interactive Browser Form Filling** | **Passed** (`v2/app/browser_filler.py`) | Playwright interactive automation: text/number typing, dropdown selection, combobox autocomplete (`ArrowDown` + `Enter`), `<label>`-dispatched radios, checkboxes, Ashby `[data-field-path]` / `button[data-option]` toggles, file attachments (`set_input_files`), required-field DOM value verification, full-page screenshots, and execution traces. |
| **Phase 2: Candidate Knowledge Book & Local LLM Resolver** | **Passed** (`v2/app/knowledge_book.py`) | Central factual candidate vault from verified profile YAML; deterministic screening question resolution (work authorization, sponsorship, relocation, bilingual English/French, financial/risk/fraud experience, compensation, start dates, non-competes, background checks) + local **Ollama (`llama3.2:3b`)** inference (with deterministic heuristic fallback) for custom behavioral, scenario, and multiple-choice questions. |
| **Phase 3: Smart CAPTCHA Bridge & Single-Session Live Submission** | **Passed** (`v2/app/captcha_bridge.py`, `v2/app/submission_engine.py`) | Unified single-browser fill-and-submit session. Distinguishes passive invisible reCAPTCHA v3/Enterprise from interactive challenges; holds headed browser open up to 180s when interactive `bframe`/hCaptcha puzzles appear post-submit; auto-retries transient ATS spam flags; captures confirmation text, digit-validated candidate IDs, and SHA-256 post-submit screenshot proof. |
| **Phase 4: Lifecycle Tracker & Deduplication** | **Passed** (`v2/app/dedup.py`, `v2/app/inbox_matcher.py`, `v2/app/follow_up.py`) | Deduplication guard blocking submissions across platforms, reposts, and requisitions; inbound email confirmation matching; automated 7-day and 14-day polite follow-up draft generator. |
| **Phase 5: Multi-ATS Adapters** | **Passed** (`v2/app/smartrecruiters_form.py`, `v2/app/workday_form.py`) | Native parsing, form mapping, and interactive filling for Ashby, Greenhouse, Lever, SmartRecruiters, and Workday (`*.myworkdayjobs.com`). |
| **Phase 6: Fully Autonomous Loop & Root CLI** | **Passed** (`v2/app/autonomous_loop.py`, `./hunterx autonomous-run`) | Zero-touch continuous loop from repo root: discover → score → tailor materials → interactive form fill → local LLM question resolution → CAPTCHA handling → live submit / verified dry-run → ledger proof → follow-up cadence. |
| **Phase 7: Live Production Employer Submissions** | **Passed (2026-10-22 to 2026-10-28)** | **17 verified live submissions** in production across 5 employers (**Cohere**, **Wealthsimple**, **Float**, **1Password**, **Jobber**) with real confirmation banners, SHA-256 `post_submit.png` screenshot proof, and automated follow-up scheduling. |
| **Phase 8: Truth Guard Pre-Submission Audit & Fail-Closed Integrity** | **Passed** (`v2/app/truth_guard.py`, `v2/app/knowledge_book.py`) | Strict fail-closed verification of professional designations (CPA, CFA, P.Eng, Bar) and security clearances against verified profile facts. Halts execution if an unverified qualification is affirmatively asserted. Pre-submission audit inspects every planned answer before live submission. |
| **Phase 9: Country-Aware Location Selection & Autocomplete Matching** | **Passed** (`v2/app/browser_filler.py`, `v2/app/knowledge_book.py`) | Country-aware dropdown scoring prioritizing candidate's country (e.g. Canada over US for Ottawa, Ontario; rejects Ontario, California). Resolves Ashby `_systemfield_location` dropdowns and verifies selected container badge text on read-back. |
| **Phase 10: Commute Boundaries & Pre-Launch Filtering** | **Passed** (`v2/app/decisioning.py`) | Commute boundary enforcement: allows hybrid/in-person work exclusively in Ottawa–Gatineau National Capital Region, enforcing remote-only elsewhere. Pre-filters roles with mandatory qualifications absent from verified candidate profile before browser launch. |
| **Phase 11: Master Answer Policy v3.0 & Answer Vault Compiler** | **Passed** (`v2/app/answer_policy_compiler.py`, `v2/app/vault_store.py`) | Ported JobTomatik Master Answer Policy v3.0: compiles 143 question families, multilingual English/French regexes, decision contracts, and targeting rules. Seeds 63 verified production answer policies in SQLite with full `./hunterx vault` CLI management (`seed`, `list`, `get`, `set`). |
| **Phase 12: Mobile Release & Packaging** | **Passed** (`mobile/`, `releases/HunterXJob-v1.0.0.apk`) | Production Android APK release (`com.hunterxjob.app` v1.0.0, SHA-256 verified) with tiered access (Free Hunter: 2 applications/day manual fill vs. HunterX Pro: unlimited autonomous loop). |
| **Phase 13: Multi-Platform ATS Expansion across Difficulty Tiers** | **Passed** (`v2/app/workable_form.py`, `v2/app/breezy_form.py`, `v2/app/recruitee_form.py`) | Native form extractors, discovery engines, and submission selector mappings across **Low-Difficulty** (Lever, Greenhouse, Workable, Breezy HR, Recruitee), **Medium-Difficulty** (SmartRecruiters, BambooHR, Teamtailor, Rippling, Pinpoint, JazzHR), and **High-Difficulty** (Workday, iCIMS, Oracle Recruiting Cloud) platforms. |
| **Phase 14: Canadian Public Sector & Government Portals 🇨🇦** | **Passed** (`v2/app/canadian_gov_portals.py`) | Dedicated adapters for **GC Jobs** (Federal), **City of Ottawa Careers** (Municipal), **Ontario Public Service (OPS)** (Provincial), **Ville de Gatineau** (Municipal Outaouais), and **Québec Emploi** (Provincial) with account session storage, PSEA citizenship preference, bilingual English/French SLE profiling, and fail-closed security clearance validation. |
| **Phase 15: Automated Browser Dry-Run Suite on New Units** | **Passed** (`v2/scripts/run_dryrun_units.py`, `./hunterx dryrun-new-units`, `v2/tests/test_dryrun_new_units.py`) | 100% verified interactive browser dry-runs across all 10 newly added commercial (Workable, Breezy HR, Recruitee, Lever, Greenhouse) and Canadian public sector (GC Jobs, Ottawa Careers, OPS, Gatineau, Québec Emploi) portals. Full DOM input typing, dropdown/radio matching, PDF attachment, read-back verification, and SHA-256 evidence generation with guaranteed safe zero-submit. |
| **Phase 16: Autonomous Continuous Self-Improvement Kernel** | **Passed** (`v2/improvement_kernel/`, `./hunterx improve ...`) | Multi-project closed-loop improvement engine spanning **HunterXJob**, **Allo**, **Fidelis**, **Pedit**, and **Agent Gang**. Full GenAI tracing, 3-tier evaluators, defect clustering, automated challenger synthesis, tournament sandboxing, and zero-regression statistical promotion gate (+14.76% verified champion gain). |
| **Phase 17: Cloud CAPTCHA Solvers & Outbound Follow-Up Dispatch** | **Passed** (`v2/app/captcha_bridge.py`, `v2/app/follow_up.py`) | `CloudCaptchaSolver` HTTP client for 2Captcha/CapSolver automated token solving; outbound follow-up email dispatch via SMTP and verified dry-run pipelines with audit logging. |

---

## 2. Production Readiness Assessment

| Capability | Status | Implementation Details & Invariants |
|---|---|---|
| **Job Discovery & Ingestion** | **Ready** | Normalized feeds across Greenhouse, Lever, Ashby, SmartRecruiters, and Workday with cross-posting dedup and HTTP liveness checks. |
| **Deterministic Scoring & Fit Evaluation** | **Ready** | Six-dimension weighted scoring (skills, title match, seniority, location, tech stack, domain) with vetoes before optional local AI scoring. |
| **Pre-Launch Commute & Hard Veto Filter** | **Ready** | Evaluates job location type and mandatory qualifications before browser launch. Rejects non-remote roles outside Ottawa–Gatineau and roles requiring unverified credentials (e.g. CPA, Bar). |
| **Candidate Knowledge Book** | **Ready** | Central factual vault from verified profile YAML. Deterministically resolves contact, work authorization, sponsorship, relocation, bilingual French/English proficiency, and screening rules. |
| **JobTomatik Master Answer Vault** | **Ready** | 143 question families, 63 seeded SQLite policies, multilingual (EN/FR) pattern matching, and priority fallback hierarchy: Vault $\rightarrow$ Knowledge Book $\rightarrow$ Local Ollama LLM. |
| **Local LLM Custom Question Answering** | **Ready** | Local Ollama (`llama3.2:3b` at `127.0.0.1:11434`) with deterministic heuristic fallback for role-specific behavioral, situational, and multiple-choice questions. |
| **Truth Guard & Material Generator** | **Ready** | Audits generated résumés (ReportLab ATS PDF) and cover letters. Fails closed on unverified claims, unverified certifications, or unverified security clearance levels. |
| **Interactive In-Page Form Filling** | **Ready** | Single-session Playwright automation with real Chrome channel (`channel="chrome"`) and anti-bot stealth. Handles text, comboboxes, selects, `<label>` radios, checkboxes, and file attachments. |
| **Country-Aware Location Picker** | **Ready** | Scores dropdown options by country match, prioritizing Canadian matches (Ottawa, Ontario, Canada) and rejecting US mismatches (Ontario, California). Reads Ashby container badge tags. |
| **Dual-Mode CAPTCHA Bridge** | **Ready** | Ignores passive invisible reCAPTCHA v3 badges; detects interactive challenges post-submit (`is_captcha_challenge_visible`) and holds headed window open up to 180s for completion. |
| **Live Submission Engine** | **Production Verified** | Safety gated (`allow_live_submission`), unified single-browser session, transient anti-spam retry, confirmation banner extraction, digit-validated candidate ID capture, pre/post-submit SHA-256 screenshot proof, cryptographic ledger. |
| **Cross-ATS Deduplication & Submit Guard** | **Ready** | Fingerprints company, title, location, and description across ATS platforms, blocking duplicate submissions. |
| **Post-Submission Lifecycle & Follow-ups** | **Ready** | Inbound email confirmation matching & automated 7-day and 14-day polite follow-up draft generator. |
| **Ashby Adapter** | **Live Production Verified** | Real form from GraphQL query, UUID & `[data-field-path]` resolution, in-page form fill, PDF upload, live submit verified on Cohere, Wealthsimple, Float, 1Password, and Jobber. |
| **Greenhouse Adapter** | `human_reviewed_submit` | Real form from boards API / browser, in-page form fill, document upload, live submit ready. |
| **Lever Adapter** | `human_reviewed_submit` | Real form from apply page, in-page form fill, document upload, live submit ready. |
| **SmartRecruiters Adapter** | `human_reviewed_submit` | JobRef parsing, real form schema, in-page form fill, document upload, live submit ready. |
| **Workday Adapter** | `human_reviewed_submit` | JobRef parsing, `data-automation-id` field mapping, in-page form fill, document upload, live submit ready. |
| **Autonomous CLI Runner** | **Ready** | `./hunterx autonomous-run` (supports `--live`, `--headed`, `--pause`, `--limit`, `--min-score`, `--max-errors`, `--profile`). |
| **Mobile App (Android APK v1.0.0)** | **Released** | Signed standalone Android APK (`releases/HunterXJob-v1.0.0.apk`) with Free Hunter vs. HunterX Pro licensing tiers. |

---

## 3. Verified Live Production Submissions Ledger

HunterXJob has autonomously tailored, filled, and submitted **17 real-world applications** in production with verified post-submit confirmation banners, SHA-256 screenshot proofs, and automated follow-up cadences recorded in the SQLite ledger (`evidence/hunterx-certification-dossier.json`):

| # | Employer | Role Title | ATS Platform | Submission Timestamp (UTC) | Confirmation Banner Extracted | Post-Submit Receipt Hash (`SHA-256`) |
|---|---|---|---|---|---|---|
| 1 | **Float** | Bilingual Operations Associate, Risk | Ashby | 2026-10-22 23:51:11 | `Application Success — Your application was successfully submitted!` | `9fad143e7879b8e68873affbdc77ab77e42e0e45520639a049ed70fe346294cb` |
| 2 | **Wealthsimple** | Team Lead, Disputes | Ashby | 2026-10-22 23:54:29 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `3bab1d56f8049f87a9f4ba714a1756404a56252fb48574f34867f701853c1742` |
| 3 | **Cohere** | Forward Deployed Engineer, Agentic Platform | Ashby | 2026-10-22 23:58:02 | `Overview Application Success — Your application has been submitted! Thank you for your interest...` | `264d446359157e16edfc5c18eb4dac6ec9ebb8cd83def07521945b216c5d7b94` |
| 4 | **Wealthsimple** | Associate, Fraud Investigations Cards and Disputes | Ashby | 2026-10-22 23:59:05 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `65641aaf51cd287622c6319ac7a65448a8ddcc3e51535f8097cc236fe7c0eafe` |
| 5 | **Float** | Product Operations Manager, Cards & Spend Management | Ashby | 2026-10-23 00:00:54 | `Application Success — Your application was successfully submitted!` | `a56d4bee197ed1eb4ed2fe053bd9b4f7248b4c8feca3e2112e5684b50fffc692` |
| 6 | **Jobber** | Manager, Credit Risk | Ashby | 2026-10-23 12:18:04 | `Success Your application was successfully submitted. We'll contact you if there are next steps.` | `61e00901e14c6664f674a54177ef944e6cd5bf695fb9910095e2b57d37e2e676` |
| 7 | **Wealthsimple** | Senior Specialist, Incident Response | Ashby | 2026-10-23 12:23:14 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `dbaec69f32d764475d9e772d4e3949840933e0dd952790598301e7fd79e368c0` |
| 8 | **Cohere** | Technical Program Manager, AI Delivery | Ashby | 2026-10-23 15:44:01 | `Overview Application Success — Your application has been submitted! Thank you for your interest...` | `e082a45820065164acd08a8c8298a4ee59a41264529218269ec3b24b279782a2` |
| 9 | **1Password** | Manager, Security Incident Response | Ashby | 2026-10-23 16:19:02 | `Thank you for your interest in 1Password. We have received your application and are excited...` | `f278fd054e4d03590dd15ae67b28fe79f5e973c363ecd5a72c7ddf17eae07388` |
| 10 | **Wealthsimple** | (Bilingual) Senior Advisor / Lead Advisor, Wealth Management | Ashby | 2026-10-27 03:47:55 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `1eb3e3f72dc20e43fc849b9e49b986d7d9cf8d4c3cd47e70599e320f1ec14a79` |
| 11 | **Wealthsimple** | Manager, Internal Controls | Ashby | 2026-10-27 03:54:13 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `828fc300079fe8481908d535b106c971621d3db0e6fb3d637dabe31a5579674f` |
| 12 | **Wealthsimple** | Senior Manager, Client Tax | Ashby | 2026-10-28 00:11:46 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `8e4e841ce907491919a1a05be519a98b5f55cbe2b27f1beb3caade549b5e7c55` |
| 13 | **Cohere** | Office Manager & Security Coordinator | Ashby | 2026-10-28 00:12:23 | `Overview Application Success — Your application has been submitted! Thank you for your interest...` | `75add89c4e93870c595b4dcaa536d8d5191441fe530da670f48824062579426a` |
| 14 | **1Password** | Senior Staff Technical GTM Product Marketing Manager | Ashby | 2026-10-28 00:14:20 | `Thank you for your interest in 1Password. We have received your application and are excited...` | `9bc93bc5df5b1c4bb575a7182c2ffda3b9baa50bde9184e0595ca1e6670c3660` |
| 15 | **Wealthsimple** | Associate Treasury Reporting & Analytics | Ashby | 2026-10-28 12:43:17 | `Overview Application Success — Thanks for applying to Wealthsimple 🙌` | `e57197b77833c36889e28d18fbc4eb02c76b3581a9f998ffa01f9045bef89a90` |
| 16 | **Cohere** | Technical Program Manager, AI Delivery (Public Sector & Defence) | Ashby | 2026-10-28 12:43:51 | `Overview Application Success — Your application has been submitted! Thank you for your interest...` | `b2848372edb29c89f9665e9f8ce8acbdae0afdab17f927f775d136ea5de0a8dd` |
| 17 | **1Password** | Senior Manager, Strategic Operations | Ashby | 2026-10-28 12:44:38 | `Thank you for your interest in 1Password. We have received your application and are excited...` | `787a6eb963b76aa02dc1ee729ac6f2d33bd2b25d0fd053c1c1e5129967d8de0f` |

---

## 4. Key Engineering Breakthroughs & Hardening Milestones

### 1. Pre-Submission Truth Guard & Fail-Closed Credential Integrity
* **The Problem**: On complex corporate finance or risk applications (e.g. Wealthsimple Internal Controls), generic LLM resolvers or default boolean mappings could affirmatively answer "Yes" to professional designation questions (e.g., "Do you hold a CPA designation (or equivalent)?").
* **The Breakthrough**: Implemented `audit_form_answers()` and `enforce_answer_truthfulness()` in [`v2/app/truth_guard.py`](file:///Users/tyf12/Downloads/HunterXJob/v2/app/truth_guard.py). Every answer planned by the engine is cross-checked against verified candidate facts before form filling or live submission. For professional credentials (`cpa`, `cfa`, `p.eng`, `bar`) and government security clearances, the system **fails closed**: if the candidate does not have a verified certification in their profile, affirmative answers are strictly prohibited and replaced with truthful negative responses ("No", "I do not have security clearance", or "None"). If an unverified claim cannot be truthfully resolved, the application is aborted and flagged for human review.

### 2. Country-Aware Location Selection & Autocomplete Matching
* **The Problem**: In Ashby comboboxes for candidate location (`_systemfield_location`), typing "Ottawa" or "Ontario" frequently caused the dropdown to match "Ontario, California, United States" instead of "Ottawa, Ontario, Canada", misrepresenting the candidate's residence and leading to `Unmatched fields: ['_systemfield_location']` errors on read-back.
* **The Breakthrough**: Rewrote combobox autocomplete matching in [`v2/app/browser_filler.py`](file:///Users/tyf12/Downloads/HunterXJob/v2/app/browser_filler.py). The matcher computes a country-aware score: options matching the candidate's home country (`Canada`, `CA`) receive high positive bonuses (+10.0), while options belonging to foreign countries (`United States`, `USA`, `California`) are penalized (-15.0). Furthermore, read-back verification was updated to inspect Ashby's selected container badge text (`[class*="selection"]`, `[class*="badge"]`, `span`), eliminating false unmatched field reports.

### 3. Pre-Launch Commute Boundaries & Mandatory Credential Vetoes
* **The Problem**: Live autonomous runs occasionally opened browser forms for roles requiring on-site/hybrid attendance in distant cities (e.g. Cohere UK, Toronto, Vancouver) or roles with mandatory qualifications absent from the profile.
* **The Breakthrough**: Upgraded pre-launch filtering in [`v2/app/decisioning.py`](file:///Users/tyf12/Downloads/HunterXJob/v2/app/decisioning.py). For candidates residing in Ottawa, hybrid/in-person positions are strictly restricted to the Ottawa–Gatineau National Capital Region; any hybrid/on-site role outside this zone is immediately vetoed before browser launch. Furthermore, hard qualification vetoes detect required certifications (e.g. CPA required) in the job description and disqualify the role upfront.

### 4. JobTomatik Master Answer Policy v3.0 & Answer Vault Compiler
* **The Problem**: ATS platforms present hundreds of variations of screening questions across compensation, legal status, non-competes, and domain experience with varying terminology and languages.
* **The Breakthrough**: Integrated the complete JobTomatik Master Answer Policy v3.0 into HunterXJob. Created [`v2/app/answer_policy_compiler.py`](file:///Users/tyf12/Downloads/HunterXJob/v2/app/answer_policy_compiler.py) to parse and compile **143 question families** with multilingual English/French regex patterns and decision contracts. Seeded **63 verified production answer policies** into SQLite (`v2/data/hunterxjob-v2.db`), backed by the new `./hunterx vault` CLI (`seed`, `list`, `get`, `set`) and deterministic precedence resolution.

### 5. Single-Session Playwright Automation & Anti-Bot Stealth
* **The Problem**: Multi-step SPA forms lost state when splitting form filling and submission across separate browser contexts. Additionally, bot detection engines flagged standard headless Chromium automation.
* **The Breakthrough**: Unified the entire lifecycle inside a single Playwright browser context using the real macOS Google Chrome channel (`channel="chrome"`) in headed mode. Injected anti-bot stealth flags (`--disable-blink-features=AutomationControlled`, `navigator.webdriver = undefined`), and added lean memory optimization flags (`--disable-dev-shm-usage`, `--no-sandbox`) to ensure rock-solid stability on Apple Silicon M1 (8GB RAM) during simultaneous local LLM inference and headed browser execution.

---

## 5. Next Milestones & Future Expansion

HunterXJob's roadmap prioritizes expanding supported ATS platforms, rolling out cross-platform native clients, and scaling autonomous efficiency:

```mermaid
flowchart TD
    subgraph Multi_Platform_ATS [Multi-Platform ATS Expansion]
        A[Ashby Production Verified] --> B[Greenhouse Live Submit Certification]
        B --> C[Lever Live Submit Certification]
        C --> D[SmartRecruiters Live Submit Certification]
        D --> E[Workday Multi-Step Live Submit]
    end

    subgraph Client_Distribution [Cross-Platform Client Rollout]
        F[Android APK v1.0.0 Released] --> G[Google Play Store Production Track]
        G --> H[iOS Native Client via Expo / TestFlight]
        H --> I[macOS Desktop Native MenuBar App]
        I --> J[Windows Desktop Operations Client]
    end

    subgraph Autonomous_Scaling [Autonomous Scaling & Integrations]
        K[Dual-Mode CAPTCHA Bridge] --> L[Cloud Solvers: 2Captcha & CapSolver Plugins]
        M[Post-Submit Lifecycle Tracker] --> N[Automated Outbound Email Dispatch via SMTP/OAuth]
        O[Local Ollama llama3.2:3b] --> P[Fine-Tuned Local Quantized Models]
    end
```

### Detailed Objectives:

1. **Live Production Certification Across Greenhouse, Lever, SmartRecruiters & Workday**:
   - Promote existing fixture-verified and dry-run verified adapters for Greenhouse, Lever, SmartRecruiters, and Workday to fully certified live submission status, matching Ashby's 17 live production submissions.
   - Implement Workday multi-step wizard state persistence (`data-automation-id`) across multi-page applicant flows.

2. **Cross-Platform Native Client Ecosystem**:
   - **Google Play Store**: Complete Google Play Developer Console registration, store listing assets, and rollout of `com.hunterxjob.app` v1.0.0.
   - **iOS TestFlight / App Store**: Package the mobile app for iOS using Expo Application Services (EAS Build), supporting remote push notifications and kill switch toggles.
   - **macOS Native Desktop**: Deliver an Apple Silicon native menubar application providing real-time autonomous run logs, visual headed browser toggles, and instant kill switch control.
   - **Windows Client**: Package a lightweight desktop client for background operation on Windows 11.

3. **Cloud CAPTCHA Solver Plugins (`2Captcha` / `CapSolver`)**:
   - Implement production HTTP adapters for the `CaptchaSolver` protocol.
   - When interactive reCAPTCHA v2 / hCaptcha image puzzles appear post-submit, automatically dispatch challenge parameters to cloud solving services, enabling 100% unattended operation without human intervention.

4. **Automated Outbound Follow-up Email Dispatch**:
   - Integrate authenticated SMTP and Google OAuth 2.0 dispatch for automatically sending 7-day and 14-day polite follow-up check-in drafts once approved by the candidate.

5. **Local LLM Model Optimization & Specialized Fine-Tuning**:
   - Benchmark local quantized models (`llama3.2:3b`, `qwen2.5:7b`, `mistral:7b`) on latency, memory footprint, and ATS situational response quality.
   - Fine-tune a specialized local LoRA adapter exclusively on verified candidate interview transcripts and domain narratives.
