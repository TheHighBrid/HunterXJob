"""Greenhouse/Lever adapters should parse mocked API responses into JobPostingDTOs."""
import httpx
import respx

from app.services.job_sources.generic_feed import GenericFeedAdapter
from app.services.job_sources.greenhouse import GreenhouseAdapter
from app.services.job_sources.lever import LeverAdapter

GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 12345,
            "title": "Senior Backend Engineer",
            "location": {"name": "Remote - US"},
            "content": "<p>We need a <b>Python</b> expert. Remote friendly.</p>",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
        },
        {
            "id": 67890,
            "title": "Product Designer",
            "location": {"name": "San Francisco, CA"},
            "content": "<p>Design beautiful things.</p>",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/67890",
        },
    ]
}

LEVER_RESPONSE = [
    {
        "id": "abc-123",
        "text": "Staff Software Engineer",
        "categories": {"location": "Remote", "team": "Platform"},
        "descriptionPlain": "Build distributed systems. Remote OK.",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
    },
    {
        "id": "def-456",
        "text": "Sales Manager",
        "categories": {"location": "New York, NY", "team": "Sales"},
        "descriptionPlain": "Own the enterprise sales pipeline.",
        "hostedUrl": "https://jobs.lever.co/acme/def-456",
    },
]


@respx.mock
def test_greenhouse_adapter_parses_jobs():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE_RESPONSE)
    )

    adapter = GreenhouseAdapter(board_tokens=["acme"])
    postings = adapter.search()

    assert len(postings) == 2
    first = postings[0]
    assert first.source == "greenhouse"
    assert first.external_id == "12345"
    assert first.title == "Senior Backend Engineer"
    assert first.company == "acme"
    assert "Python expert" in first.description
    assert "<b>" not in first.description
    assert first.remote is True
    assert first.url == "https://boards.greenhouse.io/acme/jobs/12345"


@respx.mock
def test_greenhouse_adapter_filters_by_query():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE_RESPONSE)
    )
    adapter = GreenhouseAdapter(board_tokens=["acme"])
    postings = adapter.search(query="Designer")
    assert len(postings) == 1
    assert postings[0].title == "Product Designer"


@respx.mock
def test_lever_adapter_parses_postings():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=LEVER_RESPONSE)
    )

    adapter = LeverAdapter(companies=["acme"])
    postings = adapter.search()

    assert len(postings) == 2
    first = postings[0]
    assert first.source == "lever"
    assert first.external_id == "abc-123"
    assert first.title == "Staff Software Engineer"
    assert first.location == "Remote"
    assert first.remote is True
    assert first.url == "https://jobs.lever.co/acme/abc-123"


@respx.mock
def test_lever_adapter_handles_http_error_gracefully():
    respx.get("https://api.lever.co/v0/postings/badcompany").mock(
        return_value=httpx.Response(404)
    )
    adapter = LeverAdapter(companies=["badcompany"])
    postings = adapter.search()
    assert postings == []


@respx.mock
def test_generic_feed_adapter_parses_json_list():
    feed_url = "https://example.com/jobs.json"
    respx.get(feed_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "1",
                        "title": "DevOps Engineer",
                        "company": "Widgets Inc",
                        "location": "Remote",
                        "description": "Manage our infra.",
                        "url": "https://example.com/jobs/1",
                    }
                ]
            },
        )
    )
    adapter = GenericFeedAdapter(feed_urls=[feed_url])
    postings = adapter.search()
    assert len(postings) == 1
    assert postings[0].title == "DevOps Engineer"
    assert postings[0].company == "Widgets Inc"
    assert postings[0].remote is True


@respx.mock
def test_generic_feed_adapter_parses_rss():
    feed_url = "https://example.com/jobs.rss"
    rss_body = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>QA Engineer</title>
        <link>https://example.com/jobs/qa</link>
        <description>Test everything.</description>
      </item>
    </channel></rss>"""
    respx.get(feed_url).mock(
        return_value=httpx.Response(200, content=rss_body, headers={"content-type": "application/xml"})
    )
    adapter = GenericFeedAdapter(feed_urls=[feed_url])
    postings = adapter.search()
    assert len(postings) == 1
    assert postings[0].title == "QA Engineer"
    assert postings[0].url == "https://example.com/jobs/qa"
