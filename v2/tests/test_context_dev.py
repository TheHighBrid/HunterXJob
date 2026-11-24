"""Context.dev public-page fallback tests. Zero live API calls."""
from types import SimpleNamespace

import httpx
import pytest

from app.context_dev import ContextDevError, enrich_record, public_url, scrape_url
from app.config import Settings
from app.discovery import JobRecord, discover_run

KEY = "offline-test-key"
OPTIONS = SimpleNamespace(context_dev_cache_hours=6, context_dev_timeout_seconds=20,
                          context_dev_budget_seconds=25, context_dev_zdr=False)


def _client(callback):
    return httpx.Client(transport=httpx.MockTransport(callback))


def test_partial_outputs_keep_successful_markdown_and_exact_request_shape():
    seen = []
    def callback(request):
        import json
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json={
            "url": body["url"], "isPartial": True, "request_id": "fixture",
            "json": {"requested": True, "success": False, "error_code": "EXTRACTION_FAILED", "data": None},
            "markdown": {"requested": True, "success": True, "data": "Live description"},
        })
    with _client(callback) as http:
        output = scrape_url("https://jobs.example.com/123", formats={"json": True, "markdown": True},
                            api_key=KEY, json_schema={"type": "object", "properties": {}}, client=http)
    assert output["is_partial"] is True
    assert output["outputs"]["json"]["error_code"] == "EXTRACTION_FAILED"
    assert output["outputs"]["markdown"]["data"] == "Live description"
    assert seen[0]["maxAgeMs"] == 21_600_000
    assert seen[0]["timeoutOpts"]["milliseconds"] == 20000


def test_only_missing_job_fields_are_enriched():
    record = SimpleNamespace(url="https://jobs.example.com/a", title="Source title", company="",
                             location="", remote=False, description="")
    def callback(request):
        return httpx.Response(200, json={
            "url": record.url,
            "json": {"requested": True, "success": True, "data": {
                "job_title": "Should not overwrite", "employer": "Source Employer", "location": "Ottawa",
                "work_mode": "fully remote", "qualifications": ["French required"],
                "source_excerpts": {"mandatory_requirements": "French required"}}},
            "markdown": {"requested": True, "success": True, "data": "Full listing"},
        })
    with _client(callback) as http:
        output = enrich_record(record, api_key=KEY, settings=OPTIONS, client=http)
    assert record.title == "Source title"
    assert record.company == "Source Employer" and record.location == "Ottawa"
    assert record.remote is True and "French required" in record.description
    assert output["extracted"]["source_excerpts"]["mandatory_requirements"] == "French required"


def test_one_retry_only_for_transient_status():
    calls = []
    def callback(request):
        calls.append(1)
        return httpx.Response(503 if len(calls) == 1 else 200,
                              json={} if len(calls) == 1 else {
                                  "markdown": {"requested": True, "success": True, "data": "OK"}})
    with _client(callback) as http:
        result = scrape_url("https://jobs.example.com", formats={"markdown": True}, api_key=KEY, client=http)
    assert len(calls) == 2 and result["outputs"]["markdown"]["data"] == "OK"

    calls.clear()
    with _client(lambda request: (calls.append(1), httpx.Response(401))[1]) as http:
        with pytest.raises(ContextDevError, match="HTTP 401"):
            scrape_url("https://jobs.example.com", formats={"markdown": True}, api_key=KEY, client=http)
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://localhost/private", "http://127.0.0.1/",
                                 "https://10.0.0.7", "file:///etc/passwd",
                                 "https://user:pass@host.com", "https://x.internal/test"])
def test_internal_urls_are_refused(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_zdr_and_highlights_conflict():
    with pytest.raises(ValueError, match="ZDR"):
        scrape_url("https://example.com", api_key=KEY, formats={"highlights": True},
                   highlights_query="topic", zdr=True)


def test_discovery_off_by_default(monkeypatch):
    from app import discovery
    monkeypatch.setattr(discovery, "configured_sources", lambda settings: [("ashby", "example")])
    monkeypatch.setattr(discovery, "_FETCHERS", {"ashby": lambda board: [
        JobRecord(source="ashby", external_id="1", title="Analyst", company="Example",
                  location="Ottawa", remote=False, url="https://example.com/job", description="brief")
    ]})
    def no_context(*args, **kwargs):
        pytest.fail("opt-out must never contact external Context.dev")
    monkeypatch.setattr(httpx.Client, "post", no_context)
    run = discover_run(Settings(_env_file=None, context_dev_enabled=False))
    assert len(run.records) == 1 and not run.errors
