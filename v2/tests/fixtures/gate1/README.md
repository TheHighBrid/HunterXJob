# Gate 1 negative fixtures

Gate 1 (`scripts/gate1.py`, see `docs/RECOVERY_CONTRACT.md`) serves the existing
`../sample_form.html` and `../greenhouse/d2l_7696196.json` over loopback HTTP.
These snippets are injected into `sample_form.html` (just before `</body>`) only by
the gate's negative tests, to prove the gate fails when a page tries to write
anything. They contain no real data and are never served by the app itself.

| File | What the page tries to do | The gate must report |
|---|---|---|
| `inject_post_beacon.html` | `fetch()` POST to the fixture origin | `zero_submit_and_non_get` fails (non-GET attempt) |
| `inject_auto_submit_post.html` | switches the form to POST and calls `requestSubmit()` | `zero_submit_and_non_get` fails (submit event and non-GET attempt) |
| `inject_auto_submit_get.html` | calls `form.submit()` on a GET form (no non-GET request at all) | `zero_submit_and_non_get` fails (navigation away / unexpected request) |
