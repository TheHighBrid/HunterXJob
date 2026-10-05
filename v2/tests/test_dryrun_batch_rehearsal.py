"""Dry-run batch rehearsal with a real Playwright-owned Chromium (``pytest -m gate1``).

Runs ``scripts/dryrun_batch.py --rehearse`` end to end on Gate 1's loopback fixtures with the
made-up example profile: profile import, owner answers, materials generated and the résumé
approved in the throwaway run database, the production dry-run route, and every batch check.
No real site is contacted (``--live`` refuses to start under CI).
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gate1
V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
batch = importlib.import_module("dryrun_batch")


def test_batch_rehearsal_passes_every_check_with_backed_answers_only(tmp_path):
    assert batch.main(["--rehearse", "--out", str(tmp_path), "--pause", "0"]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["aggregate"]["runs"] == 1 and summary["aggregate"]["safety_pass_rate"] == 1.0
    assert summary["aggregate"]["unbacked_answers_total"] == 0
    report = json.loads((tmp_path / "run-01-report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "pass" and report["failed_checks"] == []
    assert set(report["request_counts"]["by_method"]) == {"GET"}
    planned = report["field_mapping"]["planned"]
    assert planned["first_name"] == "Avery" and planned["resume"].endswith(".pdf")  # approved résumé, never uploaded
    kinds = [row["kind"] for row in report["checks"]["evidence_persisted_in_ledger"]["detail"]["rows"]]
    assert kinds.count("dry_run_review") == 1 and "submission" not in kinds
    assert len(report["trace_sha256"]) == 64 and len(report["screenshot_sha256"]) == 64
    # Re-evaluating from the saved observations gives the same verdict with no browser and no network.
    assert batch.main(["--recheck", "--out", str(tmp_path)]) == 0
    again = json.loads((tmp_path / "run-01-report.json").read_text(encoding="utf-8"))
    assert again["outcome"] == "pass" and again["rechecked"]["previous_outcome"] == "pass"
