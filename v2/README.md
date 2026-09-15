# HunterXJob v2

Android-first, zero-cost, local-first job hunting system with bounded autonomy.

## Design contract

- Runs inside Ubuntu `proot-distro` on non-root Android, or any local Python 3.12.
- Uses SQLite, FastAPI, and optional Ollama. No paid API is required.
- Every pipeline stage is durable and resumable.
- Deterministic eligibility runs before local AI.
- `REVIEW` jobs never reach scoring or application creation.
- Submission defaults to `dry_run`. Live submit stays locked until an adapter is `certified_autonomous`.

## Pipeline

`discovered -> eligible|review|rejected -> scored -> shortlisted -> approved -> materials_generated -> ready_to_apply -> applying -> form_filled -> validated -> needs_review|submission_uncertain|submitted -> confirmed`

## Safety layer

- Provenance-aware answer vault
- Form-control engine with confidence and handoff reasons
- Adapter registry with maturity levels
- Submission evidence ledger
- Scheduler caps, quiet hours, and kill switches
- Global and per-platform feature flags

## Quick start

```bash
cd HunterXJob/v2
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env
./hunterx doctor
./hunterx start
```

Open `http://127.0.0.1:8011`.

## Commands

```bash
./hunterx install
./hunterx start
./hunterx stop
./hunterx status
./hunterx doctor
./hunterx test
```

## Safety modes

- `review`: generate and fill, then wait.
- `dry_run`: validate and capture artifacts, never submit.
- `autonomous`: would submit only when every gate passes. No adapter is certified for that yet.

Default is `dry_run`. `ALLOW_LIVE_SUBMISSION` and `unattended_mode` stay false.

## Usable now

1. `PUT /api/resume-facts`
2. `POST /api/answers` for explicit policies. Work authorization must be `user` or `policy`.
3. Set Greenhouse board tokens / Lever slugs in `.env`.
4. `POST /api/discovery/run` then `POST /api/scoring/run`.
5. Generate materials, approve, then `POST /api/applications/{id}/apply` for a dry-run.
6. Watch `/api/review-tasks` instead of hoping the bot guessed.

## Tests

```bash
PYTHONPATH=. python -m pytest -q
```
