# HunterXJob

HunterXJob is a self-hosted job-hunting assistant. `backend/` is the original engine. `v2/` is the current local-first runtime: discover, score, prepare, dry-run apply, and stop at any unsafe boundary.

Live unattended submission is intentionally locked. See [`docs/ROADMAP_STATUS.md`](docs/ROADMAP_STATUS.md).

## Repository layout

- `v2/` — current FastAPI runtime, SQLite state machine, answer vault, form engine, adapter registry, and tests.
- `backend/` — v1 FastAPI application, Playwright adapters, and pytest suite.
- `mobile/` — Expo Router dashboard for jobs, applications, reports, and settings.
- `docs/` — architecture, reference-repo policy, and roadmap status.
- `scripts/test-all.sh` — one-command local validation for backend, v2, and mobile.

## Prerequisites

- Python 3.12 for `v2/`.
- Python 3.11 for the legacy `backend/`.
- Node.js/npm for the mobile app.
- Chromium via Playwright only if you run v1 browser/PDF paths.

## Start v2

```bash
cd v2
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env
./hunterx doctor
./hunterx start
```

Open `http://127.0.0.1:8011`.

## Legacy backend

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Mobile setup

```bash
cd mobile
npm install
npm run start
```

## Validation

```bash
./scripts/test-all.sh
```

The script runs backend syntax/tests, v2 syntax/tests, and the mobile typecheck, then prints a PASS/FAIL summary and exits non-zero if any step failed. It expects `backend/.venv` (Python 3.11), `v2/.venv` (Python 3.12+), and `mobile/node_modules` (`cd mobile && npm ci`) to exist.

CI mirrors this: `.github/workflows/v2-tests.yml` covers `v2/`, and `.github/workflows/validate.yml` covers `backend/` and `mobile/`.
