# Recovery contract

Owner: TheHighBrid (repository owner). Decided 2026-10-04. This decision is locked.

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

- Plain Linux at $0: the developer box plus GitHub-hosted Ubuntu runners on
  GitHub Actions. The `gate1` job is pinned to `ubuntu-24.04` so the
  Playwright 1.47 browser dependencies keep installing when `ubuntu-latest`
  moves to a newer release.
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
3. Fields are extracted, and the values planned for them are read back and
   match what was expected. (This is planned values, not values typed into
   the page; see below.)
4. A valid Playwright trace zip is written, integrity-checked, and hashed.
5. Evidence is persisted in the ledger.
6. Zero submit actions and zero non-GET requests, shown by network evidence.
7. After shutdown, the browser context, the browser process, and any child
   PIDs are gone.
8. The API request/task completes cleanly.
9. It reproduces with no human.

The gate report must explicitly record anything that was NOT proven (for
example `real_employer_site = false`).

Planned values, not typed values: the production form engine plans values
from the vault and checks the rendered page read-only. It never types into the
page, so there are no in-page "filled values" to read back. Gate 1 reads the
planned values back from the ledger and compares them with a hand-written
expected plan, and it checks the rendered fields against an independent parse
of the fixture HTML. Typing values into the page is therefore NOT proven, and
the report records it as `in_page_typing_of_values: false`.

### How Gate 1 is run

`v2/scripts/gate1.py` runs it. CI runs it in the `gate1` job of
`.github/workflows/v2-tests.yml`.

```bash
cd v2
pip install -e '.[test,browser]' && python -m playwright install --with-deps chromium
python scripts/gate1.py --runs 3      # writes v2/gate-artifacts/gate1-report.json
python -m pytest -m gate1             # negative proofs: POST, HEAD, auto-submit, iframe submit, process leak
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

The browser session is read-only: only GET requests leave the browser (HEAD
and every other method are aborted and counted), service workers are blocked,
and the page cannot submit a form (submit events are cancelled and
`form.submit()`/`requestSubmit()` are replaced by recorders in every frame).
A submit attempt makes the dry-run fail closed to review.

The report lists every browser request (method and URL) and the fixture
server's own request log. It also lists the trace SHA-256 values, the browser
PIDs that were seen, and the PIDs left after shutdown. Any non-GET attempt,
submit attempt, extra navigation, off-origin request, or unexpected server
request fails the run. The parent process also samples each run's process
tree; a run that times out is killed together with every descendant, and any
process that outlives a run fails it.

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

### How Gate 2 is run

`v2/scripts/gate2.py` runs it, on plain Linux, by hand. It never runs in CI:
`--live` refuses to start when `CI` is set. CI only runs the same runner
against Gate 1's loopback fixtures (`--rehearse`, plus
`tests/test_gate2_rehearsal.py` under `-m gate1`).

```bash
cd v2
python scripts/gate2.py --live --board <board> --job-id <id>   # one real posting, once
python scripts/gate2.py --recheck --out gate-artifacts/gate2    # re-evaluate saved observations; no network
python scripts/gate2.py --rehearse                              # loopback fixtures (what CI runs)
```

- The parent confirms the posting is open with one GET to
  `boards-api.greenhouse.io`. It retries once on a transport error or 5xx and
  never on anything else.
- A fresh process then runs the same path as Gate 1:
  - uvicorn on `127.0.0.1`;
  - the fake identity (Test / Applicant / test@example.com / 555-0100, no
    résumé) through `POST /api/answers`;
  - one job seeded at `ready_to_apply`;
  - the apply route with `GREENHOUSE_BROWSER_VERIFY=true` and
    `BROWSER_TRACE_DIR` set.
- The production engine probes the posting and fetches the form (both GET).
  Playwright then launches its own Chromium (`chromium.launch()`, never CDP)
  and loads the hosted form once, read-only.
- Real pages fire analytics beacons. The router aborts every non-GET before it
  leaves the browser, and the report records each one. An aborted attempt is
  allowed. A non-GET that was not aborted fails the run, as does any of these:
  - a rewrite;
  - a submit attempt;
  - more than one main-frame document load;
  - a main-frame URL off the form's origin and path (same-document history
    updates are allowed);
  - app HTTP that is not GET to the boards API.
- Outcome:
  - `pass`;
  - `fail`;
  - `blocked_by_bot_check`: an anti-bot handoff, or a 401/403/429/503 on the
    form document. This counts only if every safety check still held. It is a
    valid finding. Nothing retries the page or tries to get past a challenge.

Artifacts stay local and gitignored under `v2/gate-artifacts/`:

- the report (`gate2-report.json`), with:
  - the field mapping;
  - requests by method, action, and host;
  - proven and not-proven;
- `run/observations.json`;
- the Playwright trace zip, plus a full-page screenshot of the form as seen;
- the run's SQLite ledger.

The dry-run usually stops for review on a real form, because employer
questions are not in a fake vault. The ledger then holds a `dry_run_review`
row with the planned values, the fields that need review, and the trace and
screenshot hashes. That row is never sufficient and never counts as a
completed dry-run.

### Real-profile dry-run batch (after Gates 1 and 2)

`v2/scripts/dryrun_batch.py` is a thin wrapper around the Gate 2 code path for
the owner's real profile across Greenhouse, Lever and Ashby. The safety rules
are the same: dry-run only, live submission locked, zero submits, zero uploads,
no typing, Playwright-launched Chromium (never CDP), and a bot check fails
closed and is recorded. It never runs in CI: `--live` refuses when `CI` is set.
`--out` inside the repository is refused, because the output contains the
owner's identity. CI only runs `--rehearse` (loopback fixtures and the made-up
example profile, `tests/test_dryrun_batch_rehearsal.py` under `-m gate1`).

```bash
cd v2
python scripts/dryrun_batch.py --live --targets T.json --profile P.yaml --answers A.yaml \
    --guardrails G.yaml --out /private/dir --pause 30          # sequential, >= 20 s apart
python scripts/dryrun_batch.py --recheck --out /private/dir --guardrails G.yaml   # no network
python scripts/dryrun_batch.py --rehearse --out /tmp/batch --pause 0              # what CI runs
```

- The parent confirms each posting is open with one GET to the platform's
  public board API, with one retry on a transport error or 5xx.
- Each run is a fresh process with a throwaway database. That process:
  - imports the profile document;
  - stores the owner's form-answer defaults;
  - generates the résumé and cover letter (LLM off);
  - approves the résumé and rejects the cover letter in that throwaway
    database (nothing is ever uploaded in a dry-run);
  - calls the apply route.
- Lever and Ashby use the same read-only browser. Ashby's page reads its form
  through allowlisted GraphQL *queries*. The router re-issues those as GET, so
  only GET leaves the browser, and the report counts them. Every other non-GET
  is aborted, as on Greenhouse.
- Extra checks on top of the Gate 2 checks:
  - live submission is locked;
  - the page inspector has no input APIs;
  - every planned answer comes from a verified profile fact, an owner answer,
    or approved material, and no "never auto-answer" topic is answered;
  - the generated materials contain none of the owner's claims to avoid.
- A run is retried once only for a transient network error. A crashed run stops
  the batch.

## Human intervention rule

The owner is never the debugging harness.

- Do not ask the owner to paste commands, reproduce failures, fetch logs, or
  test speculative fixes.
- The owner is involved only for genuine human gates: CAPTCHA, final review
  or submit, account auth, product decisions, and access only the owner can
  grant.
- The owner may also be asked for a final verification after automated proof.
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

## Superseded and on-hold documents

- `docs/GREENHOUSE_CERTIFICATION.md` (the Android Ubuntu PRoot certification
  host from PRs #21 to #25) is superseded by this contract. It is kept as
  legacy reference.
- `docs/DEPLOY_VM.md` (unattended hosting on a cloud VM) is on hold, because
  this contract rules out a cloud VM and puts hosting out of scope until
  Gates 1 and 2 pass. Its content is unchanged and kept for later.
