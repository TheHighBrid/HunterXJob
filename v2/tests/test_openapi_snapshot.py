"""The committed OpenAPI snapshot is what the mobile app's types are generated from."""

from app.cli import OPENAPI_SNAPSHOT, openapi_text


def test_openapi_snapshot_is_current():
    assert OPENAPI_SNAPSHOT.is_file(), "run ./hunterx openapi"
    assert OPENAPI_SNAPSHOT.read_text(encoding="utf-8") == openapi_text(), (
        "v2/openapi.json is out of date: run `./hunterx openapi` (or `python -m app.cli openapi`) "
        "and then `npm run api:generate` in mobile/"
    )


def test_remote_routes_have_typed_responses():
    import json

    schema = json.loads(openapi_text())
    typed = ["/api/settings", "/api/reports/summary", "/api/jobs", "/api/jobs/{job_id}", "/api/review-tasks",
             "/api/review-tasks/{task_id}", "/api/scheduler/status", "/api/kill-switch", "/api/backups"]
    for path in typed:
        ok = schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok, path
        assert ok != {} and ok.get("additionalProperties") is not True, path
