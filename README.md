# HunterXJob

**HunterXJob** is an autonomous, self-hosted end-to-end job hunting and application engine designed for precision, strict truthfulness, and zero-touch operation.

It discovers live job postings, evaluates match scores against your verified profile, dynamically tailors ATS-friendly résumés and truthful cover letters, launches an interactive headless browser (Playwright) to fill application forms and attach files, resolves CAPTCHAs, executes verified submissions with cryptographic proof, prevents duplicate submissions across ATS platforms, and manages post-submission follow-ups.

---

## Key Capabilities

* **Autonomous End-to-End Loop**: From discovery $\rightarrow$ fit scoring $\rightarrow$ material tailoring $\rightarrow$ interactive browser form filling $\rightarrow$ CAPTCHA resolution $\rightarrow$ live submit / verified dry-run $\rightarrow$ evidence hashing $\rightarrow$ follow-up cadence tracking.
* **Interactive Browser Automation**: Uses Playwright to interactively type inputs, match `<select>` dropdown options, toggle radios/checkboxes, attach generated résumé PDFs and cover letters (`set_input_files`), and verify DOM values with full-page screenshots and execution traces.
* **Candidate Knowledge Book**: Central factual vault loaded from your verified profile YAML. Deterministically answers complex ATS screening questions (Canadian work authorization, sponsorship, relocation, background check consent, prior employment, non-competes).
* **Strict Truth Guard (Zero Hallucination)**: All generated résumé bullets and cover letters are strictly audited against verified candidate facts before any application is prepared or submitted.
* **Dual-Mode CAPTCHA Bridge**: Detects Google reCAPTCHA (v2/v3), hCaptcha, and Cloudflare Turnstile, with automated solving token injection into the DOM and supervised fallback.
* **Cross-ATS Deduplication & Submit Guard**: Detects cross-posted or duplicate roles across Greenhouse, Lever, Ashby, SmartRecruiters, and Workday, permanently blocking duplicate submissions.
* **Cryptographic Evidence & Post-Submission Tracking**: Captures confirmation receipt text, candidate requisition IDs, pre/post-submit DOM hashes, full-page screenshots, and sets automated 7-day and 14-day check-in reminders.
* **Multi-Platform ATS Support**:
  - **Greenhouse** (`boards.greenhouse.io`, `job-boards.greenhouse.io`)
  - **Lever** (`jobs.lever.co`)
  - **Ashby** (`jobs.ashbyhq.com`)
  - **SmartRecruiters** (`jobs.smartrecruiters.com`, `careers.smartrecruiters.com`)
  - **Workday** (`*.myworkdayjobs.com`)

---

## Repository Layout

```
HunterXJob/
├── v2/                 # FastAPI core engine, Playwright runner, SQLite state machine, and tests
│   ├── app/            # Application modules (autonomous loop, browser filler, knowledge book, etc.)
│   ├── tests/          # Comprehensive 528-test verification suite
│   ├── scripts/        # Recovery Gate 1 & Gate 2 verification scripts
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
Ensure Python 3.12+ is installed (e.g. via `brew install python@3.12 git` on macOS).

```bash
cd v2
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
cp examples/profile.example.yaml my_profile.yaml
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
Open [http://127.0.0.1:8011](http://127.0.0.1:8011) in your browser. Copy the `API_KEY` from your `.env` file, paste it into the key box, and click **Save key**.

---

## Autonomous Operation Modes

HunterXJob provides the `./hunterx autonomous-run` CLI:

### Mode 1: Verified Browser Dry-Run (Default & Safe)
Fills real employer application forms in a headless browser, attaches your tailored résumé PDF and cover letter, verifies all DOM read-backs, takes screenshots, and records cryptographic SHA-256 evidence **without clicking final submit**:
```bash
./hunterx autonomous-run --limit 5 --min-score 70.0
```

### Mode 2: Visual Inspection Mode (`--headed`)
Watch the Playwright browser open Chromium, type answers, select dropdown options, and attach documents live on screen:
```bash
./hunterx autonomous-run --headed --limit 1
```

### Mode 3: Live Autonomous Submission
To execute actual live applications:
1. Enable `ALLOW_LIVE_SUBMISSION=true` and `APPLICATION_MODE=autonomous` in `v2/.env`.
2. Run with the `--live` flag:
   ```bash
   ./hunterx autonomous-run --live --limit 5 --min-score 75.0
   ```

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
