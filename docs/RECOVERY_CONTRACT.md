# Recovery contract

Owner: Mohamed Alem. Decided 2026-10-04. This decision is locked.

This contract is modeled on what the sister project learned. It replaces the
Android-hosted certification plan from PRs #21 to #25. Until this contract is
changed, it takes precedence over every other plan in this repository.

## Core decision

- Freeze Android execution. No new Termux, PRoot, ADB, native-Chrome, or CDP
  work.
- The code from PRs #21 to #25 stays as legacy. Nobody extends it and nobody
  deletes it.
- The Android app is a client only. It talks to the v2 API and runs nothing.

## Execution host

- Plain Linux at $0: the developer box plus GitHub Actions `ubuntu-latest`
  runners.
- No cloud VM, no paid service, no card.

## Keep the existing product

Keep the v2 FastAPI app, the SQLite state machine, the answer vault,
profile/materials, adapters, the form engine, the evidence ledger, the safety
gates, the kill switch, and the mobile app. No rewrites.

## Gate 1: prove the existing pipeline on plain Linux

Path under test:

```text
existing v2 API
  -> existing dry-run/apply path (POST /api/applications/{id}/apply)
  -> current production form engine + browser verification
  -> Playwright chromium.launch()  (Playwright-owned; never connect_over_cdp,
                                    never an externally launched browser)
  -> EXISTING local fixtures already in the repo, served over loopback HTTP
```

Gate 1 succeeds only when all of the following hold, verified automatically
across at least 3 independent runs:

1. Chromium was launched by Playwright.
2. The fixture loads.
3. Fields are extracted, and the filled values are read back and match what
   was expected.
4. A valid Playwright trace zip is written, integrity-checked, and hashed.
5. Evidence is persisted in the ledger.
6. Zero submit actions and zero non-GET requests, shown by network evidence.
7. After shutdown, the browser context, the browser process, and any child
   PIDs are gone.
8. The API request/task completes cleanly.
9. It reproduces with no human.

The gate report must explicitly record anything that was NOT proven (for
example `real_employer_site = false`).

What "filled values" means here: the production form engine plans values
from the vault and checks the rendered page read-only. It never types into the
page. Gate 1 therefore reads the planned values back from the ledger and
compares them with a hand-written expected plan. In-page typing is recorded as
not proven.

### How Gate 1 is run

`v2/scripts/gate1.py` runs it. CI runs it in the `gate1` job of
`.github/workflows/v2-tests.yml`.

```bash
cd v2
pip install -e '.[test,browser]' && python -m playwright install --with-deps chromium
python scripts/gate1.py --runs 3      # writes v2/gate-artifacts/gate1-report.json
python -m pytest -m gate1             # negative proofs: POST, auto-submit, process leak
```

Each run is a fresh process with an empty SQLite database. Each run does the
following:

- Serves `tests/fixtures/greenhouse/d2l_7696196.json` (as the boards-API
  payload) and `tests/fixtures/sample_form.html` (as the embed form) on
  `127.0.0.1`.
- Starts the real app under uvicorn on `127.0.0.1`.
- Seeds fake answers through `POST /api/answers`.
- Calls the apply route with `GREENHOUSE_BROWSER_VERIFY=true` and
  `BROWSER_TRACE_DIR` set.

The Greenhouse fetcher is pointed at the loopback server through
`app/fixture_origin.py`. That allowance cannot come from configuration:

- It is not a setting, so `.env`, environment variables, and the phone API
  cannot reach it.
- It only accepts `http://<loopback IP>:<port>`.
- It needs an explicit acknowledgement string.
- It refuses outright unless the mode is `dry_run` with live submission off.
- Tests cover every one of these rules.

The report lists every browser request (method and URL) and the fixture
server's own request log. It also lists the trace SHA-256 values, the browser
PIDs that were seen, and the PIDs left after shutdown. Any non-GET attempt,
submit event, extra navigation, off-origin request, or unexpected server
request fails the run.

## Gate 2: one real public Greenhouse posting

Gate 2 starts only after Gate 1 is green in CI.

- One real public Greenhouse posting.
- The same runtime.
- A fake identity.
- Dry-run only.
- Trace, evidence, and a clean shutdown.
- Zero submit and zero non-GET requests.
- A bot challenge or CAPTCHA means fail closed. Record it as a finding.

Nothing else enters scope until Gates 1 and 2 pass. That includes the 30-run
certification, Lever/Ashby, phone hosting, and any other hosting.

## Human intervention rule

The owner is never the debugging harness.

- Do not ask him to paste commands, reproduce failures, fetch logs, or test
  speculative fixes.
- He is involved only for genuine human gates: CAPTCHA, final review or
  submit, account auth, product decisions, and access only he can grant.
- He may also be asked for a final verification after automated proof.
- A failed test goes back into automated investigation.

## Failure rule

1. Reproduce the failure automatically.
2. Inspect the trace, logs, DOM, and code.
3. Find the root cause.
4. Repair the smallest responsible layer.
5. Add regression coverage.
6. Verify again.

Do not add infrastructure to work around an unproven assumption.

## Out of scope now

- Browser-provider abstraction
- A browser service or container
- noVNC
- MCP
- A new queue architecture
- New mobile features
- New Android/PRoot work
- Lever/Ashby fixes
- Scheduler/autopilot expansion
- Expanded certification infrastructure

## Architectural kill criterion

If the local fixture proof needs Android, external browser ownership, CDP, or
cross-layer hacks, reconsider the runtime, not the product.

## Superseded documents

- `docs/GREENHOUSE_CERTIFICATION.md` (the Android Ubuntu PRoot certification
  host from PRs #21 to #25) is superseded by this contract. It is kept as
  legacy reference.
