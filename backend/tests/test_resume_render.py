"""render_resume_pdf should produce a real, non-trivial PDF file.

Skipped gracefully (not failed) if Playwright's Chromium browser isn't
installed in this environment.
"""
from __future__ import annotations

import pytest

from app.services.resume_render import render_cover_letter_pdf, render_resume_pdf

SAMPLE_RESUME = {
    "name": "Jordan Rivers",
    "email": "jordan.rivers@example.com",
    "phone": "+1 555-123-4567",
    "location": "Remote",
    "linkedin_url": "linkedin.com/in/jordanrivers",
    "summary": "Backend engineer focused on distributed systems and developer tooling.",
    "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
    "experience": [
        {
            "title": "Senior Backend Engineer",
            "company": "Acme Corp",
            "location": "Remote",
            "start_date": "2021",
            "end_date": None,
            "bullets": [
                "Migrated billing pipeline to event-driven architecture, tripling throughput.",
                "Reduced on-call pages by 50% via better alerting and runbooks.",
            ],
        }
    ],
    "education": [
        {"degree": "B.S. Computer Science", "school": "State University", "end_date": "2018"}
    ],
}


def _playwright_chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def playwright_available():
    return _playwright_chromium_available()


def test_render_resume_pdf(tmp_path, playwright_available):
    if not playwright_available:
        pytest.skip("Playwright Chromium browser is not installed in this environment")

    output_path = str(tmp_path / "resume.pdf")
    result_path = render_resume_pdf(SAMPLE_RESUME, output_path)

    assert result_path == output_path
    from pathlib import Path

    pdf_file = Path(output_path)
    assert pdf_file.exists()
    # A real rendered PDF should be at least a few KB, not an empty/broken file.
    assert pdf_file.stat().st_size > 1000


def test_render_cover_letter_pdf(tmp_path, playwright_available):
    if not playwright_available:
        pytest.skip("Playwright Chromium browser is not installed in this environment")

    profile = {
        "full_name": "Jordan Rivers",
        "email": "jordan.rivers@example.com",
        "phone": "+1 555-123-4567",
        "location": "Remote",
    }
    output_path = str(tmp_path / "cover_letter.pdf")
    result_path = render_cover_letter_pdf(
        profile, "Dear Hiring Manager,\n\nI'm excited to apply...", output_path
    )

    from pathlib import Path

    pdf_file = Path(result_path)
    assert pdf_file.exists()
    assert pdf_file.stat().st_size > 500
