"""Gate 2 runner rehearsal with a real Playwright-owned Chromium (``pytest -m gate1``).

Runs ``scripts/gate2.py --rehearse`` end to end against Gate 1's loopback fixtures,
so CI exercises the exact runner used for the real posting without ever
contacting a real site (``--live`` refuses to start under CI).
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gate1
V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
runner = importlib.import_module("gate2")


def test_rehearsal_passes_with_screenshot_review_ledger_row_and_get_only_traffic(tmp_path):
    assert runner.main(["--rehearse", "--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "gate2-report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "pass" and report["failed_checks"] == [] and report["challenge"] is None
    assert not report["proven"]["one_real_public_greenhouse_posting_dry_run"]  # a rehearsal never proves Gate 2
    assert set(report["request_counts"]["by_method"]) == {"GET"}
    shot = report["run"]["checks"]["screenshot_saved_and_hashed"]["detail"]
    assert Path(shot["path"]).is_file() and len(shot["sha256"]) == 64
    rows = report["run"]["checks"]["evidence_persisted_in_ledger"]["detail"]["rows"]
    assert [row["kind"] for row in rows] == ["dry_run_review"]
    assert report["field_mapping"]["planned"] == {"first_name": "Test", "last_name": "Applicant",
                                                  "email": "test@example.com", "phone": "555-0100"}
    log = report["run"]["fixture_server_log"]
    assert log and all(entry["method"] == "GET" for entry in log)
    # Re-evaluating from the saved observations gives the same verdict with no browser and no network.
    assert runner.main(["--recheck", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "gate2-report.first-evaluation.json").is_file()
