# HunterXJob

HunterXJob is a self-hosted job-hunting assistant. `v2/` is the runtime: it discovers, scores and prepares applications, dry-runs them against real forms, and stops at any unsafe boundary. `mobile/` is a phone remote control for v2. `backend/` is the legacy v1 engine, kept only as a donor.

Live submission is intentionally locked, and neither the API nor the app can unlock it. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP_STATUS.md`](docs/ROADMAP_STATUS.md).

## Repository layout

- `v2/`: the current FastAPI runtime (SQLite state machine, answer vault, form engine, adapter registry, continuous run, remote-control API) and its tests. `v2/openapi.json` is the committed API schema.
- `mobile/`: Expo Router remote control for v2 (dashboard, kill switch, scheduler, jobs, review queue, reports, settings), with a typed client generated from `v2/openapi.json`.
- `backend/`: **legacy v1, donor only.** Nothing depends on it. It is kept for reference and will be removed in a later milestone.
- `docs/` — architecture, reference-repo policy, and roadmap status.
- `scripts/test-all.sh` — one-command local validation for backend, v2, and mobile.

## Prerequisites

- Python 3.12 for `v2/`.
- Python 3.11 for the legacy `backend/`.
- Node.js/npm for the mobile app.
- Chromium via Playwright for v2 browser verification (see `v2/README.md`) and for the legacy v1 browser/PDF paths.

## Start v2

```bash
cd v2
./hunterx install    # venv, dependencies, .env with a random API_KEY
./hunterx doctor
./hunterx start
```

Open `http://127.0.0.1:8011`. Every API call except `/api/health` needs the `X-API-Key` header (the key is in `v2/.env`; `./hunterx install` generates it).

Before any résumé or cover letter is generated, load your profile and verify its facts (`./hunterx profile import data/profile.yaml`, then verify on the phone or with `./hunterx profile verify ...`). Generated materials are drafts until you approve them; only approved versions are ever attached to a dry-run. See [v2/README.md → Profile and application materials](v2/README.md#profile-and-application-materials).

To run it unattended, use the systemd service on a small Linux VM (primary) or Termux:Boot on the phone (fallback): see [docs/DEPLOY_VM.md](docs/DEPLOY_VM.md). Continuous discover → score → prepare → dry-run cycles are off by default; see `v2/README.md`.

## Legacy backend (v1, donor only)

You don't need this to run HunterXJob. It is kept only so its code can be mined and its tests stay green.

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Mobile app

```bash
cd mobile
npm ci
npm run start
```

On first launch the app opens **Connection**. Enter the v2 server URL (for example `http://<your-vm>.<tailnet>.ts.net:8011` over Tailscale) and the `API_KEY` from `v2/.env`, then run the connection test. The key is stored in the device's secure storage. See [`mobile/README.md`](mobile/README.md).

## Validation

```bash
./scripts/test-all.sh
```

The script runs backend syntax/tests, v2 syntax/tests (including the OpenAPI snapshot check), and the mobile typecheck, unit tests and API-type check, then prints a PASS/FAIL summary and exits non-zero if any step failed. It expects `backend/.venv` (Python 3.11), `v2/.venv` (Python 3.12+), and `mobile/node_modules` (`cd mobile && npm ci`) to exist.

CI mirrors this: `.github/workflows/v2-tests.yml` covers `v2/`, and `.github/workflows/validate.yml` covers `backend/` and `mobile/`.
