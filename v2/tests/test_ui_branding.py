"""Cosmetic UI headers and timeline labels must show HunterXJob, not JobTomatik."""

from __future__ import annotations

from app.dashboard_template import get_dashboard_html


def test_dashboard_headers_use_hunterxjob_branding() -> None:
    html = get_dashboard_html()
    assert "<title>HunterXJob v2 — Autonomous Operations Center</title>" in html
    assert "HUNTERXJOB ENGINE" in html
    assert "JOBTOMATIK ENGINE" not in html
    assert "JobTomatik" not in html
    assert "JOBTOMATIK" not in html
    assert "HunterXJob" in html

