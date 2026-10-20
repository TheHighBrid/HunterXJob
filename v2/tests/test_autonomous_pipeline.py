import asyncio
import http.server
import threading
from pathlib import Path

import pytest

from app.browser_filler import fill_application_form
from app.knowledge_book import KnowledgeBook
from app.materials import JobContext, build_cover_letter, build_resume, cover_letter_text
from app.render import render_pdf_bytes

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXAMPLE_YAML = Path(__file__).parent.parent / "examples" / "profile.example.yaml"


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    def log_message(self, format, *args):
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


def test_end_to_end_tailored_materials_and_form_filling(fixture_server, tmp_path):
    # 1. Load Candidate Knowledge Book
    kb = KnowledgeBook.from_yaml_file(EXAMPLE_YAML)

    # 2. Target Job Context
    job = JobContext(
        title="Senior Fraud Analyst",
        company="D2L",
        location="Ottawa, ON",
        description="Looking for an experienced AML and fraud analyst with SQL expertise.",
    )

    # 3. Generate Personalized Materials
    resume_doc = build_resume(kb.profile, job)
    resume_pdf_bytes = render_pdf_bytes(resume_doc)
    resume_path = tmp_path / "Avery_Quinn_Resume.pdf"
    resume_path.write_bytes(resume_pdf_bytes)

    letter_doc = build_cover_letter(kb.profile, job, resume_doc)
    letter_text = cover_letter_text(letter_doc)

    # 4. Resolve Form Answers
    answers = kb.get_contact_answers()
    answers["cover_letter"] = letter_text

    # Resolve screening question for Canadian work authorization
    auth_val, _ = kb.resolve_screening_question(
        "Work authorization",
        options=["Authorized to work in Canada", "Need sponsorship"],
    )
    answers["work_authorization"] = auth_val

    files = {
        "resume": resume_path,
    }

    screenshot_path = tmp_path / "filled_application_form.png"
    trace_path = tmp_path / "application_trace.zip"

    required_fields = {
        "first_name",
        "last_name",
        "email",
        "phone",
        "cover_letter",
        "work_authorization",
        "resume",
    }

    # 5. Execute Browser Form Filling & Attachment
    url = f"{fixture_server}/sample_form.html"
    result = asyncio.run(
        fill_application_form(
            url=url,
            answers=answers,
            files=files,
            required_fields=required_fields,
            screenshot_path=screenshot_path,
            trace_path=trace_path,
            headless=True,
        )
    )

    # 6. Verify Results
    assert result.success is True
    assert result.fields_filled >= 7
    assert len(result.unmatched_fields) == 0

    # Verify candidate info in DOM
    assert result.field_reports["first_name"].read_back_value == "Avery"
    assert result.field_reports["last_name"].read_back_value == "Quinn"
    assert result.field_reports["email"].read_back_value == "avery.quinn@example.com"
    assert result.field_reports["phone"].read_back_value == "+1 613 555 0142"

    # Verify personalized cover letter text is present in the textarea
    assert "Senior Fraud Analyst" in result.field_reports["cover_letter"].read_back_value
    assert "Dear Hiring Team at D2L" in result.field_reports["cover_letter"].read_back_value

    # Verify work authorization dropdown selection
    assert result.field_reports["work_authorization"].read_back_value == "Authorized to work in Canada"

    # Verify file upload
    assert len(result.files_attached) == 1
    assert result.files_attached[0].file_name == "Avery_Quinn_Resume.pdf"
    assert result.files_attached[0].bytes == len(resume_pdf_bytes)

    # Verify audit artifacts
    assert screenshot_path.is_file()
    assert trace_path.is_file()
    assert result.screenshot_sha256 is not None
    assert result.trace_sha256 is not None
