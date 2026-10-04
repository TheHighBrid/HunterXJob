"""Gate 1 negative proofs with a real Playwright-owned Chromium (``pytest -m gate1``).

Each case runs ``scripts/gate1.py`` for one independent run against a fixture
that misbehaves, and asserts the gate fails for the right reason. Deselected
from the default suite (it needs ``playwright install chromium``); the
``gate1`` CI job runs it.
"""
import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gate1
V2 = Path(__file__).resolve().parents[1]


def _gate(tmp_path: Path, *args: str) -> tuple[int, dict]:
    command = [sys.executable, str(V2 / "scripts" / "gate1.py"), "--runs", "1", "--out", str(tmp_path), *args]
    options = {"cwd": V2, "capture_output": True, "text": True, "timeout": 600, "check": False, "stdin": subprocess.DEVNULL}
    proc = subprocess.run(command, **options)  # noqa: S603  # nosec B603
    report = json.loads((tmp_path / "gate1-report.json").read_text(encoding="utf-8"))
    return proc.returncode, report


def _failed(report: dict) -> set[str]:
    return set(report["summary"][0]["failed_checks"])


def _problems(report: dict) -> str:
    return "\n".join(report["runs"][0]["checks"]["zero_submit_and_non_get"]["detail"]["problems"])


def test_post_attempt_fails_the_gate(tmp_path):
    code, report = _gate(tmp_path, "--variant", "post_beacon")
    assert code == 1 and report["verdict"] == "fail"
    assert _failed(report) == {"zero_submit_and_non_get"}
    assert "non-GET browser request POST" in _problems(report)


@pytest.mark.parametrize("variant", ["auto_submit_post", "auto_submit_get"])
def test_auto_submit_fails_the_gate_and_the_dry_run_fails_closed(tmp_path, variant):
    code, report = _gate(tmp_path, "--variant", variant)
    assert code == 1 and report["verdict"] == "fail"
    failed = _failed(report)
    assert {"zero_submit_and_non_get", "api_completed_cleanly", "evidence_persisted_in_ledger"} <= failed
    api = report["runs"][0]["checks"]["api_completed_cleanly"]["detail"]
    assert api["status"] == "needs_review" and api["submitted"] is False
    assert "not trustworthy" in api["detail"]
    # The production path still left no browser behind.
    assert "browser_and_children_gone_after_shutdown" not in failed
    assert report["summary"][0]["leftover_processes"] == 0


def test_process_leak_fails_the_gate(tmp_path):
    code, report = _gate(tmp_path, "--inject-leak")
    assert code == 1 and report["verdict"] == "fail"
    assert _failed(report) == {"browser_and_children_gone_after_shutdown"}
    assert report["summary"][0]["leftover_processes"] >= 1
