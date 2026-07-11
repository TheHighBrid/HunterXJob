"""Render structured resume JSON (and cover letters) to PDF via Playwright.

Uses a Jinja2 HTML template (app/templates/resume.html.j2 or
cover_letter.html.j2) and Chromium's print-to-PDF (page.pdf()) — no LaTeX
toolchain needed.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


def render_resume_html(resume_json: dict[str, Any]) -> str:
    template = _env.get_template("resume.html.j2")
    return template.render(resume=resume_json)


def render_cover_letter_html(profile: dict[str, Any], cover_letter_text: str) -> str:
    template = _env.get_template("cover_letter.html.j2")
    return template.render(
        profile=profile,
        cover_letter_text=cover_letter_text,
        today=dt.date.today().strftime("%B %d, %Y"),
    )


def _html_to_pdf(html: str, output_path: str) -> str:
    """Render `html` to a PDF file at `output_path` using headless Chromium.

    Raises RuntimeError with a clear message if Playwright's Chromium
    browser isn't installed (e.g. `playwright install chromium` wasn't run).
    """
    from playwright.sync_api import sync_playwright

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.pdf(path=output_path, format="Letter", print_background=True)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - re-raise with actionable context
        raise RuntimeError(
            "Failed to render PDF via Playwright Chromium. If this is a fresh "
            "install, run `python -m playwright install chromium` first. "
            f"Original error: {exc}"
        ) from exc

    return output_path


def render_resume_pdf(resume_json: dict[str, Any], output_path: str) -> str:
    """Render `resume_json` to an ATS-friendly PDF at `output_path`. Returns the path."""
    html = render_resume_html(resume_json)
    return _html_to_pdf(html, output_path)


def render_cover_letter_pdf(
    profile: dict[str, Any], cover_letter_text: str, output_path: str
) -> str:
    """Render a cover letter to PDF at `output_path`. Returns the path."""
    html = render_cover_letter_html(profile, cover_letter_text)
    return _html_to_pdf(html, output_path)
