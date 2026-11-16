# Ledger import (schema v1)

Status: **independently tested / repo-only.** The importer and its tests exist in
this repo and run in CI against made-up records only. No real export has been
imported, so nothing here is production-evidenced.

## Purpose

The live applications were submitted through JobTomatik's supervised-submit,
handoff, evidence and ledger services, not through HunterXJob `v2/`, which is
dry-run only. Until an export of those applications is imported, they stay
"documented but unverified" in this repo. This document defines the export
format HunterXJob accepts and how each record is labelled.

The importer (`v2/app/ledger_import.py`) is offline: it reads a file, validates
it and prints a summary. It does not write to the database, call the network,
open a browser, or submit anything.

## File format

UTF-8 JSON Lines: one JSON object per line. Blank lines are ignored.

| Field | Required | Type | Rule |
|---|---|---|---|
| `schema_version` | yes | string | Must be `hx-ledger-import-v1`. |
| `record_id` | yes | string | Unique within the file; an opaque export id. |
| `source_system` | yes | string | System that submitted, e.g. `jobtomatik`. |
| `company` | yes | string | Employer name. |
| `job_title` | yes | string | Posting title. |
| `job_url` | yes | string | `http`/`https` URL of the posting. |
| `ats` | no | string | e.g. `greenhouse`, `lever`, `workday`. |
| `submitted_at` | yes | string | ISO 8601 with a timezone offset. |
| `outcome` | yes | string | One of `confirmed_submission`, `probable_submission`, `uncertain_outcome`, `confirmed_non_submission`. |
| `evidence` | yes | list | Evidence items (below); may be empty. |
| `human_handoff` | no | list | Any of `captcha`, `ambiguous_question`, `login`, `upload`, `other`. |
| `notes` | no | string | Free text with no personal data. |

Any other field is rejected as unknown.

### Evidence item

| Field | Required | Rule |
|---|---|---|
| `type` | yes | `confirmation_page`, `confirmation_email`, `receipt_id`, `screenshot_proof` or `http_redirect`. `dry_run_ledger` is rejected: a rehearsal is not proof of a submission. |
| `source` | yes | `browser_dom`, `inbound_email`, `network_response` or `manual_review`. |
| `ref` | yes | Pointer to where the evidence is kept (path, message id, URL). The evidence itself is not imported. |
| `sha256` | no | 64 lowercase hex characters, digest of the referenced artifact. |

Types and sources reuse the enums in `v2/app/evidence_reconciliation.py`.

## Personal data

The export must not carry the applicant's personal data. A record containing
any of `name`, `first_name`, `last_name`, `email`, `phone`, `address`,
`salary`, `salary_expectation`, `resume`, `cover_letter`, `answers`,
`date_of_birth`, `sin` or `ssn` is rejected (not silently stripped), so a bad
export fails loudly. Never commit a real export to this repo; tests use only
made-up records under `v2/tests/fixtures/ledger/`.

## Labels

Each accepted record gets one of the roadmap labels:

- **production-evidenced**: `outcome` is `confirmed_submission` and at least one
  evidence item has a reliability weight of 0.85 or more in
  `RELIABILITY_WEIGHTS` (for example a receipt id or confirmation page from the
  browser DOM, or a confirmation email). The `ref` is the pointer to the evidence.
- **documented but unverified**: everything else, including confirmed records
  with only weak evidence (an HTTP redirect) or none.

Rejected records are listed with their line number and every error found.

## Running it

```bash
cd v2
python scripts/ledger_import.py --input /path/outside/repo/export.jsonl
```

It prints a JSON summary (accepted, rejected, counts per label, rejection
reasons) and exits 1 if any record was rejected.

## Not in scope

Writing imported records to the database, linking them to `Application` rows,
and any JobTomatik integration wait on Mohamed's decision about how HunterXJob
relates to the JobTomatik engine.
