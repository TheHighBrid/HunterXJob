# HunterXJob Safety & Runtime Invariants

## 1. Safety & Feature Flag Contracts
- **Global Kill-Switch Disarm**: Any API request to disengage the global kill switch (`POST /api/kill-switch` with `{"engaged": false}`) MUST include `{"confirm": true}`. Failing to provide `confirm: true` returns HTTP 428 Precondition Required.
- **Live Submission Safety Lock**: Under no circumstances should an HTTP endpoint be created or modified to allow remote callers to turn ON live submissions. `allow_live_submission` and `unattended_mode` are protected by `API_ENABLE_FORBIDDEN` and must return HTTP 403 Forbidden if an API call attempts to enable them. Turning them OFF via the API is permitted.
- **Local Startup Configuration**: Live submissions are unlocked exclusively through host environment settings (`ALLOW_LIVE_SUBMISSION=true` and `APPLICATION_MODE=autonomous` in `v2/.env` or `./start_mac.command`). Server startup (`lifespan` in `v2/app/main.py`) must synchronize this configuration into the database.

## 2. Web Dashboard & Inline Template Safety
- **Raw String Templates**: Whenever defining HTML/JS templates inside Python strings (such as `v2/app/dashboard_template.py`), always use raw string literals (`r"""<!doctype html>..."""`) or explicit double backslashes (`\\n`). Standard triple-quoted strings collapse `\n` into unescaped raw newlines inside JavaScript string literals, causing fatal browser syntax errors (`SyntaxError: Invalid or unexpected token`).
- **Syntax Validation**: Before finishing edits to any inline script or template, validate JavaScript syntax using `node -c`.
- **Frontend Fallbacks**: Ensure client-side API headers (`authHeaders()`) include a fallback to the default local server API key if `localStorage` has not yet been populated.
- **Immediate Initialization**: Hook dashboard `init()` into `DOMContentLoaded` (or execute immediately if `document.readyState !== 'loading'`) rather than relying exclusively on `window.onload`.
