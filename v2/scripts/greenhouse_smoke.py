#!/usr/bin/env python
"""Read-only smoke dry-run against live public Greenhouse postings.

Fetches each posting's real application form (public boards API, optionally
verified in a read-only headless browser), plans it against a throwaway
in-memory answer vault, and prints what would fill and what would block.

It never submits, uploads, or POSTs anything, and it does not touch the
HunterXJob database.

Usage:
    .venv/bin/python scripts/greenhouse_smoke.py asana:8165477 https://job-boards.greenhouse.io/d2l/jobs/7696196
    .venv/bin/python scripts/greenhouse_smoke.py --browser-verify --answers my_answers.json gitlab:8556658002
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.answer_vault import AnswerRecord, AnswerSource, AnswerVault
from app.form_engine import plan_fill
from app.greenhouse_browser import inspect_hosted_form, reconcile
from app.greenhouse_form import (
    FormFetchError,
    GreenhouseJobRef,
    fetch_greenhouse_form,
    parse_greenhouse_ref,
)

# Obviously fake identity so the plan can show which *non-identity* fields block.
DEFAULT_ANSWERS = {
    "first_name": "Test",
    "last_name": "Candidate",
    "email": "candidate@example.test",
    "phone": "555-0100",
    "resume": "~/hunterx/resume.pdf",
}


def _ref(arg: str) -> GreenhouseJobRef:
    if ":" in arg and not arg.startswith("http"):
        token, job_id = arg.split(":", 1)
        return GreenhouseJobRef(token, job_id)
    ref = parse_greenhouse_ref(arg)
    if ref is None:
        raise SystemExit(f"cannot identify a Greenhouse board/job in {arg!r}; use token:job_id")
    return ref


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("postings", nargs="+", help="token:job_id or a Greenhouse posting URL")
    parser.add_argument("--browser-verify", action="store_true", help="also load the embed page read-only and reconcile")
    parser.add_argument("--answers", type=Path, help="JSON object of extra vault answers (treated as user-authored)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args()


def _vault(answers_path: Path | None) -> AnswerVault:
    answers = dict(DEFAULT_ANSWERS)
    if answers_path:
        answers.update(json.loads(answers_path.read_text(encoding="utf-8")))
    return AnswerVault([AnswerRecord(key, value, AnswerSource.USER) for key, value in answers.items()])


def _dry_run(arg: str, vault: AnswerVault, browser_verify: bool) -> dict[str, object]:
    ref = _ref(arg)
    entry: dict[str, object] = {"posting": f"{ref.board}:{ref.job_id}"}
    try:
        form = fetch_greenhouse_form(ref)
        if browser_verify:
            form = reconcile(form, inspect_hosted_form(ref))
    except FormFetchError as exc:
        entry.update(status="flagged", reason=exc.reason_code, detail=exc.detail)
        return entry
    plan = plan_fill(form.controls, vault)
    summary = form.summary()
    entry.update(
        status="dry_run_ready" if plan.ready and not form.handoff else "needs_review",
        title=form.title,
        location=form.metadata.get("job_location"),
        form_source=form.source,
        fields_total=summary["fields_total"],
        fields_required=summary["fields_required"],
        sections=summary["sections"],
        types=dict(Counter(control.control_type.value for control in form.controls)),
        blockers=plan.blockers,
        would_fill=[item.control.key for item in plan.items if item.status == "fill"],
        blocked_fields=[
            {"key": item.control.key, "section": item.control.section, "required": item.control.required,
             "label": item.control.label[:90], "reason": item.reason}
            for item in plan.items if item.status == "review"
        ],
        warnings=form.warnings,
        submit_boundary=form.metadata.get("submit_boundary"),
        dom_only_required_fields=form.metadata.get("dom_only_required_fields"),
    )
    return entry


def _print_entry(entry: dict[str, object]) -> None:
    print(f"\n=== {entry['posting']}: {entry.get('title', '')} [{entry.get('location', '')}]")
    if entry["status"] == "flagged":
        print(f"status: flagged ({entry.get('reason')}: {entry.get('detail')})")
        return
    print(f"status: {entry['status']}")
    print(f"source: {entry['form_source']}  fields: {entry['fields_total']} ({entry['fields_required']} required)  sections: {entry['sections']}")
    print(f"types: {entry['types']}")
    print(f"would fill: {', '.join(entry['would_fill'])}")
    print(f"blockers: {', '.join(entry['blockers']) or 'none'}")
    for field in entry["blocked_fields"]:
        print(f"  - [{field['section']}] {'REQ' if field['required'] else 'opt'} {field['key']}: {field['label']} -> {field['reason']}")
    for warning in entry["warnings"]:
        print(f"  ! {warning}")


def main() -> int:
    args = _parse_args()
    vault = _vault(args.answers)
    results = [_dry_run(arg, vault, args.browser_verify) for arg in args.postings]
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    for entry in results:
        _print_entry(entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
