# HunterXJob v2

Android-first, zero-cost, local-first autonomous job hunting system.

## Design contract

- Runs inside Ubuntu `proot-distro` on non-root Android.
- Uses SQLite, FastAPI, Playwright and Ollama only.
- No paid API is required.
- Every pipeline stage is durable and resumable.
- Deterministic eligibility checks run before local AI.
- Submission defaults to `dry_run` and requires explicit configuration.
- One command controls install, start, stop, status and diagnostics.

## Pipeline

`discovered -> normalized -> eligible -> scored -> shortlisted -> materials_generated -> materials_reviewed -> ready_to_apply -> form_filled -> validated -> submitted -> confirmed`

Failures are recorded per stage. Retrying never discards completed work.

## Android quick start

```bash
proot-distro login ubuntu
git clone -b hunterxjob-v2 https://github.com/TheHighBrid/HunterXJob.git
cd HunterXJob/v2
./hunterx install
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

- `review`: generates and fills, then waits.
- `dry_run`: validates and captures artifacts, never submits.
- `autonomous`: submits only when all gates pass.

The default is `dry_run`.

## Current v2 foundation

This branch establishes the clean runtime, durable state machine, local AI abstraction, API-first Greenhouse/Lever discovery, deterministic scoring, health checks, process supervision and tests. Platform submission adapters are intentionally isolated behind a stable interface so they can be added without destabilizing discovery, scoring or document generation.
