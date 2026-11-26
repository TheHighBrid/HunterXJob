# HunterXJob owner architecture rules

## Non-negotiable separation

HunterXJob is the sole, standalone end-to-end job application engine. The owner explicitly rejects integrating JobTomatik into HunterXJob, especially for live submission, handoffs, retries, evidence, ledgers or state-machine operations.

- Keep native discovery, scoring, answer vault, truth guard, browser filler, autonomous loop, submission engine, deduplication and SQLite evidence.
- Do not import, call, clone, port or depend on JobTomatik's submission, browser/session handoff, retry or state-machine services. Do not introduce cross-repository runtime dependencies or external job-application engines.
- Maintain one HunterXJob-owned browser session. Human intervention is permitted only when actually required; keep session ownership local and fail closed.
- Preserve explicit live authorization, kill switch, truthful answers, duplicate prevention, confirmation verification and evidence integrity. Do not relax safety gates or claim live success without evidence.
- Dry-run is a mode, not the product architecture. Gate-specific dry-run restrictions remain in force for those gates.
- Do not regress independently implemented answer-vault and question-matching capabilities or remove evidence records.
- Changes proposing replacement of HunterXJob's native live pathway must be rejected unless the repository owner explicitly changes this decision.
- Run `cd v2 && python -m pytest tests/test_submission_independence.py` on changes to the execution path. Never weaken the independence regression check to accommodate external submission services.

Read [docs/LIVE_PATHWAY.md](docs/LIVE_PATHWAY.md) and [docs/RECOVERY_CONTRACT.md](docs/RECOVERY_CONTRACT.md) before touching live execution. Validate changes with offline tests; never submit real applications in CI.
