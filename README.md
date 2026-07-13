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

1. `python -m compileall -q app tests` in `backend/`.
2. `pytest -q` in `backend/`, preferring `backend/.venv/bin/python` when it has pytest installed.
3. `npm run typecheck` in `mobile/`.

If backend dependencies are not installed, create the backend virtual environment and install `requirements.txt` plus `requirements-dev.txt` first.
