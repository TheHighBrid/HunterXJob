import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from app.browser_filler import fill_application_form

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, format, *args):
        # Silence standard server logging
        pass


@pytest.fixture(scope="module")
def fixture_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def test_browser_filler_fills_all_fields_and_attaches_file(fixture_server, tmp_path):
    # Create a dummy resume PDF
    resume_file = tmp_path / "john_doe_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 dummy pdf content for testing")

    url = f"{fixture_server}/sample_form.html"
    answers = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "+1-555-0199",
        "cover_letter": "I am deeply interested in this software engineering opportunity.",
        "work_authorization": "Authorized to work in Canada",
    }
    files = {
        "resume": resume_file,
    }
    labels = {
        "work_authorization": "Work authorization",
    }
    screenshot = tmp_path / "screenshot.png"
    trace = tmp_path / "trace.zip"

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            labels=labels,
            screenshot_path=screenshot,
            trace_path=trace,
            headless=True,
        )
    )

    assert result.success is True
    assert result.fields_attempted == 7
    assert result.fields_filled == 7
    assert len(result.unmatched_fields) == 0

    # Verify individual field reports
    assert result.field_reports["first_name"].read_back_value == "John"
    assert result.field_reports["last_name"].read_back_value == "Doe"
    assert result.field_reports["email"].read_back_value == "john.doe@example.com"
    assert result.field_reports["phone"].read_back_value == "+1-555-0199"
    assert "software engineering" in result.field_reports["cover_letter"].read_back_value
    assert result.field_reports["work_authorization"].read_back_value == "Authorized to work in Canada"

    # Verify file attachment
    assert len(result.files_attached) == 1
    assert result.files_attached[0].file_name == "john_doe_resume.pdf"
    assert result.files_attached[0].bytes == len(b"%PDF-1.4 dummy pdf content for testing")

    # Verify screenshot and trace
    assert screenshot.is_file()
    assert result.screenshot_sha256 is not None
    assert trace.is_file()
    assert result.trace_sha256 is not None


def test_browser_filler_handles_missing_fields(fixture_server, tmp_path):
    url = f"{fixture_server}/sample_form.html"
    answers = {
        "first_name": "Alice",
        "non_existent_field_xyz": "Some value",
    }

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            headless=True,
        )
    )

    assert result.success is False
    assert result.fields_attempted == 2
    assert result.fields_filled == 1
    assert "non_existent_field_xyz" in result.unmatched_fields
    assert result.field_reports["first_name"].matched is True
    assert result.field_reports["non_existent_field_xyz"].matched is False
    assert "Could not locate" in (result.field_reports["non_existent_field_xyz"].error or "")


def test_browser_filler_handles_dropdown_fuzzy_matching(fixture_server, tmp_path):
    url = f"{fixture_server}/sample_form.html"
    answers = {
        "first_name": "Bob",
        # Substring of "Authorized to work in Canada"
        "work_authorization": "Canada",
    }

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            headless=True,
        )
    )

    assert result.field_reports["work_authorization"].matched is True
    assert result.field_reports["work_authorization"].read_back_value == "Authorized to work in Canada"


def test_browser_filler_fills_lever_fixture(fixture_server, tmp_path):
    resume_file = tmp_path / "jane_doe_cv.pdf"
    resume_file.write_bytes(b"%PDF-1.4 dummy cv for lever test")

    url = f"{fixture_server}/ats/lever_apply.html"
    answers = {
        "name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "+1-555-9876",
        "org": "Acme Software Inc",
        "urls[LinkedIn]": "https://linkedin.com/in/janedoe",
    }
    files = {
        "resume": resume_file,
    }

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            headless=True,
        )
    )

    assert result.success is True
    assert result.field_reports["name"].read_back_value == "Jane Doe"
    assert result.field_reports["email"].read_back_value == "jane.doe@example.com"
    assert result.field_reports["phone"].read_back_value == "+1-555-9876"
    assert result.field_reports["org"].read_back_value == "Acme Software Inc"
    assert result.field_reports["urls[LinkedIn]"].read_back_value == "https://linkedin.com/in/janedoe"
    assert len(result.files_attached) == 1
    assert result.files_attached[0].file_name == "jane_doe_cv.pdf"



def test_browser_filler_enforces_dom_constraints(fixture_server):
    url = f"{fixture_server}/constraints_form.html"
    answers = {
        # String exceeding 40 chars -> should be truncated <= 40 chars
        "short_answer": "This is an overly verbose answer that definitely exceeds forty characters in total length",
        # String shorter than 80 chars -> should be expanded to >= 80 chars
        "long_narrative": "Excited to apply.",
        # Bilingual boolean match ("Yes" matching "Oui, j'accepte les conditions")
        "french_consent": "Yes",
        # Checkbox boolean
        "remote_ready": "Yes",
    }
    labels = {
        "short_answer": "Brief Summary (Max 40 chars)",
        "long_narrative": "Detailed Motivation (Min 80 chars)",
    }

    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            labels=labels,
            headless=True,
        )
    )

    assert result.success is True
    # 1. Check truncation
    short_read = result.field_reports["short_answer"].read_back_value
    assert len(short_read) <= 40
    assert result.field_reports["short_answer"].matched is True

    # 2. Check expansion
    long_read = result.field_reports["long_narrative"].read_back_value
    assert len(long_read) >= 80
    assert result.field_reports["long_narrative"].matched is True

    # 3. Check dropdown match
    assert result.field_reports["french_consent"].matched is True
    assert "j'accepte" in result.field_reports["french_consent"].read_back_value

    # 4. Check checkbox
    assert result.field_reports["remote_ready"].read_back_value is True
