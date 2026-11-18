# HunterXJob native live application pathway

## Owner decision

HunterXJob is the independent, end-to-end job-application engine. It must not outsource live submission, session handoff, retry, evidence, ledger or state-machine ownership to JobTomatik or another job-application engine.

## Native execution

- `v2/app/autonomous_loop.py` orchestrates discovery, deduplication, scoring, preparation, answer resolution and browser execution.
- `v2/app/browser_filler.py` fills and verifies the form in the HunterXJob-owned browser.
- `v2/app/submission_engine.py` executes the explicitly authorized submission and verifies confirmation evidence.
- `v2/app/human_intervention.py` supplies native in-session human intervention when necessary. No browser-process/session transfers to another engine.
- `v2/app/evidence.py`, the local database and `v2/app/certification_system.py` manage and evaluate evidence.

The autonomous loop passes its native `execute_live_submission` callback to the browser filler only when its run configuration, environment setting and database feature flag permit live submission. Otherwise it runs in dry-run mode. Existing truth guard, deduplication, kill switch and confirmation requirements remain in force.

## Evidence

The [roadmap](ROADMAP_STATUS.md) records 17 historical live submissions across five employers and links the [certification dossier](../evidence/hunterx-certification-dossier.json). These are historical repository evidence, not a claim that all adapters currently support live execution. Distinguish verified live receipts from recorded dry-runs, fixture tests, and unverified claims. Never submit an application solely to test this documentation.

The earlier proposal to route submissions through another engine was rejected (PR #49 and #50 were closed, unmerged). Gate 1 and Gate 2 in the [recovery contract](RECOVERY_CONTRACT.md) remain dry-run and safety proofs; they do not define all later production functionality.
