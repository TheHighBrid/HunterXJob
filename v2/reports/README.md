# Fixture reports

Generated offline artifacts committed for CI lock-in.

- `fixture_answer_coverage.json` — fill / review / skip counts from sanitized Greenhouse, Lever, and Ashby fixtures using `examples/profile.example.yaml` plus explicit policy answers for classifier keys. Regenerate with `python scripts/fixture_answer_coverage.py`.
- `fixture_answer_gaps.json` — the review and skip controls from the coverage run, grouped by gap category and control type. Regenerate with `python scripts/fixture_answer_gaps.py`.
