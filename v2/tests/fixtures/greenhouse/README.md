# Greenhouse form fixtures

Sanitized snapshots of real, public Greenhouse postings, captured 2026-10-01
for offline tests. Nothing here came from an application submission. Each one
was read with a GET request to a public endpoint.

| File | Source |
|---|---|
| `asana_8165477.json` | `GET https://boards-api.greenhouse.io/v1/boards/asana/jobs/8165477?questions=true` (Vancouver, BC; location question, demographic question, GDPR demographic consent, legal certification, export-control question) |
| `gitlab_8556658002.json` | `GET https://boards-api.greenhouse.io/v1/boards/gitlab/jobs/8556658002?questions=true` (EEOC compliance block, country selects, residence-country sponsorship) |
| `d2l_7696196.json` | `GET https://boards-api.greenhouse.io/v1/boards/d2l/jobs/7696196?questions=true` (Canada work eligibility, multi-select, salary range, single-option consent) |
| `asana_8165477_dom.json` | Field structure read with `app.greenhouse_browser.DOM_EXTRACT_JS` from `https://job-boards.greenhouse.io/embed/job_app?for=asana&token=8165477`. The page was loaded read-only: non-GET requests were aborted, and nothing was clicked or typed. |

How they were sanitized:
- The job description (`content`), EEOC notice text, and demographic notice text were replaced with placeholders.
- Internal IDs, departments, offices, and metadata were dropped.
- Country select lists with about 200 entries were cut down to Canada, India, Netherlands, United Kingdom, and United States.
- For the DOM snapshot, page body text was removed. Only field ids, names, roles, labels, and requiredness were kept.

`../sample_form.html` is the old hand-written sample form, which used to live in
`app/adapter_runtime.py` as `GREENHOUSE_FIXTURE`. Only tests may use it. The app can no longer reach it.
