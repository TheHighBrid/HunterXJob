# Gate 1 negative fixtures

Gate 1 (`scripts/gate1.py`, see `docs/RECOVERY_CONTRACT.md`) serves the existing
`../sample_form.html` and `../greenhouse/d2l_7696196.json` over loopback HTTP.
These snippets are injected into `sample_form.html` (just before `</body>`) only by
the gate's negative tests, to prove the gate fails when a page tries to write
anything. They contain no real data and are never served by the app itself.

| File | What the page tries to do | The gate must report |
|---|---|---|
| `inject_post_beacon.html` | `fetch()` POST to the fixture origin (like an analytics beacon) | `zero_submit_and_non_get` fails (non-GET attempt, aborted in the browser); the dry-run itself still completes with a warning |
| `inject_head_request.html` | `fetch()` HEAD to an expected fixture route | `zero_submit_and_non_get` fails (HEAD is not a GET, so it is aborted and counted) |
| `inject_auto_submit_post.html` | switches the form to POST and calls `requestSubmit()` | the submission is refused in the page and recorded; the dry-run fails closed (`needs_review`) |
| `inject_auto_submit_get.html` | calls `form.submit()` on a GET form (fires no `submit` event, no non-GET request) | the call is refused in the page and recorded; the dry-run fails closed (`needs_review`) |
| `inject_iframe_form_submit.html` | `form.submit()` of a GET form targeting a hidden iframe, and a form inside an `srcdoc` iframe submitting itself to `_top` | both attempts are refused and recorded (submit attempts are read from every frame); the dry-run fails closed |
