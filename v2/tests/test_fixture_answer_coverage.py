"""Lock offline answer-matching coverage across sanitized ATS fixtures."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.fixture_answer_coverage import report

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fixture_answer_coverage.py"
SNAPSHOT = ROOT / "reports" / "fixture_answer_coverage.json"


def test_fixture_answer_coverage_totals_match_snapshot():
    """Example profile + policy answers → stable fill/review/skip across fixtures."""
    totals = report()["totals"]
    assert totals["forms"] == 6
    assert totals["controls"] == 132
    assert totals["fill"] == 58
    assert totals["review"] == 41
    assert totals["skip"] == 33
    assert totals["planned_pct"] == 43.9
    assert SNAPSHOT.is_file(), "commit reports/fixture_answer_coverage.json after regenerating"
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8"))["totals"] == totals


def test_fixture_answer_coverage_script_writes_json():
    """CLI stays offline and prints the same totals the module reports."""
    expected = report()["totals"]
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    printed = json.loads(result.stdout)
    assert printed["totals"] == expected
