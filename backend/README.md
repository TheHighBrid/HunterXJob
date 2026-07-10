# HunterXJob Backend

Self-hosted automated job-application engine. Python 3.11 / FastAPI / SQLAlchemy
(SQLite) / APScheduler / Playwright / Jinja2. See `/docs/ARCHITECTURE.md` (repo
root) for the full design spec — this backend implements it.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # adds pytest, respx for tests

# Chromium is needed for resume/cover-letter PDF rendering and the
# Playwright-based application adapters:
python -m playwright install chromium --with-deps
# If --with-deps fails (e.g. sandboxed/offline environment, apt repo
# issues), try without it — the browser binary itself will still download
# and often works if the host already has the needed shared libraries:
python -m playwright install chromium

cp .env.example .env
# edit .env: set API_KEY, and (optionally) LLM/SMTP/IMAP/job-source config
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

On startup this creates the SQLite DB (`DATABASE_PATH`, default
`./data/hunterxjob.db`) and tables, then starts the in-process APScheduler
with four recurring jobs: `job_search_cycle`, `automation_cycle`,
`email_check_cycle`, `report_generation_cycle` (intervals configurable via
`.env`).

Check it's alive (no API key needed for `/api/health`):

```bash
curl http://localhost:8000/api/health
```

Every other `/api/*` route requires an `X-API-Key` header matching
`API_KEY` from `.env`:

```bash
curl -H "X-API-Key: <your key>" http://localhost:8000/api/jobs
```

### LLM provider

Default is Ollama (free, local, no API key): install Ollama separately
(https://ollama.com), run `ollama pull llama3.2`, and make sure it's
listening at `OLLAMA_BASE_URL` (default `http://localhost:11434`). Content
generation (cover letters, application emails) will fail with a clear error
if Ollama isn't reachable — everything else in the API still works.

Set `LLM_PROVIDER=openai_compatible` plus `OPENAI_COMPATIBLE_*` in `.env` to
use OpenAI, Groq, or any OpenAI-chat-completions-compatible endpoint
instead.

### Docker

```bash
docker build -t hunterxjob-backend .
docker run -p 8000:8000 --env-file .env -v $(pwd)/data:/app/data hunterxjob-backend
```

(A `docker-compose.yml` adding an Ollama service is a natural next step per
the architecture doc's "Docker Compose for one-command self-hosting" goal —
not included in this pass; the Dockerfile above is the backend half.)

## Test

```bash
source .venv/bin/activate
pytest tests -v
```

PDF-rendering tests (`test_resume_render.py`) skip gracefully (not fail) if
Playwright's Chromium binary isn't installed/launchable in the current
environment — everything else runs with mocked HTTP (respx) and a temp
SQLite DB per test, no network or real LLM/SMTP/IMAP access required.

## What's implemented (v1 scope) vs. scaffolded (roadmap)

**Fully implemented, real, running code:**
- Data model: `Profile`, `ResumeVersion`, `JobPosting`, `Application`,
  `QAAnswer`, `AutomationRun`, `Report`, `SettingsKV` (SQLAlchemy models in
  `app/models.py`, matching architecture doc section 4)
- REST API with `X-API-Key` auth: jobs, applications, automation, reports,
  settings, resume upload, health, plus a small `profile` router (not in
  the doc's explicit list but needed to populate the `profile` table used
  by content generation — see deviation note below)
- Job discovery: Greenhouse public Job Board API, Lever public Postings
  API, and a generic JSON/RSS feed adapter (`app/services/job_sources/`)
- Job-fit scoring (`app/services/matching.py`): TF-IDF cosine similarity if
  scikit-learn happens to be installed, otherwise a dependency-free
  weighted keyword-overlap scorer — no network/model download required
  either way
- AI content generation (`app/services/content_generation.py`): cover
  letters + application emails via the pluggable LLM provider (Ollama or
  OpenAI-compatible), Jinja2-templated prompts with few-shot style guidance
- Resume/cover-letter rendering to PDF via Playwright + Jinja2 HTML
  templates (`app/templates/resume.html.j2`, `cover_letter.html.j2`)
- Q&A answer bank (`app/services/qa_bank.py`): difflib fuzzy matching of
  recurring application questions to stored answers, no extra deps
- Fully automated apply flow for **Greenhouse**, **Lever**, and **direct
  email** applications, plus a **generic Playwright form-filler** with
  per-field confidence scoring that aborts to `needs_review` rather than
  guessing on unrecognized required fields
- Safety rails (`app/services/safety.py`): daily submission cap,
  minimum inter-application delay, company blacklist — enforced in
  `automation_cycle` *before* any adapter is called
- `submitted` / `blocked` / `needs_review` / `failed` are tracked as
  genuinely distinct outcomes everywhere; no adapter ever reports
  "submitted" without an observed confirmation signal (page text/URL for
  browser adapters, a non-raising `smtplib` send for email)
- IMAP inbox polling (`app/services/email_monitor.py`) inferring
  interview/rejection/offer status from reply emails
- APScheduler background scheduler wiring all four periodic cycles
  together (`app/services/scheduler.py`)
- Automated pytest suite (36 tests): matching, QA bank, resume rendering
  (real PDFs), job source adapters (respx-mocked HTTP), safety rails, and
  the jobs/applications REST API via `TestClient`

**Scaffold only (roadmap, per architecture doc section 3), not runnable
by default:**
- `app/services/automation/linkedin_adapter.py`,
  `indeed_adapter.py`, `upwork_adapter.py` — class exists implementing the
  `ApplicationAdapter` interface, `enabled = False` by default, `submit()`
  raises `NotImplementedError` with a docstring pointing back to
  `docs/ARCHITECTURE.md`'s roadmap/ToS-risk section. They accept a
  Playwright `storageState` file path (never a password) and deliberately
  contain **no** stealth/anti-detection/CAPTCHA-bypass code — that's
  explicitly out of scope regardless of automation mode.
- Mobile dashboard app (Expo/React Native) — not part of this backend pass.
- Postgres/Celery/Redis scaling path, Docker Compose with Ollama bundled,
  cloud APK build — documented as roadmap in the architecture doc, not
  built here.

## Deviations from the spec, and why

- **Added `app/routers/profile.py`** (`GET/PUT /api/profile`). The
  documented API surface (architecture doc section 5) doesn't list it, but
  the `profile` table (section 4) has no other way to be populated, and
  `automation_cycle`/`content_generation` read from it. Treated as a small,
  consistent extension rather than a silent gap.
- **`GET /api/health` does not require `X-API-Key`**, unlike every other
  route. This is a deliberate, documented (in the route's own docstring)
  deviation so container health checks / uptime monitors don't need the
  secret — flagged here in case the strict "all endpoints" reading in
  section 5 was intentional.
- **`sklearn` is not a hard dependency** for job-fit scoring — kept out of
  `requirements.txt` entirely per the "no heavy ML deps" instruction; the
  pure-Python fallback is what actually runs unless you separately `pip
  install scikit-learn`.
- **Settings are layered**: `.env` provides defaults/seed values; `PUT
  /api/settings` writes runtime overrides into the `settings_kv` table,
  which take precedence when present. This lets the mobile app change caps
  live without restarting the process or editing `.env`.
- Greenhouse/Lever/generic Playwright **automation adapters use best-effort
  standard-pattern selectors** (documented in each adapter's docstring) —
  real Greenhouse/Lever job pages do vary in exact markup, so selectors may
  need adjustment for a given company's board; the adapters fail safe to
  `needs_review`/`failed` rather than mis-reporting `submitted` if a
  selector doesn't match.
