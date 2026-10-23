# HunterXJob

**HunterXJob** is an autonomous, self-hosted end-to-end job hunting and application engine designed for precision, strict truthfulness, and zero-touch operation.

It discovers live job postings, evaluates match scores against your verified profile, dynamically tailors ATS-friendly résumés and truthful cover letters, launches an interactive headless browser (Playwright) to fill application forms and attach files, resolves CAPTCHAs, executes verified submissions with cryptographic proof, prevents duplicate submissions across ATS platforms, and manages post-submission follow-ups.

---

## Key Capabilities

* **Autonomous End-to-End Loop**: From discovery $\rightarrow$ fit scoring $\rightarrow$ material tailoring $\rightarrow$ interactive browser form filling $\rightarrow$ local LLM custom question answering $\rightarrow$ CAPTCHA resolution $\rightarrow$ live submit / verified dry-run $\rightarrow$ evidence hashing $\rightarrow$ follow-up cadence tracking.
* **Unified Single-Browser Playwright Automation**: Uses a single browser session (with real Google Chrome channel support in headed mode and anti-bot stealth hardening) to interactively type inputs, navigate combobox/autocomplete dropdowns (`ArrowDown` + `Enter`), click React/Ashby `<label>`-bound radio groups and `button[data-option]` Yes/No toggles, attach generated résumé PDFs and cover letters (`set_input_files`), and verify DOM read-backs with pre- and post-submit full-page screenshots and execution traces.
* **Candidate Knowledge Book + Local Ollama LLM (`llama3.2:3b`)**: Central factual vault loaded from your verified profile YAML. Deterministically resolves standard ATS screening questions (Canadian/US work authorization, sponsorship, relocation, bilingual English/French proficiency, domain/financial experience, salary expectations, notice period, background check consent) and wires into local **Ollama (`llama3.2:3b`)** (with deterministic heuristic fallbacks) to answer custom behavioral, scenario, and role-specific multiple-choice or open-ended questions.
* **Strict Truth Guard (Zero Hallucination)**: All generated résumé bullets and cover letters are strictly audited against verified candidate facts before any application is prepared or submitted.
* **Smart CAPTCHA Bridge & Post-Submit Challenge Hold**: Detects Google reCAPTCHA (v2/v3/Enterprise), hCaptcha, and Cloudflare Turnstile. Automatically ignores passive invisible reCAPTCHA v3 badges so forms submit cleanly, supports automated token injection, and—when an interactive post-submit CAPTCHA puzzle (`bframe` / hCaptcha) appears—automatically holds the headed browser window open for up to 180 seconds for completion before capturing confirmation proof. Includes automatic backoff and retry for transient ATS anti-spam flags.
* **Cross-ATS Deduplication & Submit Guard**: Detects cross-posted or duplicate roles across Greenhouse, Lever, Ashby, SmartRecruiters, and Workday, permanently blocking duplicate submissions.
* **Cryptographic Evidence & Post-Submission Tracking**: Captures confirmation receipt banners, digit-validated candidate reference IDs, pre/post-submit SHA-256 screenshot and DOM hashes, and schedules automated 7-day and 14-day check-in reminders.
* **Multi-Platform ATS Support**:
  - **Greenhouse** (`boards.greenhouse.io`, `job-boards.greenhouse.io`)
  - **Lever** (`jobs.lever.co`)
  - **Ashby** (`jobs.ashbyhq.com`) — *verified live autonomous submissions in production*
  - **SmartRecruiters** (`jobs.smartrecruiters.com`, `careers.smartrecruiters.com`)
  - **Workday** (`*.myworkdayjobs.com`)

---

## Current State & Achievements (October 2026)

* **Phases 1–6 & Recovery Gates 1–2 Complete**: Full autonomous pipeline verified across **528 passing automated tests** (`v2/tests/`).
* **Live Production Submissions Verified**: Executed end-to-end live autonomous submissions against real employer boards (including **Cohere**, **Wealthsimple**, and **Float** on Ashby), capturing live confirmation banners, SHA-256 post-submit full-page screenshot proofs, and automated Day 7 / Day 14 follow-up schedules in the SQLite ledger.
* **Zero-Touch Dynamic Form Resolution**: Full support for UUID-keyed ATS fields, `[data-field-path]` containers, custom scenario radio/select options, and narrative textareas powered by local `llama3.2:3b` + Knowledge Book rules.

---

## Repository Layout

```
HunterXJob/
├── hunterx             # Root CLI wrapper (forwards directly to v2/hunterx)
├── v2/                 # FastAPI core engine, Playwright runner, SQLite state machine, and tests
│   ├── app/            # Application modules (autonomous loop, browser filler, knowledge book, etc.)
│   ├── tests/          # Comprehensive 528-test verification suite
│   ├── scripts/        # Recovery Gate 1, Gate 2, and batch verification scripts
│   ├── hunterx         # Primary CLI utility for running, testing, and managing the daemon
│   └── openapi.json    # Committed OpenAPI 3.1 specification
├── mobile/             # Expo / React Native remote control app with typed client
├── backend/            # Legacy v1 engine (donor/reference only)
├── docs/               # Architecture, recovery contracts, and roadmap status
└── scripts/            # Repository-wide test runners
```

---

## Quick Start (macOS / Linux)

### 1. Installation
Ensure Python 3.12+ is installed (e.g. via `brew install python@3.12 git` on macOS). You can run `./hunterx` from the repository root or from `v2/`:

```bash
./hunterx install
```

### 2. Run Health Verification
Verify your environment, Python virtualenv, database, and Playwright Chromium installation:
```bash
./hunterx doctor
```

### 3. Load Your Candidate Profile
Copy the example profile and personalize your details:
```bash
cp v2/examples/profile.example.yaml my_profile.yaml
# Edit my_profile.yaml with your contact, employment history, education, and skills
./hunterx profile import my_profile.yaml
./hunterx profile list
```

### 4. Configure Job Boards (`.env`)
Edit `v2/.env` to configure target locations, keywords, and job board slugs:
```dotenv
TARGET_LOCATIONS=Ottawa,Remote Canada,Canada,Toronto
TARGET_KEYWORDS=software,developer,python,backend,engineer

# Public job board tokens
GREENHOUSE_BOARD_TOKENS=d2l,geotab,faire,later,hootsuite
LEVER_COMPANIES=pointclickcare,waveapps,achievers
ASHBY_ORGS=cohere,wealthsimple,1password,jobber,float
```

### 5. Start the Web UI & API
```bash
./hunterx start
```
Open [http://127.0.0.1:8011](http://127.0.0.1:8011) in your browser. Copy the `API_KEY` from your `v2/.env` file, paste it into the key box, and click **Save key**.

---

## Autonomous Operation Modes

HunterXJob provides the `./hunterx autonomous-run` CLI (runnable from the repo root or `v2/`):

### Mode 1: Verified Browser Dry-Run (Default & Safe)
Fills real employer application forms in a headless browser, attaches your tailored résumé PDF and cover letter, verifies all required DOM read-backs, takes screenshots, and records cryptographic SHA-256 evidence **without clicking final submit**:
```bash
./hunterx autonomous-run --limit 5 --min-score 70.0
```

### Mode 2: Visual Inspection Mode (`--headed`)
Watch the Playwright browser open Google Chrome / Chromium, type answers, select dropdown options, click radio labels, and attach documents live on screen (with configurable `--pause` seconds before closing):
```bash
./hunterx autonomous-run --headed --pause 5 --limit 1
```

### Mode 3: Live Autonomous Submission (`--live`)
Executes end-to-end live applications in a unified browser session, unlocking the live submission gate, solving or holding for interactive CAPTCHAs, verifying the post-submit confirmation banner, saving `post_submit.png` SHA-256 proof, and scheduling 7-day / 14-day follow-up cadences:
```bash
./hunterx autonomous-run --live --headed --limit 5 --min-score 75.0 --max-errors 3
```

---

## Roadmap

See [docs/ROADMAP_STATUS.md](docs/ROADMAP_STATUS.md) for the complete milestone matrix and readiness breakdown:

* **Completed**:
  - Recovery Gate 1 (loopback fixture proof) & Gate 2 (real public employer form dry-run)
  - Phase 1: Interactive Playwright Browser Form Filling & DOM Verification
  - Phase 2: Candidate Knowledge Book & Deterministic Screening Resolver
  - Phase 3: Dual-Mode CAPTCHA Bridge & Single-Session Live Submission Engine
  - Phase 4: Cross-ATS Deduplication Guard, Inbox Matcher & Follow-Up Cadence Scheduler
  - Phase 5: Multi-ATS Native Adapters (Greenhouse, Lever, Ashby, SmartRecruiters, Workday)
  - Phase 6: Zero-Touch Autonomous Loop, Root CLI, Local Ollama LLM (`llama3.2:3b`) Custom Question Answering, Anti-Bot Stealth & Live Production Submissions
* **Next Milestones**:
  1. **Live Production Certification Across Remaining ATS Adapters**: Expand live submission verification to Greenhouse, Lever, SmartRecruiters, and Workday postings alongside Ashby.
  2. **Automated 2Captcha / CapSolver API Integration**: Plug cloud solver providers into the `CaptchaSolver` protocol for fully unattended resolution of interactive reCAPTCHA v2/hCaptcha image challenges.
  3. **Outbound Follow-Up Email Dispatch**: Authenticated SMTP/OAuth dispatch of approved 7-day and 14-day check-in drafts.
  4. **Session Continuity & Enterprise Portal Auth**: Persistent browser storage states for multi-step corporate login portals (Workday / iCIMS / Taleo).

---

## Safety & Governance Controls

* **Global Kill Switch**: Instantly freezes all running browsers, schedulers, and submissions:
  ```bash
  python -c "from app.db import SessionLocal; from app.flags import set_flag; db=SessionLocal(); set_flag(db, 'global_kill_switch', True); print('Kill switch engaged')"
  ```
* **Daily Submission Caps**: Enforces `MAX_APPLICATIONS_PER_DAY=10` in `.env` to prevent flood applications.
* **Quiet Hours**: Suspends automatic cycles during configured off-hours (default: 23:00 to 07:00 local time).
* **Truth Guard Validation**: Compares every line of generated text against candidate facts. Any unverified claim triggers a review task and halts submission.

---

## Testing & Verification

Run the full 528-test test suite:
```bash
cd v2
./hunterx test
```

Run Recovery Gate 1 (deterministic loopback proof with process origin check):
```bash
.venv/bin/python scripts/gate1.py --runs 3
```

Run Recovery Gate 2 rehearsal:
```bash
.venv/bin/python scripts/gate2.py --rehearse
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
