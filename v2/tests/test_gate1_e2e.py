"""Gate 1 negative proofs with a real Playwright-owned Chromium (``pytest -m gate1``).

Each case runs ``scripts/gate1.py`` (its ``main``) for one independent run against a fixture
that misbehaves, and asserts the gate fails for the right reason. Deselected
from the default suite (it needs ``playwright install chromium``); the
``gate1`` CI job runs it.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gate1
V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "scripts"))
runner = importlib.import_module("gate1")


def _gate(tmp_path: Path, *args: str) -> tuple[int, dict]:
    code = runner.main(["--runs", "1", "--out", str(tmp_path), *args])
    report = json.loads((tmp_path / "gate1-report.json").read_text(encoding="utf-8"))
    return code, report


def _failed(report: dict) -> set[str]:
    return set(report["summary"][0]["failed_checks"])


def _problems(report: dict) -> str:
    return "\n".join(report["runs"][0]["checks"]["zero_submit_and_non_get"]["detail"]["problems"])


def _server_requests(report: dict) -> list[tuple[str, str, bool]]:
    log = report["runs"][0]["checks"]["zero_submit_and_non_get"]["detail"]["fixture_server_log"]
    return [(entry["method"], entry["path"], entry["expected"]) for entry in log]


@pytest.mark.parametrize("variant, method", [("post_beacon", "POST"), ("head_request", "HEAD")])
def test_non_get_attempt_fails_the_gate_but_not_the_dry_run(tmp_path, variant, method):
    code, report = _gate(tmp_path, "--variant", variant)
    assert code == 1 and report["verdict"] == "fail"
    assert _failed(report) == {"zero_submit_and_non_get"}
    assert f"non-GET browser request {method}" in _problems(report)
    # Production behaviour: the write was aborted in the browser, so it never reached the server,
    # and the dry-run itself still completes with an informational warning (e.g. analytics beacons).
    assert all(entry[0] == "GET" for entry in _server_requests(report))
    api = report["runs"][0]["checks"]["api_completed_cleanly"]["detail"]
    assert api["status"] == "dry_run_complete" and api["submitted"] is False


@pytest.mark.parametrize("variant, attempts", [("auto_submit_post", 1), ("auto_submit_get", 1), ("iframe_form_submit", 2)])
def test_submit_attempt_fails_the_gate_and_the_dry_run_fails_closed(tmp_path, variant, attempts):
    code, report = _gate(tmp_path, "--variant", variant)
    assert code == 1 and report["verdict"] == "fail"
    failed = _failed(report)
    assert {"zero_submit_and_non_get", "api_completed_cleanly", "evidence_persisted_in_ledger"} <= failed
    api = report["runs"][0]["checks"]["api_completed_cleanly"]["detail"]
    assert api["status"] == "needs_review" and api["submitted"] is False
    assert f"not trustworthy: page attempted {attempts} form submission(s)" in api["detail"]
    # Every attempt (submit event, form.submit() with no event, a form inside an iframe) was
    # refused in the page: the fixture server saw only the three expected GETs.
    assert _server_requests(report) == [("GET", "/v1/boards/d2l/jobs/7696196", True)] * 2 + [("GET", "/embed/job_app", True)]
    # The production path still left no browser behind.
    assert "browser_and_children_gone_after_shutdown" not in failed
    assert "no_process_outlived_the_run" not in failed
    assert report["summary"][0]["leftover_processes"] == 0


def test_process_leak_fails_the_gate(tmp_path):
    code, report = _gate(tmp_path, "--inject-leak")
    assert code == 1 and report["verdict"] == "fail"
    assert _failed(report) == {"browser_and_children_gone_after_shutdown"}
    assert report["summary"][0]["leftover_processes"] >= 1
