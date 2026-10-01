# Code quality configuration

Static analysis runs in two places: Codacy on every pull request, and the
`V2 Tests` workflow (Ruff + Bandit on `v2/`). The configuration lives in the
repository root so both use the same rules.

| File | Used by | What it does |
|---|---|---|
| `.codacy.yml` | Codacy (always) | Excludes `**/tests/**` from **Bandit only**. Bandit's B101 (`assert_used`) flags every pytest assertion; application code keeps every Bandit check. Also excludes two generated files from all analysis: `v2/openapi.json` and `mobile/src/api/schema.d.ts`. CI checks that both match the code. |
| `bandit.yml` | `bandit -c bandit.yml`, CI, and Codacy if "Use configuration file" is on for Bandit | Skips B101 only for files under `tests/`. All other Bandit tests stay on for tests and app code. |
| `ruff.toml` | `ruff check`, CI, and Codacy if "Use configuration file" is on for Ruff | `app` is first-party for both `backend/` and `v2/` (fixes false import-order findings); `S101` is ignored only under `tests/`; FastAPI's `Depends`/`Query`/... are declared immutable for B008 instead of disabling B008; `UP042` (StrEnum) is not applied because it changes `str()` of existing enums. |

v2 also uses `Annotated[..., Depends(...)]` dependencies, FastAPI's recommended
style, which B008 does not flag at all.

## Owner action in Codacy (one-time)

Codacy reads `.codacy.yml` automatically. It only reads tool configuration files
after you enable them on **Code patterns**: select **Ruff**, turn on
**Configuration file**; repeat for **Bandit**. Until then Codacy keeps its own
Ruff settings, which treat `app` in `tests/` as third-party and report import order
there.

Note: with a `.codacy.yml` present, ignore rules set in the Codacy UI ("Ignored
files") no longer apply; put any such paths under `exclude_paths` in the file.

## Run locally

```bash
python3.12 -m venv ~/.cache/hx-lint && ~/.cache/hx-lint/bin/pip install ruff bandit
~/.cache/hx-lint/bin/ruff check v2
~/.cache/hx-lint/bin/bandit -q -c bandit.yml -r v2/app v2/scripts v2/tests
```
