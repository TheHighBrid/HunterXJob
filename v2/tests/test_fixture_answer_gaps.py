"""Lock the gap breakdown of review/skip controls from the offline fixture run."""
from __future__ import annotations

import json
from pathlib import Path

from app.fixture_answer_coverage import report as coverage_report
from app.fixture_answer_gaps import main, report

SNAPSHOT = Path(__file__).resolve().parents[1] / "reports" / "fixture_answer_gaps.json"


def _counts(rows):
    return {row["name"]: (row["review"], row["skip"]) for row in rows}


def test_gap_totals_match_coverage_report():
    coverage = coverage_report()["totals"]
    gaps = report()
    expected = coverage["review"] + coverage["skip"]
    assert gaps["totals"] == {"review": coverage["review"], "skip": coverage["skip"]}
    assert sum(row["total"] for row in gaps["by_category"]) == expected
    assert sum(row["total"] for row in gaps["by_control_type"]) == expected


def test_gap_categories_are_locked():
    assert _counts(report()["by_category"]) == {
        "documents": (7, 9),
        "profile_names_links": (1, 13),
        "role_screening": (8, 3),
        "free_text_essay": (5, 4),
        "legal_consent": (7, 1),
        "prior_employment": (1, 0),
        "sensitive_self_id": (4, 1),
        "option_mismatch": (2, 0),
        "referral": (0, 2),
        "work_authorization": (1, 0),
    }


def test_snapshot_and_cli_agree(tmp_path, capsys):
    out = tmp_path / "gaps.json"
    assert main(out) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == report()
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == data
    assert json.loads(capsys.readouterr().out)["by_category"] == data["by_category"]
