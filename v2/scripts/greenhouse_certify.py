#!/usr/bin/env python
"""Run the Greenhouse live certification gate without submitting anything.

The gate uses the real verified HunterXJob profile and stored answer policies,
fetches live public Greenhouse forms, renders every selected form in Chromium,
and writes a redacted JSON report. Browser traffic is read-only through the
existing Greenhouse browser verifier. A review stop is a valid certification
outcome when the rendered form was inspected and the blocker was surfaced.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.answer_vault import AnswerRecord, AnswerSource
from app.certification import DEFAULT_MIN_BOARDS, DEFAULT_REQUIRED_RUNS, evaluate_results, select_round_robin
from app.config import Settings
from app.db import SessionLocal
from app.discovery import SOURCE_FAILURES, JobRecord, greenhouse_jobs
from app.form_engine import plan_fill
from app.greenhouse_browser import inspect_hosted_form, reconcile
from app.greenhouse_form import FormFetchError, GreenhouseJobRef, fetch_greenhouse_form
from app.profile import list_facts, readiness, verified_profile
from app.vault_store import load_vault


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_REQUIRED_RUNS, help="required browser-verified plans")
    parser.add_argument("--min-boards", type=int, default=DEFAULT_MIN_BOARDS, help="minimum distinct employer boards")
    parser.add_argument("--boards", help="comma-separated Greenhouse board tokens; defaults to .env")
    parser.add_argument("--output", type=Path, help="report path; defaults under data/certification")
    parser.add_argument("--browser-timeout", type=float, help="seconds per rendered form; defaults to .env")
    return parser.parse_args()


def _boards(args: argparse.Namespace, settings: Settings) -> list[str]:
    if args.boards:
        values = [item.strip() for item in args.boards.split(",") if item.strip()]
    else:
        values = settings.greenhouse_board_list
    return list(dict.fromkeys(values))


def _profile_state() -> tuple[list[str], int, int]:
    with SessionLocal() as db:
        facts = list_facts(db)
        missing = readiness(verified_profile(db))
    verified = sum(1 for fact in facts if fact.verified)
    return missing, verified, len(facts) - verified


def _browser_preflight() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed; run ./hunterx prepare-certification") from exc

    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.exists():
            raise RuntimeError("Playwright Chromium is not installed; run ./hunterx prepare-certification")
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:
            raise RuntimeError(f"Chromium exists but cannot launch: {exc}") from exc
        browser.close()


def _discover(boards: list[str]) -> tuple[dict[str, list[JobRecord]], list[str]]:
    records: dict[str, list[JobRecord]] = {}
    errors: list[str] = []
    for board in boards:
        try:
            found = greenhouse_jobs(board)
        except SOURCE_FAILURES as exc:
            errors.append(f"greenhouse:{board}: {type(exc).__name__}: {exc}")
            continue
        if found:
            records[board.lower()] = found
    return records, errors


def _run_posting(record: JobRecord, timeout_ms: int) -> dict[str, object]:
    ref = GreenhouseJobRef(record.board, record.external_id)
    result: dict[str, object] = {
        "board": ref.board,
        "job_id": ref.job_id,
        "title": record.title,
        "location": record.location,
    }
    try:
        form = fetch_greenhouse_form(ref)
        form = reconcile(form, inspect_hosted_form(ref, timeout_ms=timeout_ms))
    except FormFetchError as exc:
        result.update(status="flagged", reason=exc.reason_code, detail=exc.detail, dom_verified=False)
        return result

    with SessionLocal() as db:
        vault = load_vault(db, ref.vault_scopes)
    # Certification never uploads. These synthetic file references only let the
    # planner prove it recognized the document controls without exposing paths.
    vault.put(AnswerRecord("resume", "certification://approved-resume", AnswerSource.USER))
    vault.put(AnswerRecord("cover_letter", "certification://approved-cover-letter", AnswerSource.USER))
    plan = plan_fill(form.controls, vault)
    summary = form.summary()
    result.update(
        status="dry_run_ready" if plan.ready and not form.handoff else "needs_review",
        form_source=form.source,
        dom_verified=form.metadata.get("dom_verified") is True,
        fields_total=summary["fields_total"],
        fields_required=summary["fields_required"],
        sections=summary["sections"],
        types=dict(Counter(control.control_type.value for control in form.controls)),
        blockers=plan.blockers,
        blocked_fields=[
            {
                "key": item.control.key,
                "section": item.control.section,
                "required": item.control.required,
                "label": item.control.label[:120],
                "reason": item.reason,
            }
            for item in plan.items
            if item.status == "review"
        ],
        warnings=form.warnings,
        submit_boundary=form.metadata.get("submit_boundary"),
        dom_only_required_fields=form.metadata.get("dom_only_required_fields") or [],
    )
    return result


def _report_path(requested: Path | None) -> Path:
    if requested is not None:
        return requested
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("data/certification") / f"greenhouse-{stamp}.json"


def _write_report(
    path: Path,
    *,
    summary: object,
    results: list[dict[str, object]],
    discovery_errors: list[str],
    profile: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "kind": "greenhouse_browser_verified_dry_run_certification",
        "generated_at": datetime.now(UTC).isoformat(),
        "safety": {
            "submission": "disabled",
            "browser_interaction": "read_only",
            "network_mutations": "blocked",
        },
        "profile": profile,
        "summary": summary.as_dict(),
        "discovery_errors": discovery_errors,
        "results": results,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    if args.count < 1 or args.min_boards < 1:
        print("count and min-boards must both be at least 1", file=sys.stderr)
        return 2

    settings = Settings()
    boards = _boards(args, settings)
    if len(boards) < args.min_boards:
        print(
            f"Need at least {args.min_boards} configured Greenhouse boards; found {len(boards)}. "
            "Run ./hunterx prepare-certification or set GREENHOUSE_BOARD_TOKENS.",
            file=sys.stderr,
        )
        return 2

    missing, verified_count, unverified_count = _profile_state()
    profile = {
        "ready": not missing,
        "missing": missing,
        "verified_facts": verified_count,
        "unverified_facts": unverified_count,
    }
    if missing:
        print("Verified profile is not ready: " + "; ".join(missing), file=sys.stderr)
        return 2

    try:
        _browser_preflight()
    except RuntimeError as exc:
        print(f"Browser preflight failed: {exc}", file=sys.stderr)
        return 2

    records_by_board, discovery_errors = _discover(boards)
    selected = select_round_robin(records_by_board, args.count)
    if len(selected) < args.count:
        print(f"Only {len(selected)} live postings were discovered; certification requires {args.count}.", file=sys.stderr)
        return 2

    timeout = args.browser_timeout if args.browser_timeout is not None else settings.greenhouse_browser_timeout
    timeout_ms = max(1, int(timeout * 1000))
    results: list[dict[str, object]] = []
    for index, record in enumerate(selected, start=1):
        result = _run_posting(record, timeout_ms)
        results.append(result)
        print(
            f"[{index:02d}/{args.count}] {result['board']}:{result['job_id']} "
            f"{result['status']} dom_verified={result.get('dom_verified', False)}"
        )

    summary = evaluate_results(results, required_runs=args.count, min_boards=args.min_boards)
    output = _report_path(args.output)
    _write_report(output, summary=summary, results=results, discovery_errors=discovery_errors, profile=profile)
    print(f"Report: {output}")
    print(
        f"Gate: {'PASS' if summary.passed else 'FAIL'} | browser-verified "
        f"{summary.browser_verified_runs}/{summary.required_runs} | boards {summary.distinct_boards}/{summary.min_boards}"
    )
    if summary.reasons:
        print("Reasons: " + "; ".join(summary.reasons))
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
