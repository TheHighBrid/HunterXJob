#!/usr/bin/env python
"""Run the Greenhouse live certification gate without submitting anything.

The gate uses the real verified HunterXJob profile and stored answer policies,
fetches live public Greenhouse forms, renders every selected form in Chromium,
and writes a redacted JSON report. Browser traffic is read-only through the
existing Greenhouse browser verifier. A review stop is a valid certification
outcome when the rendered form was inspected and the blocker was surfaced.

Progress is checkpointed after every posting so Android/PRoot process death does
not discard completed browser-verified runs. A checkpoint is resumed only when
the certification parameters, verified profile/answer policies, and relevant
certification code still match the original run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.answer_vault import AnswerRecord, AnswerSource
from app.certification import DEFAULT_MIN_BOARDS, DEFAULT_REQUIRED_RUNS, evaluate_results, select_round_robin
from app.config import Settings
from app.db import SessionLocal
from app.discovery import SOURCE_FAILURES, JobRecord, greenhouse_jobs
from app.form_engine import plan_fill
from app.greenhouse_browser import inspect_hosted_form, reconcile
from app.greenhouse_form import FormFetchError, GreenhouseJobRef, fetch_greenhouse_form
from app.models import AnswerPolicy
from app.profile import list_facts, readiness, verified_profile
from app.vault_store import load_vault

CHECKPOINT_SCHEMA = 1
CHECKPOINT_KIND = "greenhouse_browser_verified_dry_run_checkpoint"
DEFAULT_CHECKPOINT = Path("data/certification/greenhouse-in-progress.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_REQUIRED_RUNS, help="required browser-verified plans")
    parser.add_argument("--min-boards", type=int, default=DEFAULT_MIN_BOARDS, help="minimum distinct employer boards")
    parser.add_argument("--boards", help="comma-separated Greenhouse board tokens; defaults to .env")
    parser.add_argument("--output", type=Path, help="report path; defaults under data/certification")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT, help="durable in-progress checkpoint path")
    parser.add_argument("--restart", action="store_true", help="discard an existing checkpoint and start a fresh sample")
    parser.add_argument("--browser-timeout", type=float, help="seconds per rendered form; defaults to .env")
    return parser.parse_args()


def _boards(args: argparse.Namespace, settings: Settings) -> list[str]:
    if args.boards:
        values = [item.strip() for item in args.boards.split(",") if item.strip()]
    else:
        values = settings.greenhouse_board_list
    return list(dict.fromkeys(values))


def _state_fingerprint(facts: list[Any], policies: list[AnswerPolicy]) -> str:
    payload = {
        "facts": [
            {
                "key": fact.key,
                "category": fact.category,
                "data_json": fact.data_json,
                "verified": fact.verified,
                "source": fact.source,
                "provenance": fact.provenance,
            }
            for fact in sorted(facts, key=lambda item: item.key)
        ],
        "policies": [
            {
                "key": row.key,
                "scope": row.scope,
                "value": row.value,
                "source": row.source,
                "confidence": row.confidence,
                "sensitive": row.sensitive,
                "note": row.note,
            }
            for row in sorted(policies, key=lambda item: (item.scope, item.key))
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_state() -> tuple[list[str], int, int, str]:
    with SessionLocal() as db:
        facts = list_facts(db)
        policies = list(db.execute(select(AnswerPolicy)).scalars())
        missing = readiness(verified_profile(db))
    verified = sum(1 for fact in facts if fact.verified)
    return missing, verified, len(facts) - verified, _state_fingerprint(facts, policies)


def _code_revision() -> str:
    """Fingerprint code that can change certification interpretation or evidence."""
    v2_root = Path(__file__).resolve().parents[1]
    relevant_paths = (
        Path(__file__).resolve(),
        v2_root / "app" / "answer_vault.py",
        v2_root / "app" / "certification.py",
        v2_root / "app" / "form_engine.py",
        v2_root / "app" / "greenhouse_browser.py",
        v2_root / "app" / "greenhouse_form.py",
        v2_root / "app" / "profile.py",
        v2_root / "app" / "vault_store.py",
    )
    digest = hashlib.sha256()
    for path in relevant_paths:
        relative = path.relative_to(v2_root)
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "files:" + digest.hexdigest()


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


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _posting_key(record: JobRecord) -> str:
    return f"{record.board.casefold()}:{record.external_id}"


def _result_key(result: dict[str, object]) -> str:
    board = str(result.get("board") or "").casefold()
    job_id = result.get("job_id") or ""
    return f"{board}:{job_id!s}"


def _checkpoint_config(
    *,
    boards: list[str],
    count: int,
    min_boards: int,
    profile_fingerprint: str,
    code_revision: str,
) -> dict[str, object]:
    return {
        "boards": sorted(board.casefold() for board in boards),
        "count": count,
        "min_boards": min_boards,
        "profile_fingerprint": profile_fingerprint,
        "code_revision": code_revision,
    }


def _load_checkpoint(
    path: Path,
    *,
    expected_config: dict[str, object],
) -> tuple[list[str], list[dict[str, object]]] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"checkpoint is unreadable: {exc}; rerun with --restart") from exc
    if not isinstance(payload, dict) or payload.get("schema") != CHECKPOINT_SCHEMA or payload.get("kind") != CHECKPOINT_KIND:
        raise RuntimeError("checkpoint format is not recognized; rerun with --restart")
    if payload.get("config") != expected_config:
        raise RuntimeError(
            "checkpoint does not match the current profile, answer policies, code, or gate settings; rerun with --restart"
        )

    raw_selection = payload.get("selection")
    raw_results = payload.get("results")
    if not isinstance(raw_selection, list) or not all(isinstance(item, str) for item in raw_selection):
        raise RuntimeError("checkpoint selection is invalid; rerun with --restart")
    if not isinstance(raw_results, list) or not all(isinstance(item, dict) for item in raw_results):
        raise RuntimeError("checkpoint results are invalid; rerun with --restart")

    selection = list(raw_selection)
    allowed = set(selection)
    by_key: dict[str, dict[str, object]] = {}
    for item in raw_results:
        result = dict(item)
        key = _result_key(result)
        if key not in allowed:
            raise RuntimeError("checkpoint contains a result outside its saved selection; rerun with --restart")
        if key in by_key:
            raise RuntimeError("checkpoint contains duplicate posting results; rerun with --restart")
        by_key[key] = result
    results = [by_key[key] for key in selection if key in by_key]
    return selection, results


def _resume_selection(records_by_board: dict[str, list[JobRecord]], saved_keys: list[str]) -> list[JobRecord]:
    available = {_posting_key(record): record for records in records_by_board.values() for record in records}
    missing = [key for key in saved_keys if key not in available]
    if missing:
        preview = ", ".join(missing[:3])
        suffix = "..." if len(missing) > 3 else ""
        raise RuntimeError(
            f"saved certification postings are no longer discoverable ({preview}{suffix}); rerun with --restart"
        )
    return [available[key] for key in saved_keys]


def _checkpoint_payload(
    *,
    config: dict[str, object],
    selection: list[str],
    results: list[dict[str, object]],
    discovery_errors: list[str],
    profile: dict[str, object],
) -> dict[str, object]:
    return {
        "schema": CHECKPOINT_SCHEMA,
        "kind": CHECKPOINT_KIND,
        "updated_at": datetime.now(UTC).isoformat(),
        "config": config,
        "selection": selection,
        "profile": profile,
        "discovery_errors": discovery_errors,
        "results": results,
    }


def _write_report(
    path: Path,
    *,
    summary: object,
    results: list[dict[str, object]],
    discovery_errors: list[str],
    profile: dict[str, object],
) -> None:
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
    _atomic_json_write(path, payload)


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

    missing, verified_count, unverified_count, state_fingerprint = _profile_state()
    profile = {
        "ready": not missing,
        "missing": missing,
        "verified_facts": verified_count,
        "unverified_facts": unverified_count,
        "state_fingerprint": state_fingerprint,
    }
    if missing:
        print("Verified profile is not ready: " + "; ".join(missing), file=sys.stderr)
        return 2

    try:
        _browser_preflight()
    except RuntimeError as exc:
        print(f"Browser preflight failed: {exc}", file=sys.stderr)
        return 2

    checkpoint = args.checkpoint
    if args.restart:
        checkpoint.unlink(missing_ok=True)
        checkpoint.with_name(checkpoint.name + ".tmp").unlink(missing_ok=True)

    config = _checkpoint_config(
        boards=boards,
        count=args.count,
        min_boards=args.min_boards,
        profile_fingerprint=state_fingerprint,
        code_revision=_code_revision(),
    )

    records_by_board, discovery_errors = _discover(boards)
    try:
        saved = _load_checkpoint(checkpoint, expected_config=config)
    except RuntimeError as exc:
        print(f"Checkpoint error: {exc}", file=sys.stderr)
        return 2

    if saved is None:
        selected = select_round_robin(records_by_board, args.count)
        results: list[dict[str, object]] = []
        selection_keys = [_posting_key(record) for record in selected]
    else:
        selection_keys, results = saved
        try:
            selected = _resume_selection(records_by_board, selection_keys)
        except RuntimeError as exc:
            print(f"Checkpoint error: {exc}", file=sys.stderr)
            return 2
        print(f"Resuming checkpoint: {checkpoint} ({len(results)}/{args.count} completed)")

    if len(selected) < args.count:
        print(f"Only {len(selected)} live postings were discovered; certification requires {args.count}.", file=sys.stderr)
        return 2
    if len(selection_keys) != args.count:
        print("Checkpoint selection size does not match the requested certification count; rerun with --restart.", file=sys.stderr)
        return 2

    _atomic_json_write(
        checkpoint,
        _checkpoint_payload(
            config=config,
            selection=selection_keys,
            results=results,
            discovery_errors=discovery_errors,
            profile=profile,
        ),
    )

    timeout = args.browser_timeout if args.browser_timeout is not None else settings.greenhouse_browser_timeout
    timeout_ms = max(1, int(timeout * 1000))
    completed = {_result_key(result) for result in results}
    for index, record in enumerate(selected, start=1):
        key = _posting_key(record)
        if key in completed:
            print(f"[{index:02d}/{args.count}] {key} checkpointed")
            continue
        result = _run_posting(record, timeout_ms)
        results.append(result)
        completed.add(key)
        _atomic_json_write(
            checkpoint,
            _checkpoint_payload(
                config=config,
                selection=selection_keys,
                results=results,
                discovery_errors=discovery_errors,
                profile=profile,
            ),
        )
        print(
            f"[{index:02d}/{args.count}] {result['board']}:{result['job_id']} "
            f"{result['status']} dom_verified={result.get('dom_verified', False)} checkpointed"
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
    if summary.passed:
        checkpoint.unlink(missing_ok=True)
        print("Checkpoint cleared after PASS.")
    else:
        print(f"Checkpoint retained for inspection/resume: {checkpoint}")
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
