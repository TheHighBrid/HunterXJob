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

## Decision and truth layer

The v2 pipeline now has portable, deterministic components that can be used by every source and submission adapter:

- `app/decisioning.py` runs hard vetoes and an explainable six-dimension score before optional AI evaluation.
- `app/answer_vault.py` resolves form answers with provenance and confidence gates. Sensitive answers require explicit user or policy sources.
- `app/liveness.py` distinguishes live, expired, blocked, and ambiguous application pages.
- Pipeline events retain the deterministic score breakdown, matched keywords, vetoes, and review flags.

The source comparison and clean-room integration policy are documented in [`../docs/REFERENCE_REPO_SYNTHESIS.md`](../docs/REFERENCE_REPO_SYNTHESIS.md).

## Android quick start

```bash
proot-distro login ubuntu
git clone https://github.com/TheHighBrid/HunterXJob.git
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

This directory contains the local runtime, durable state machine, local AI abstraction, API-first Greenhouse/Lever discovery, explainable deterministic scoring, health checks, process supervision, and tests. Platform submission adapters remain isolated behind a stable interface so they can be expanded without destabilizing discovery, scoring, or document generation.
