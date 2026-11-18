"""Offline regression checks for the owner-mandated native submission path."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "v2" / "app"
FORBIDDEN_SERVICES = frozenset({
    "supervised_submission",
    "supervised_submission_integration",
    "operator_assisted_submission",
    "operator_assisted_handoff_integration",
    "application_target_handoff",
    "submission_handoff",
    "browser_handoff",
    "form_filler_handoff",
    "handoff_session",
    "handoff_recovery",
    "handoff_integration",
    "handoff_confirmation_target",
    "handoff_notifications",
    "handoff_safety_integration",
    "submission_integrity",
    "submission_integrity_integration",
    "submission_evidence_review",
    "platform_submission_evidence",
    "evidence_ledger",
})
DONOR_NAME = re.compile(r"job[\s_-]*tomatik", re.IGNORECASE)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _forbidden_module(name: str) -> bool:
    return bool(DONOR_NAME.search(name)) or bool(
        FORBIDDEN_SERVICES.intersection(name.split("."))
    )


def test_no_external_submission_services():
    violations = []
    for root in (APP, ROOT / "backend" / "app"):
        for path in sorted(root.rglob("*.py")):
            if _forbidden_module(path.stem):
                violations.append(str(path.relative_to(ROOT)))
            for node in ast.walk(_parse(path)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or "", *(alias.name for alias in node.names)]
                elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                    name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                    if name in {"import_module", "__import__"} and node.args:
                        arg = node.args[0]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            names = [arg.value]
                for name in names:
                    if _forbidden_module(name):
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
    assert not violations, "External submission services: " + ", ".join(violations)


def test_autonomous_loop_and_browser_filler_use_native_submission_engine():
    for filename in ("autonomous_loop.py", "browser_filler.py"):
        tree = _parse(APP / filename)
        assert any(
            isinstance(node, ast.ImportFrom)
            and node.module == "app.submission_engine"
            and any(alias.name == "execute_live_submission" for alias in node.names)
            for node in ast.walk(tree)
        ), f"{filename} must use HunterXJob's native submission engine"


def test_native_submission_engine_owns_submit_execution():
    tree = _parse(APP / "submission_engine.py")
    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "execute_live_submission"
        for node in tree.body
    )


def test_guard_distinguishes_rejected_services_from_native_modules():
    assert _forbidden_module("app.services.supervised_submission")
    assert _forbidden_module("JobTomatik.backend.app")
    assert _forbidden_module("handoff_session")
    assert not _forbidden_module("app.submission_engine")
    assert not _forbidden_module("app.human_intervention")
    assert not _forbidden_module("app.evidence")
