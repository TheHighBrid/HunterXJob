# Greenhouse live certification

This is the next production milestone after PR #20. It does not require a cloud VM.

## Runtime

Use the existing Android device as the host:

```text
Android
  -> Ubuntu PRoot
  -> Python 3.12 HunterXJob v2
  -> Playwright + Chromium
  -> SQLite verified profile / answer policies
  -> public Greenhouse forms
  -> browser-verified read-only dry-runs
  -> JSON certification report
```

Plain Termux is not the certification runtime because Playwright Chromium is unavailable there. Oracle or another 24/7 VM is intentionally deferred until local certification proves a hosting requirement.

## 1. Prepare the Ubuntu PRoot runtime

From `HunterXJob/v2` inside Ubuntu PRoot:

```bash
bash scripts/prepare_proot_certification.sh
```

The preparation command is deliberately opinionated for certification:

- installs and uses Python 3.12, matching CI;
- creates or reuses a valid `.venv`;
- installs HunterXJob test/browser dependencies and Playwright Chromium;
- keeps `APPLICATION_MODE=dry_run`;
- forces `ALLOW_LIVE_SUBMISSION=false` and `CONTINUOUS_RUN_ENABLED=false`;
- enables Greenhouse browser verification and leaves browser fallback off;
- fills the starter Greenhouse board list only when no Greenhouse boards are configured;
- migrates the database and runs `./hunterx doctor`;
- logs the complete preparation run to `logs/prepare-certification-latest.log` and fails early on critically low disk space.

It does not enable automatic submission, submit an application, or create a cloud account.

## 2. Load and verify the real profile

The certification runner refuses to start unless the verified-facts profile is ready. Existing verified facts and answer policies are read from the normal SQLite database. No answer values are copied into the report.

Use the mobile Profile screen or the existing CLI import/verify workflow. Imported résumé facts remain unverified until explicitly attested.

A ready profile must include the minimum facts already enforced by HunterXJob: verified first name, last name, email, and at least one verified employment or education fact. Employer-specific legal, sensitive, and ambiguous questions may still stop at `needs_review`; that is correct fail-closed behavior.

## 3. Run the 30-form gate

```bash
.venv/bin/python scripts/greenhouse_certify.py
```

Defaults:

- 30 browser-verified live Greenhouse plans;
- at least 5 distinct Greenhouse employer boards;
- round-robin selection across boards so one employer cannot consume the sample;
- the real verified profile and scoped answer policies from SQLite;
- Playwright Chromium rendering for every selected form;
- no typing, clicking, uploading, POSTing, or submitting;
- final report written to `data/certification/greenhouse-<UTC timestamp>.json`;
- in-progress state written atomically to `data/certification/greenhouse-in-progress.json` after every posting.

### Crash-safe resume

The certification runner is resumable by default. If Android kills Ubuntu PRoot, Chromium crashes, the terminal closes, or the Python process is interrupted after some forms have completed, run the exact same command again:

```bash
.venv/bin/python scripts/greenhouse_certify.py
```

The runner reloads the checkpoint, preserves the exact original posting sample and order, skips already completed forms, and continues from the first unfinished posting. A crash can therefore lose at most the single posting that was executing when the process died, not the entire certification session.

A checkpoint is accepted only when all of these still match the run that created it:

- Greenhouse board set;
- requested run count and minimum board count;
- verified profile and stored answer-policy state, represented only by a SHA-256 fingerprint in the checkpoint/report;
- checked-out HunterXJob Git revision.

If any of those changed, the runner fails closed instead of mixing evidence from different configurations. Start a new certification sample explicitly with:

```bash
.venv/bin/python scripts/greenhouse_certify.py --restart
```

A passing gate deletes the in-progress checkpoint. A failing gate keeps it for inspection. JSON writes use a temporary file followed by an atomic replace so a process kill during checkpoint writing cannot destroy the previous valid checkpoint.

A `needs_review` plan counts when its rendered form was successfully browser-verified. Certification is testing that HunterXJob understands the live form and fails closed when it cannot safely answer a field. A fetch failure or a run without DOM verification does not count.

The command exits non-zero unless all required browser-verified plans are present and the minimum board diversity is met. The report contains field structure, blocker reason codes, DOM-only required fields, CAPTCHA submit boundaries, warnings, and aggregate pass/fail counts, but not answer values.

Optional overrides:

```bash
.venv/bin/python scripts/greenhouse_certify.py --boards d2l,geotab,faire,later,hootsuite --count 30 --min-boards 5
```

A custom checkpoint location is also available when needed:

```bash
.venv/bin/python scripts/greenhouse_certify.py --checkpoint /path/to/greenhouse-in-progress.json
```

## Pass gate

The local Greenhouse dry-run milestone passes only when the generated report says:

```text
Gate: PASS
browser-verified: 30/30 or better
distinct boards: 5/5 or better
flagged runs: 0
```

Do not promote Greenhouse to autonomous submission from this gate alone. Issue #9 separately requires supervised real submissions and concrete submission evidence before any maturity promotion.

## After the gate

Use the report to fix only failures actually observed on live forms. Re-run the gate after those fixes. Once the 30 browser-verified dry-runs are green, proceed to the supervised-submission milestone in issue #9. Permanent 24/7 hosting is a later deployment decision, not a prerequisite for certification.
