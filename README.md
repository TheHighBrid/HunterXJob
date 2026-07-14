# HunterXJob

HunterXJob is a self-hosted job-hunting assistant with a FastAPI backend and an Expo/React Native mobile dashboard. It discovers jobs, tracks applications, renders resumes/cover letters, and provides safe automation hooks for supported job boards.

## Repository layout

- `backend/` — FastAPI application, SQLite/SQLAlchemy data model, automation services, and pytest suite.
- `mobile/` — Expo Router mobile app for viewing jobs, applications, reports, and settings.
- `docs/` — architecture and implementation notes.
- `scripts/test-all.sh` — one-command local validation for backend syntax/tests and mobile type checking.

## Prerequisites

- Python 3.11 for the backend.
- Node.js/npm for the mobile app.
- Chromium via Playwright if you want to run browser/PDF rendering paths locally.

## Backend setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium
```

Run the API:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Mobile setup

```bash
cd mobile
npm install
npm run start
```

## Validation

Run all standard checks from the repository root:

```bash
./scripts/test-all.sh
```

The script runs:

1. Backend dependency preflight checks with actionable install hints.
2. `python -m compileall -q app tests` in `backend/`.
3. `pytest -q` in `backend/`, preferring `backend/.venv/bin/python` when it has pytest installed.
4. If present, `v2/` dependency preflight checks, syntax checks, and `pytest -q`.
5. `npm run typecheck` in `mobile/`, after verifying `node_modules` exists.

The script prefers Python 3.11 for `backend/` and Python 3.12 for `v2/`, including pyenv-installed interpreters when the usual `python3.11`/`python3.12` commands are not active. If dependencies are not installed, create the relevant virtual environment and install the dependency files first.
