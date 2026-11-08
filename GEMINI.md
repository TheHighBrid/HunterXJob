# HunterXJob Workspace Guidelines & Rules

## Core System Architecture & Protected Boundaries
HunterXJob is an autonomous job search and application platform operating under strict local-first, safety-first boundaries:
- **Server Root**: `v2/` (FastAPI backend engine, SQLite persistence at `v2/data/hunterxjob-v2.db`).
- **Mobile Remote**: `mobile/` (Expo / React Native TypeScript client targeting Android).
- **Extension**: `extension/` (Chrome extension for browser-assisted autofill).

## Inviolable Safety Invariants
1. **Kill-Switch Disarm Protocol**: Disengaging the global kill-switch (`POST /api/kill-switch` with `{"engaged": false}`) requires `{"confirm": true}`. Without `confirm: true`, the API returns `428 Precondition Required`.
2. **Live Submission API Lock**: Remote HTTP routes cannot enable live submissions (`API_ENABLE_FORBIDDEN` returns `403 Forbidden`). Live submissions can only be unlocked locally via host environment configuration (`ALLOW_LIVE_SUBMISSION=true` and `APPLICATION_MODE=autonomous` in `v2/.env` or `./start_mac.command`). Turning live submissions OFF via the API is always allowed.
3. **No Unreviewed Mutating Routes**: Any additions to HTTP routes must be reflected in `MUTATING_ROUTES` in `v2/tests/test_remote_api.py` and pass `test_no_route_can_unlock_live_submission_or_record_a_submission`.
4. **HTML/JS Template String Escaping**: Python modules providing inline HTML/JS templates (e.g. `v2/app/dashboard_template.py`) must use raw string literals (`r"""<!doctype html>..."""`) to prevent Python from converting `\n` into unescaped raw newlines in JavaScript string literals. Always syntax-test inline scripts with `node -c`.

For detailed reference, see [`.agents/rules/hunterx-safety-contracts.md`](file:///.agents/rules/hunterx-safety-contracts.md).
