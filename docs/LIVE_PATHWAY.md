# Live application pathway

Updated: 2026-11-02

## Where live applications run today

Live, supervised job applications currently run through the **JobTomatik**
engine ([TheHighBrid/JobTomatik](https://github.com/TheHighBrid/JobTomatik)),
not through HunterXJob. The owner reports more than 100 submitted applications,
with a human handoff for CAPTCHAs and ambiguous questions and a confirmation
evidence trail.

Evidence label in this repo: **documented but unverified**. Nothing here is
called production-evidenced until a pointer to the evidence (ledger rows,
confirmation records or an evidence export) is added. The labels are defined in
[ROADMAP_STATUS.md](ROADMAP_STATUS.md#evidence-labels).

## JobTomatik services involved (file names only)

These files are listed so they can be found. No JobTomatik code is copied into
this repo.

- Supervised submission: `backend/app/services/supervised_submission.py`,
  `supervised_submission_integration.py`, `supervised_runtime.py`,
  `supervised_platforms.py`, and the API in `backend/app/api/supervised_submissions.py`.
- Human handoff (CAPTCHA, ambiguous questions): `backend/app/services/submission_handoff.py`,
  `handoff_session.py`, `browser_handoff.py`, `handoff_recovery.py`,
  `handoff_notifications.py`, and the API in `backend/app/api/handoffs.py`.
- Evidence: `backend/app/services/evidence_ledger.py`,
  `platform_submission_evidence.py`, `submission_evidence_review.py`, and the API in
  `backend/app/api/submission_evidence_reviews.py`.
- Application ledger: `backend/app/api/pilot_ledger.py`,
  `greenhouse_pilot_ledger.py` and `lever_pilot_ledger.py`.

## What HunterXJob does and does not do

- `v2/` stays dry-run by design. It discovers, scores, prepares materials and
  plans form answers, then stops before any submit. Its live-submit lock and the
  recovery gates in [RECOVERY_CONTRACT.md](RECOVERY_CONTRACT.md) apply only to v2
  and do not affect the JobTomatik live pathway.
- `backend/` (v1) contains submit code but no handoff or evidence trail, and it is
  not the live pathway.
- None of the v2 answer-matching work currently reaches live applications.

## Open decision

How HunterXJob relates to the JobTomatik engine is an owner decision. Until it is
made, there is no integration, porting or tiering work in this repo.
