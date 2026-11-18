"""Comprehensive tests for HunterXJob multi-platform expansion.

Validates:
1. Platform detection across Low, Medium, and High difficulty ATS tiers.
2. Workable, Breezy HR, and Recruitee job refs, payloads, and form extraction.
3. LiveFormProvider routing and integration.
4. Submit button selector coverage across all platform families.
5. Adapter registry and catalog completeness.
"""
from __future__ import annotations

import httpx
import pytest

from app.adapter_runtime import (
    ADAPTER_CATALOG,
    REGISTRY,
    detect_platform,
    platform_for_job,
)
from app.breezy_form import (
    BreezyJobRef,
    breezy_ref_for_job,
    parse_breezy_payload,
    parse_breezy_ref,
)
from app.config import Settings
from app.discovery import configured_sources, workable_jobs, breezy_jobs, recruitee_jobs
from app.form_engine import ControlType
from app.models import AdapterMaturity
from app.real_forms import SUPPORTED_PLATFORMS, LiveFormProvider
from app.recruitee_form import (
    RecruiteeJobRef,
    parse_recruitee_payload,
    parse_recruitee_ref,
    recruitee_ref_for_job,
)
from app.submission_engine import SUBMIT_BUTTON_SELECTORS
from app.workable_form import (
    WorkableJobRef,
    parse_workable_payload,
    parse_workable_ref,
    workable_ref_for_job,
)


class MockJob:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_platform_detection_across_tiers():
    """Verify URL detection across Low, Medium, and High difficulty ATS platforms."""
    # Low difficulty
    assert detect_platform("https://jobs.lever.co/stripe/uuid") == "lever"
    assert detect_platform("https://boards.greenhouse.io/airbnb/jobs/12345") == "greenhouse"
    assert detect_platform("https://jobs.ashbyhq.com/cohere/65c1f098") == "ashby"
    assert detect_platform("https://apply.workable.com/spotify/j/ABC1234/") == "workable"
    assert detect_platform("https://acme.breezy.hr/p/123456-software-engineer") == "breezy"
    assert detect_platform("https://startup.recruitee.com/o/senior-developer/apply") == "recruitee"

    # Medium difficulty
    assert detect_platform("https://jobs.smartrecruiters.com/AcmeCorp/12345-dev") == "smartrecruiters"
    assert detect_platform("https://acme.bamboohr.com/careers/55") == "bamboohr"
    assert detect_platform("https://acme.teamtailor.com/jobs/999999") == "teamtailor"
    assert detect_platform("https://rippling.com/careers/rippling/job/12345") == "rippling"
    assert detect_platform("https://acme.pinpointhq.com/postings/7777") == "pinpoint"
    assert detect_platform("https://acme.applytojob.com/apply/xyz123") == "jazzhr"

    # High difficulty
    assert detect_platform("https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal/job/1234") == "workday"
    assert detect_platform("https://careers-company.icims.com/jobs/1001/job") == "icims"
    assert detect_platform("https://fa-hcm.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/requisitions/preview/5001") == "oracle"


def test_platform_for_job_prioritizes_source_and_platform():
    """Verify platform_for_job prefers explicit source/platform over heuristics."""
    job1 = MockJob(source="workable", url="https://customdomain.com/apply/123")
    assert platform_for_job(job1) == "workable"

    job2 = MockJob(source="breezy", url="https://careers.corp.com/post/456")
    assert platform_for_job(job2) == "breezy"

    job3 = MockJob(source="recruitee", url="https://jobs.corp.com/o/789")
    assert platform_for_job(job3) == "recruitee"


def test_workable_ref_parsing_and_payload():
    """Test Workable URL parsing and API response schema conversion."""
    url = "https://apply.workable.com/faire/j/B8987F86D1/apply"
    ref = parse_workable_ref(url)
    assert ref is not None
    assert ref.company == "faire"
    assert ref.shortcode == "B8987F86D1"
    assert "workable:faire" in ref.vault_scopes

    # Test payload conversion
    payload = {
        "title": "Senior Risk Specialist",
        "questions": [
            {
                "id": "q1",
                "title": "Do you have financial crime experience?",
                "type": "boolean",
                "required": True,
            },
            {
                "id": "q2",
                "title": "What are your salary expectations in CAD?",
                "type": "free_text",
                "required": True,
            },
            {
                "id": "q3",
                "title": "Notice period",
                "type": "choice",
                "choices": ["Immediately", "2 weeks", "1 month"],
                "required": False,
            },
        ],
    }

    form = parse_workable_payload(payload, ref)
    assert form.platform == "workable"
    assert form.title == "Senior Risk Specialist"
    assert len(form.controls) >= 8  # Contact fields + 3 questions

    keys = [c.key for c in form.controls]
    assert "first_name" in keys
    assert "last_name" in keys
    assert "email" in keys
    assert "resume" in keys

    # Find custom question controls
    crime_q = next(c for c in form.controls if "financial_crime" in c.key)
    assert crime_q.control_type == ControlType.CHECKBOX
    assert crime_q.required is True

    sal_q = next(c for c in form.controls if "salary" in c.key)
    assert sal_q.control_type == ControlType.TEXT

    notice_q = next(c for c in form.controls if "notice" in c.key)
    assert notice_q.control_type == ControlType.SELECT
    assert notice_q.options == ["Immediately", "2 weeks", "1 month"]


def test_breezy_ref_parsing_and_payload():
    """Test Breezy HR URL parsing and payload conversion."""
    url = "https://wealthcare.breezy.hr/p/a1b2c3d4-investigations-associate/apply"
    ref = parse_breezy_ref(url)
    assert ref is not None
    assert ref.company == "wealthcare"
    assert ref.position_id == "a1b2c3d4-investigations-associate"
    assert "breezy:wealthcare" in ref.vault_scopes

    payload = {
        "name": "Bilingual Compliance Officer",
        "questionnaire": [
            {
                "id": "bq1",
                "name": "Are you legally authorized to work in Canada?",
                "type": "radio",
                "options": ["Yes", "No"],
                "required": True,
            }
        ],
    }

    form = parse_breezy_payload(payload, ref)
    assert form.platform == "breezy"
    assert form.title == "Bilingual Compliance Officer"
    keys = [c.key for c in form.controls]
    assert "name" in keys
    assert "email" in keys
    assert "resume" in keys

    auth_q = next(c for c in form.controls if "authorized" in c.key)
    assert auth_q.control_type == ControlType.RADIO
    assert auth_q.required is True
    assert auth_q.legal is True


def test_recruitee_ref_parsing_and_payload():
    """Test Recruitee URL parsing and payload conversion."""
    url = "https://fintech.recruitee.com/o/fraud-analyst/apply"
    ref = parse_recruitee_ref(url)
    assert ref is not None
    assert ref.company == "fintech"
    assert ref.slug == "fraud-analyst"
    assert "recruitee:fintech" in ref.vault_scopes

    payload = {
        "offer": {
            "title": "Fraud Operations Specialist",
            "questions": [
                {
                    "id": "rq1",
                    "body": "Tell us about your experience managing dispute investigations.",
                    "kind": "textarea",
                    "required": True,
                }
            ],
        }
    }

    form = parse_recruitee_payload(payload, ref)
    assert form.platform == "recruitee"
    assert form.title == "Fraud Operations Specialist"
    keys = [c.key for c in form.controls]
    assert "name" in keys
    assert "email" in keys
    assert "resume" in keys

    dispute_q = next(c for c in form.controls if "dispute" in c.key)
    assert dispute_q.control_type == ControlType.TEXTAREA
    assert dispute_q.required is True


def test_live_form_provider_supported_platforms():
    """Verify LiveFormProvider includes all Tier 1 and Tier 2 supported platforms."""
    for p in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "workable", "breezy", "recruitee"):
        assert p in SUPPORTED_PLATFORMS

    settings = Settings()
    provider = LiveFormProvider(settings)

    # Workable routing
    w_job = MockJob(source="workable", board="faire", external_id="B8987F86D1", url="https://apply.workable.com/faire/j/B8987F86D1/")
    # When mocking the network client
    mock_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"title": "Mock Workable Job", "questions": []},
            )
        )
    )
    provider.client = mock_client
    form = provider.fetch(w_job)
    assert form.platform == "workable"
    assert form.title == "Mock Workable Job"


def test_submit_button_selectors_coverage():
    """Verify that submit button selectors cover all platform variations."""
    assert any("data-ui=\"application-form-submit\"" in s for s in SUBMIT_BUTTON_SELECTORS)
    assert any("apply-button" in s for s in SUBMIT_BUTTON_SELECTORS)
    assert any("btn-submit" in s for s in SUBMIT_BUTTON_SELECTORS)
    assert any("submit_app" in s for s in SUBMIT_BUTTON_SELECTORS)
    assert any("ashby-application-form-submit-button" in s for s in SUBMIT_BUTTON_SELECTORS)
    assert any("data-automation-id" in s for s in SUBMIT_BUTTON_SELECTORS)


def test_adapter_catalog_completeness():
    """Verify that ADAPTER_CATALOG registers Low, Medium, and High difficulty ATS platforms."""
    expected_platforms = [
        "greenhouse",
        "lever",
        "ashby",
        "workable",
        "breezy",
        "recruitee",
        "smartrecruiters",
        "bamboohr",
        "teamtailor",
        "rippling",
        "pinpoint",
        "jazzhr",
        "workday",
        "icims",
        "oracle",
        "taleo",
    ]
    for platform in expected_platforms:
        assert platform in ADAPTER_CATALOG, f"Missing {platform} in ADAPTER_CATALOG"
        assert platform in REGISTRY.names(), f"Missing {platform} in REGISTRY"


def test_discovery_configured_sources():
    """Verify discovery recognizes workable, breezy, and recruitee configured boards."""
    settings = Settings(
        greenhouse_board_tokens="faire",
        lever_companies="pointclickcare",
        ashby_orgs="cohere",
        workable_companies="spotify,uber",
        breezy_companies="wealthcare",
        recruitee_companies="fintech",
    )
    sources = configured_sources(settings)
    assert ("workable", "spotify") in sources
    assert ("workable", "uber") in sources
    assert ("breezy", "wealthcare") in sources
    assert ("recruitee", "fintech") in sources
