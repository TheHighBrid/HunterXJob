"""Lock offline answer-matching coverage across sanitized ATS fixtures."""
from __future__ import annotations

import json
from pathlib import Path

from app.fixture_answer_coverage import main, report

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "reports" / "fixture_answer_coverage.json"


def test_fixture_answer_coverage_totals_match_snapshot():
    """Example profile + policy answers give stable fill/review/skip across fixtures."""
    totals = report()["totals"]
    assert totals == {
        "forms": 6, "controls": 132, "fill": 63, "review": 36, "skip": 33, "planned_pct": 47.7,
    }
    assert SNAPSHOT.is_file(), "commit reports/fixture_answer_coverage.json after regenerating"
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8"))["totals"] == totals


def test_fixture_answer_coverage_main_writes_json(tmp_path, capsys):
    """The CLI entry prints and writes the same report the module returns."""
    out = tmp_path / "coverage.json"
    assert main(out) == 0
    expected = report()
    assert json.loads(out.read_text(encoding="utf-8")) == expected
    assert json.loads(capsys.readouterr().out) == expected
