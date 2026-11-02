# Live application pathway

Updated: 2026-11-02

This page records where live job applications run today and how that relates to
HunterXJob. Every claim carries one of the four labels defined in
[ROADMAP_STATUS.md](ROADMAP_STATUS.md#evidence-labels).

## Where live submissions run

**Documented but unverified (in this repo).** The owner reports more than 100
live applications submitted end to end, with human handoff for CAPTCHAs and
ambiguous questions, and confirmation evidence. Those submissions ran through
the supervised services of the separate JobTomatik project
(<https://github.com/TheHighBrid/JobTomatik>), not through HunterXJob. This repo
holds no ledger export or evidence for them, so nothing here is labelled
production-evidenced. The label changes only when a pointer to that evidence is
added.

The JobTomatik files involved, by name only (no code is copied into HunterXJob):

| Concern | JobTomatik files |
|---|---|
| Supervised submit | `backend/app/api/supervised_submissions.py`, `backend/app/schemas/supervised_submission.py`, `backend/app/services/supervised_followup.py`, `backend/app/services/supervised_pilot_intake.py` |
| Human handoff | `backend/app/api/handoffs.py`, `backend/app/services/handoff_session.py`, `backend/app/services/submission_handoff.py`, `backend/app/services/browser_handoff.py`, `backend/app/services/handoff_recovery.py` |
| Submission evidence | `backend/app/services/platform_submission_evidence.py`, `backend/app/services/submission_evidence_review.py`, `backend/app/api/submission_evidence_reviews.py` |
| Application ledger | `backend/app/services/evidence_ledger.py`, `backend/app/api/pilot_ledger.py`, `backend/app/api/greenhouse_pilot_ledger.py`, `backend/app/api/lever_pilot_ledger.py` |

File names are as of JobTomatik `main` at `1e51722` (2026-10-21).

## What HunterXJob does

- **`v2/` stays dry-run.** It discovers, scores, plans answers and rehearses
  forms, and never submits by design. *Independently tested:* the gates and the
  dry-run batch checks in CI (`v2-tests.yml`, jobs `test` and `gate1`).
- **v2's submission lock does not affect the live pathway.** The "unattended
  live submit: blocked" status in the roadmap applies to HunterXJob only. It
  says nothing about JobTomatik's supervised services.
- **`backend/` (v1) has submit code but no handoff or evidence.** It is not the
  live pathway. *Repo-only.*
- **HunterXJob's answer-matching work (#36, #40, #45) does not reach production
  yet.** *Independently tested* in CI, not production-evidenced.

## Open decision

How HunterXJob relates to the JobTomatik engine is the owner's call. Until it is
made, no integration, porting or tier work happens here.
