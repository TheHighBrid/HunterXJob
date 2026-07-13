# HunterXJob v2

HunterXJob v2 is a zero-cost, local-first, Android-compatible autonomous job hunting system.

## Hard constraints

- Android 16, aarch64, non-root
- Ubuntu through `proot-distro`
- No paid AI APIs or hosted dependencies
- Local AI through Ollama or llama.cpp-compatible HTTP APIs
- SQLite persistence
- FastAPI backend
- PWA dashboard
- Playwright only for application flows that require a browser
- Explicit dry-run, review, and autonomous modes

## Pipeline

`discovered -> normalized -> eligibility_checked -> scored -> shortlisted -> materials_generated -> materials_reviewed -> ready_to_apply -> form_filled -> validated -> submitted -> confirmed`

Every stage is persisted. A failed stage can be retried without repeating earlier work.

## Architecture

- `app/api`: FastAPI routes
- `app/core`: configuration, logging, health, lifecycle
- `app/db`: SQLite models and repositories
- `app/discovery`: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, RSS and generic feeds
- `app/scoring`: deterministic eligibility and local-AI scoring
- `app/materials`: resume, cover letter and answer generation with drafter/reviewer/reviser passes
- `app/browser`: platform adapters and deterministic form execution
- `app/workflows`: durable pipeline orchestration
- `app/workers`: scheduler and browser workers
- `scripts`: install, start, stop and doctor commands
- `tests`: unit, integration and Android/proot smoke tests

## Operating modes

- `review`: generate and fill, then wait before submission
- `dry_run`: validate and capture evidence, never submit
- `autonomous`: submit only when every guardrail passes

## Zero-cost stack

- Python 3.12
- FastAPI
- SQLite
- Ollama or llama.cpp
- Playwright + Chromium
- React PWA
- APScheduler
- IMAP for status monitoring

## Initial milestone

The first milestone proves the full spine:

1. one-command startup
2. health and dependency checks
3. durable SQLite pipeline
4. local model connectivity
5. Greenhouse and Lever discovery
6. deterministic eligibility scoring
7. local material generation
8. dry-run application execution
9. confirmation logging

The legacy implementation remains untouched while v2 is built and tested on the `v2` branch.
