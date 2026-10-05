#!/usr/bin/env python3
"""A batch of read-only dry-runs against real public postings, with the owner's real profile.

This is a thin wrapper around the Gate 2 code path (``scripts/gate2.py``), extended to
Lever and Ashby. For each target, sequentially:

1. the parent confirms the posting is open with ONE GET to the platform's public board API
   (one retry on a transport error or 5xx only);
2. a freshly spawned process starts the real v2 app on 127.0.0.1 with the same locked
   dry-run environment as Gates 1 and 2 (``APPLICATION_MODE=dry_run``,
   ``ALLOW_LIVE_SUBMISSION=false``, browser verification on, traces on), imports the owner's
   profile document into a throwaway run database, stores the owner's form-answer defaults,
   seeds the job, generates the résumé/cover letter with the materials pipeline (LLM off),
   approves the résumé and rejects the cover letter (owner instruction for dry-runs; nothing
   is ever uploaded), and calls ``POST /api/applications/{id}/apply``;
3. the production path probes the posting (GET), reads the form (GET), and has Playwright
   launch its own Chromium (never CDP) to load the hosted form once, read-only: only GET
   leaves the browser (Ashby's allowlisted GraphQL *queries* are re-issued as GET; every
   other non-GET, such as an analytics beacon, is aborted and recorded), no typing, no
   clicking, no file attached, form submission refused in the page;
4. the Gate 2 checks run, plus: live submission locked, the inspector has no input APIs,
   every planned answer is backed by a verified profile fact / owner answer / approved
   material (anything else is reported as a bug), and the generated materials contain
   none of the owner's "claims to avoid".

A bot check fails closed (``blocked_by_bot_check``) and is recorded; nothing retries the
page or tries to get past it. A run is retried once only for a transient network error.
A crashed run (no result) stops the batch so the cause can be fixed first.

Real-identity output must live outside the repository (``--out`` inside it is refused),
and ``--live`` refuses to run in CI. CI only runs ``--rehearse`` (loopback fixtures and
``examples/profile.example.yaml``)::

    python scripts/dryrun_batch.py --live --targets T.json --profile P.yaml --answers A.yaml \
        --guardrails G.yaml --out /private/dir --pause 30
    python scripts/dryrun_batch.py --rehearse --out /tmp/batch-rehearsal --pause 0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from batch_checks import (
    DECLINE_REASON,
    PLATFORMS,
    batch_outcome,
    claims_hits,
    is_transient,
    open_check_url,
    parse_open_posting,
    run_extra_checks,
    run_row,
    summarize,
)
from gate1 import SPAWN, _cdp_in_app_code, _drive_api, _ledger, _start_fixtures, _wait_for_cleanup, child_env, supervise
from gate1_checks import API_KEY, BOARD, JOB_ID, V2_ROOT, live_descendants, still_running
from gate2 import _after_exit, _instrument_http, evaluate
from gate2_checks import NOT_PROVEN, NOT_PROVEN_NOTES

REPO_ROOT = V2_ROOT.parent
USER_AGENT = "HunterXJob-dryrun-batch/1.0 (read-only dry-run verification)"
DRY_RUN_APPROVAL_NOTE = ("auto-approved for a dry-run batch per owner instruction; dry-runs never upload or submit; "
                         "this approval lives only in the run's throwaway database")
REHEARSAL_PROFILE = V2_ROOT / "examples" / "profile.example.yaml"
REHEARSAL_ANSWERS = {"answers": [{"key": "voluntary_self_identification", "value": "decline", "source": "policy",
                                  "note": "rehearsal"}]}
REHEARSAL_GUARDRAILS = {"claims_to_avoid": [{"claim": "rehearsal: never claim a PhD", "pattern": r"\bPh\.?D\b"}]}


# --------------------------------------------------------------------------- inputs

def load_inputs(profile: Path, answers: Path, guardrails: Path) -> dict[str, Any]:
    answer_doc = yaml.safe_load(answers.read_text(encoding="utf-8")) or {}
    guard_doc = yaml.safe_load(guardrails.read_text(encoding="utf-8")) or {}
    items = answer_doc.get("answers") or []
    for item in items:
        if not {"key", "value"} <= set(item):
            raise SystemExit(f"answer entry needs key and value: {item}")
    patterns = guard_doc.get("claims_to_avoid") or []
    if not patterns:
        raise SystemExit("guardrails file has no claims_to_avoid patterns")
    return {"profile": str(profile.resolve()), "answers": items, "guardrails": patterns}


def rehearsal_profile(out: Path) -> Path:
    """The made-up example profile, marked verified (it is fake; the rehearsal only needs a ready profile)."""
    document = yaml.safe_load(REHEARSAL_PROFILE.read_text(encoding="utf-8"))
    document["verified"] = True
    path = out / "rehearsal-profile.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def refuse_repo_output(out: Path) -> None:
    resolved = out.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit(f"--out {resolved} is inside the repository; real-identity artifacts must stay outside it")


def load_targets(path: Path) -> list[dict[str, Any]]:
    targets = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for target in targets:
        if target.get("platform") not in PLATFORMS or not target.get("board") or not target.get("id"):
            raise SystemExit(f"bad target (needs platform in {sorted(PLATFORMS)}, board, id): {target}")
        key = (target["platform"], target["board"], str(target["id"]))
        if key in seen:
            raise SystemExit(f"duplicate target {key}")
        seen.add(key)
    return targets


# --------------------------------------------------------------------------- one GET per posting (parent)

def fetch_open_posting(target: dict[str, Any], *, client: Any = None, sleep: Any = time.sleep) -> dict[str, Any]:
    """Confirm the posting is open (one GET; one retry on a transport error or 5xx only)."""
    import httpx

    url = open_check_url(target)
    http = client or httpx.Client(timeout=30, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    requests = []
    try:
        for attempt in (1, 2):
            try:
                response = http.get(url)
            except httpx.TransportError as exc:
                requests.append({"method": "GET", "url": url, "error": exc.__class__.__name__})
                if attempt == 2:
                    return {**target, "open": False, "reason": f"transport error: {exc.__class__.__name__}",
                            "parent_requests": requests}
                sleep(5)
                continue
            requests.append({"method": "GET", "url": url, "status": response.status_code})
            if response.status_code >= 500 and attempt == 1:
                sleep(5)
                continue
            break
    finally:
        if client is None:
            http.close()
    payload = response.json() if response.status_code == 200 else None
    posting = parse_open_posting(target, response.status_code, payload)
    if posting is None:
        return {**target, "open": False, "reason": f"not open (HTTP {response.status_code})", "parent_requests": requests}
    return {**target, **posting, "id": str(target["id"]), "open": True, "verified_open_at": datetime.now(UTC).isoformat(),
            "verified_with": f"GET {url}", "parent_requests": requests}


# --------------------------------------------------------------------------- one run (child process)

def _capture_plans(trace: list[dict[str, Any]]) -> None:
    """Record every fill plan the pipeline makes, with the vault key and raw value behind each answer."""
    from app import form_engine, pipeline

    original = pipeline.plan_fill

    def plan_fill(controls: Any, vault: Any) -> Any:
        plan = original(controls, vault)
        trace.clear()
        for item in plan.items:
            control = item.control
            entry = {"key": control.key, "label": control.label, "section": control.section,
                     "required": control.required, "type": control.control_type.value, "options": len(control.options),
                     "status": item.status, "value": item.value, "reason": item.reason}
            if item.status == "fill" and item.reason != DECLINE_REASON:
                resolved = form_engine._resolve_control(control, vault, control.sensitive or control.legal)
                record = vault._records.get(resolved.key)
                entry.update(resolved_key=resolved.key, resolved_value=resolved.value,
                             resolved_source=getattr(getattr(record, "source", None), "value", None))
            trace.append(entry)
        return plan

    pipeline.plan_fill = plan_fill


def _allowed_answers(db: Any, application: Any, answers: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Vault keys/values a planned answer may come from: verified facts, owner answers, approved material."""
    from app.answer_vault import _canonical_key
    from app.material_store import vault_records
    from app.profile import verified_profile
    from app.profile_vault import profile_answers

    allowed: dict[str, list[Any]] = {}
    sources = [*profile_answers(verified_profile(db)).items(), *((item["key"], item["value"]) for item in answers),
               *((record.key, record.value) for record in vault_records(db, application))]
    for key, value in sources:
        allowed.setdefault(_canonical_key(key), []).append(value)
    return allowed


def _materials(db: Any, application_id: str, guardrails: list[dict[str, str]]) -> list[dict[str, Any]]:
    from app.material_store import materials_for
    from app.profile import verified_profile
    from app.truth_guard import check_cover_letter, check_resume

    profile = verified_profile(db)
    rows = []
    for row in materials_for(db, application_id):
        document = json.loads(row.content_json)
        guard = check_resume(document, profile) if row.kind == "resume" else check_cover_letter(document, profile)
        rows.append({"kind": row.kind, "status": row.status, "version": row.version, "content_sha256": row.content_sha256,
                     "pdf_path": row.pdf_path, "pdf_sha256": row.pdf_sha256, "decision_note": row.decision_note,
                     "claims_hits": claims_hits(row.text, guardrails), "truth_guard": guard})
    return rows


def seed_application(posting: dict[str, Any], inputs: dict[str, Any], data: dict[str, Any]) -> tuple[str, str]:
    """Profile, owner answers, job, application, materials (résumé approved, cover letter rejected)."""
    from app import material_workflow
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import Application, Job, PipelineStage
    from app.profile import import_profile_document, parse_profile_text
    from app.vault_store import upsert_answer

    with SessionLocal() as db:
        document = parse_profile_text(Path(inputs["profile"]).read_text(encoding="utf-8"), "yaml")
        imported = import_profile_document(db, document, origin=Path(inputs["profile"]).name).as_dict()
        for item in inputs["answers"]:
            upsert_answer(db, key=item["key"], value=str(item["value"]), source=item.get("source", "user"),
                          note=item.get("note", ""))
        job = Job(source=posting["platform"], external_id=posting["id"], board=posting["board"], title=posting["title"],
                  company=posting["company"], location=posting.get("location") or "", url=posting["url"],
                  description=posting.get("description") or posting["title"], stage=PipelineStage.shortlisted.value,
                  platform=posting["platform"])
        db.add(job)
        db.commit()
        application = Application(job_id=job.id, mode="dry_run", adapter_name=posting["platform"])
        db.add(application)
        db.commit()
        rows = {row.kind: row for row in material_workflow.generate_for_application(db, get_settings(), application.id,
                                                                                     client=None)}
        material_workflow.approve_material(db, rows["resume"].id, note=DRY_RUN_APPROVAL_NOTE)
        material_workflow.reject_material(db, rows["cover_letter"].id,
                                          note="cover letter not used in dry-run batch (owner instruction)")
        db.refresh(job)
        db.refresh(application)
        data["seed"] = {"profile_import": {key: imported[key] for key in ("created", "verified", "unverified", "warnings")},
                        "answers_stored": [item["key"] for item in inputs["answers"]],
                        "job_stage": job.stage, "application_stage": application.stage}
        data["allowed_answers"] = _allowed_answers(db, application, inputs["answers"])
        data["decline_policy"] = any(item["key"] == "voluntary_self_identification" and str(item["value"]) == "decline"
                                     for item in inputs["answers"])
        if job.stage != PipelineStage.ready_to_apply.value:
            raise RuntimeError(f"job did not reach ready_to_apply (stage {job.stage})")
        return job.id, application.id


def run_once(run_dir: Path, mode: str, posting: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    from app import fixture_origin
    from app.config import get_settings
    from app.db import SessionLocal

    started = datetime.now(UTC)
    platform = PLATFORMS[posting["platform"]]
    http_log: list[dict[str, Any]] = []
    plan_trace: list[dict[str, Any]] = []
    data: dict[str, Any] = {"http_log": http_log, "plan_trace": plan_trace, "job_id": posting["id"],
                            "run_dir": str(run_dir), "api_host": platform["api_hosts"][0], **platform,
                            "cdp_in_app_code": _cdp_in_app_code(), "guardrails_loaded": len(inputs["guardrails"]),
                            "stdin_closed": sys.stdin is None or sys.stdin.closed or not sys.stdin.isatty()}
    settings = get_settings()
    data["settings"] = {"application_mode": settings.application_mode, "allow_live_submission": settings.allow_live_submission}
    fixtures = None
    try:
        if mode == "rehearse":
            server_log: list[dict[str, Any]] = []
            fixtures = _start_fixtures("clean", server_log)
            origin = f"http://127.0.0.1:{fixtures.server_address[1]}"
            fixture_origin.enable_for_gate1(origin, settings, acknowledgement=fixture_origin.GATE1_ACKNOWLEDGEMENT)
            posting = {**posting, "url": f"{origin}/{BOARD}/jobs/{JOB_ID}"}
            data.update(api_host="127.0.0.1", api_hosts=["127.0.0.1"], fixture_server_log=server_log)
        _instrument_http(http_log, data, query_limit=4000)
        _capture_plans(plan_trace)
        baseline = live_descendants()
        sampler, application_id = _drive_api(data, lambda: seed_application(posting, inputs, data), answers={},
                                             api_key=API_KEY)
        data["leftover_children"] = _wait_for_cleanup(baseline, 10)
        data["sampled_processes"] = sampler.processes
        data["leftover_sampled"] = still_running(sampler.processes)
        data["ledger"] = _ledger(application_id)
        with SessionLocal() as db:
            data["materials"] = _materials(db, application_id, inputs["guardrails"])
    finally:
        fixture_origin.disable()
        if fixtures is not None:
            fixtures.shutdown()
            fixtures.server_close()
    (run_dir / "observations.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    result = evaluate(data)
    result["checks"].update(run_extra_checks(data))
    result["outcome"] = batch_outcome(result["checks"], result["challenge"])
    return {"mode": mode, "started_at": started.isoformat(), "finished_at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(), "posting": posting, "plan_trace": plan_trace, **result}


def batch_env(run_dir: Path) -> dict[str, str]:
    env = child_env(run_dir)
    env.update({"LEVER_BROWSER_VERIFY": "true", "ASHBY_BROWSER_VERIFY": "true", "GREENHOUSE_BROWSER_TIMEOUT": "60"})
    return env


def _child_entry(run_dir: str, mode: str, posting: dict[str, Any], inputs: dict[str, Any]) -> None:
    folder = Path(run_dir)
    os.environ.clear()
    os.environ.update(batch_env(folder))
    os.chdir(folder)
    sys.path.insert(0, str(V2_ROOT))
    with (folder / "child.log").open("w", encoding="utf-8") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        result = run_once(folder, mode, posting, inputs)
    (folder / "run.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- orchestration (parent)

def run_child(run_dir: Path, mode: str, posting: dict[str, Any], inputs: dict[str, Any], timeout: float) -> dict[str, Any]:
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    os.chmod(run_dir, 0o700)
    child = SPAWN.Process(target=_child_entry, args=(str(run_dir), mode, posting, inputs), name="dryrun-batch-run")
    child.start()
    supervision = supervise(child, timeout)
    result_path = run_dir / "run.json"
    if supervision["timed_out"] or not result_path.is_file():
        log = run_dir / "child.log"
        reason = f"timed out after {timeout:g}s" if supervision["timed_out"] else f"exited {supervision['exit_code']}"
        return {"outcome": "fail", "crashed": True, "error": f"run process {reason} without a result",
                "supervision": supervision, "run_dir": str(run_dir), "checks": {}, "posting": posting,
                "log_tail": log.read_text(encoding="utf-8")[-4000:] if log.is_file() else ""}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result = _after_exit(result, supervision, still_running(result.get("browser_processes") or []))
    result.update(child_exit_code=supervision["exit_code"], run_dir=str(run_dir))
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def transient_failure(result: dict[str, Any]) -> bool:
    """A failure caused by a transient network error (eligible for the single retry)."""
    if result.get("outcome") != "fail" or result.get("crashed"):
        return False
    api = ((result.get("checks") or {}).get("api_completed_cleanly") or {}).get("detail") or {}
    return is_transient(f"{api.get('reason')} {api.get('detail')}")


def run_report(index: int, posting: dict[str, Any], result: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    checks = result.get("checks") or {}
    read_only = (checks.get("zero_submit_and_zero_non_get_sent") or {}).get("detail") or {}
    shot = (checks.get("screenshot_saved_and_hashed") or {}).get("detail") or {}
    return {
        "index": index, "posting": {key: posting.get(key) for key in ("platform", "board", "id", "company", "title",
                                                                      "location", "url", "verified_open_at", "verified_with")},
        "parent_requests": posting.get("parent_requests"),
        "outcome": result.get("outcome"), "challenge": result.get("challenge"), "attempts": attempts,
        "error": result.get("error"), "log_tail": result.get("log_tail"),
        "failed_checks": sorted(name for name, item in checks.items() if not item.get("ok")),
        "trace_sha256": (checks.get("trace_zip_valid_and_hashed") or {}).get("detail", {}).get("sha256"),
        "screenshot_sha256": shot.get("sha256"),
        "request_counts": read_only.get("browser"), "blocked_requests": read_only.get("aborted_requests"),
        "app_http_by_method": read_only.get("app_http_by_method"),
        "field_mapping": result.get("field_mapping"), "plan_trace": result.get("plan_trace"),
        "shutdown": {"api_shutdown_clean": (checks.get("api_completed_cleanly") or {}).get("detail", {}).get("api_shutdown_clean"),
                     "browser_gone": (checks.get("browser_gone_after_child_exit") or {}).get("ok"),
                     "no_process_outlived": (checks.get("no_process_outlived_the_run") or {}).get("ok"),
                     "child_exit_code": result.get("child_exit_code")},
        "checks": checks, "run_dir": result.get("run_dir"),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_summary(out: Path, reports: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    rows = [run_row(report) for report in reports]
    summary = {"generated_at": datetime.now(UTC).isoformat(), **meta, "aggregate": summarize(rows), "rows": rows,
               "not_proven": NOT_PROVEN, "not_proven_notes": NOT_PROVEN_NOTES}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    lines = ["| # | Company | Title | ATS | Verdict | Planned | Review | Planned % | Top review reasons | Bot check |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['index']} | {row['company']} | {row['title']} | {row['ats']} | {row['verdict']} | "
                     f"{row['planned']} | {row['needs_review']} | {row['planned_pct']} | "
                     f"{'; '.join(row['top_review_reasons'])} | {'yes' if row['bot_check'] else 'no'} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n\n```json\n" + json.dumps(summary["aggregate"], indent=2)
                                    + "\n```\n", encoding="utf-8")
    return summary


def run_batch(targets: list[dict[str, Any]], inputs: dict[str, Any], out: Path, *, mode: str, pause: float,
              timeout: float, fetch: Any = fetch_open_posting, runner: Any = run_child,
              sleep: Any = time.sleep, first_index: int = 1) -> tuple[list[dict[str, Any]], str | None]:
    """Run every target once (plus one retry on a transient network error). Stops on a crashed run."""
    reports: list[dict[str, Any]] = []
    stopped = None
    for index, target in enumerate(targets, start=first_index):
        if index > first_index and pause:
            sleep(pause)
        if mode == "live":
            posting = fetch(target)
            if not posting["open"]:
                skipped = {"index": index, "posting": posting, "outcome": "skipped_closed", "checks": {},
                           "error": posting["reason"]}
                reports.append(skipped)
                (out / f"run-{index:02d}-report.json").write_text(json.dumps(skipped, indent=2, default=str),
                                                                   encoding="utf-8")
                continue
        else:
            posting = {**target, "id": str(target["id"]), "title": target.get("title", "fixture posting"),
                       "company": target.get("company", BOARD), "url": "", "location": "Toronto, Ontario"}
        attempts = []
        run_dir = out / f"run-{index:02d}-{posting['platform']}-{posting['board']}"
        result = runner(run_dir, mode, posting, inputs, timeout)
        attempts.append({"attempt": 1, "outcome": result.get("outcome"), "error": result.get("error")})
        if transient_failure(result):
            sleep(max(pause, 5))
            result = runner(run_dir.with_name(run_dir.name + "-retry"), mode, posting, inputs, timeout)
            attempts.append({"attempt": 2, "outcome": result.get("outcome"), "error": result.get("error"),
                             "reason": "retry after a transient network error"})
        report = run_report(index, posting, result, attempts)
        reports.append(report)
        (out / f"run-{index:02d}-report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[{index}/{first_index + len(targets) - 1}] {posting['platform']}:{posting['board']} {report['outcome']} "
              f"planned={(report.get('field_mapping') or {}).get('planned_count')} "
              f"review={(report.get('field_mapping') or {}).get('needs_review_count')} "
              f"failed={report['failed_checks'] or report.get('error')}", flush=True)
        if result.get("crashed"):
            stopped = f"run {index} crashed ({result.get('error')}); batch stopped so the cause can be fixed first"
            break
    return reports, stopped


def recheck(out: Path) -> list[dict[str, Any]]:
    """Re-evaluate finished runs from their saved observations (no network, no browser).

    For fixing a *check* without loading any employer page again. The after-exit process checks
    keep the values recorded when each run finished.
    """
    reports = []
    sys.path.insert(0, str(V2_ROOT))
    for path in sorted(out.glob("run-*-report.json")):
        previous = json.loads(path.read_text(encoding="utf-8"))
        run_dir = Path(previous.get("run_dir") or "")
        observations = run_dir / "observations.json"
        if previous.get("outcome") == "skipped_closed" or not observations.is_file():
            reports.append(previous)
            continue
        data = json.loads(observations.read_text(encoding="utf-8"))
        os.environ["DATABASE_PATH"] = str(run_dir / "data" / "gate1.db")
        result = evaluate(data)
        result["checks"].update(run_extra_checks(data))
        for name in ("browser_gone_after_child_exit", "no_process_outlived_the_run"):
            if name in previous["checks"]:
                result["checks"][name] = previous["checks"][name]
        result["outcome"] = batch_outcome(result["checks"], result["challenge"])
        result.update(plan_trace=data.get("plan_trace"), run_dir=str(run_dir),
                      child_exit_code=previous["shutdown"].get("child_exit_code"))
        report = run_report(previous["index"], {**previous["posting"], "parent_requests": previous.get("parent_requests")},
                            result, previous.get("attempts") or [])
        report["rechecked"] = {"at": datetime.now(UTC).isoformat(), "previous_outcome": previous.get("outcome"),
                               "previous_failed_checks": previous.get("failed_checks")}
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        reports.append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="real public postings (local/manual only, never CI)")
    mode.add_argument("--rehearse", action="store_true", help="same runner on Gate 1's loopback fixtures")
    mode.add_argument("--recheck", action="store_true", help="re-evaluate the runs in --out from saved observations")
    parser.add_argument("--targets", type=Path, help="JSON list of {platform, board, id, company} (live)")
    parser.add_argument("--profile", type=Path, help="owner profile document (YAML)")
    parser.add_argument("--answers", type=Path, help="owner form-answer defaults (YAML: answers: [{key, value, source, note}])")
    parser.add_argument("--guardrails", type=Path, help="materials guardrails (YAML: claims_to_avoid: [{claim, pattern}])")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pause", type=float, default=30.0, help="seconds between runs (live minimum 20)")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--start", type=int, default=1, help="1-based index of the first target to run")
    parser.add_argument("--limit", type=int, default=0, help="run at most this many targets (0 = all)")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if args.recheck:
        previous = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        meta = {key: previous.get(key) for key in ("mode", "targets", "start", "stopped", "profile_sha256", "pause_s")}
        summary = write_summary(out, recheck(out), {**meta, "rechecked_at": datetime.now(UTC).isoformat()})
        print(json.dumps(summary["aggregate"], indent=2))
        return 0
    if args.live:
        if os.environ.get("CI"):
            parser.error("--live never runs in CI; it contacts real employer postings")
        if not all((args.targets, args.profile, args.answers, args.guardrails)):
            parser.error("--live needs --targets, --profile, --answers and --guardrails")
        if args.pause < 20:
            parser.error("--pause must be at least 20 seconds between live runs")
        refuse_repo_output(out)
        inputs = load_inputs(args.profile, args.answers, args.guardrails)
        targets = load_targets(args.targets)
        run_mode = "live"
    else:
        out.mkdir(parents=True, exist_ok=True)
        answers, guardrails = out / "rehearsal-answers.yaml", out / "rehearsal-guardrails.yaml"
        answers.write_text(yaml.safe_dump(REHEARSAL_ANSWERS), encoding="utf-8")
        guardrails.write_text(yaml.safe_dump(REHEARSAL_GUARDRAILS), encoding="utf-8")
        inputs = load_inputs(args.profile or rehearsal_profile(out), answers, guardrails)
        targets = [{"platform": "greenhouse", "board": BOARD, "id": JOB_ID, "company": BOARD}]
        run_mode = "rehearse"
    selected = targets[args.start - 1:]
    selected = selected[:args.limit] if args.limit else selected
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    reports, stopped = run_batch(selected, inputs, out, mode=run_mode, pause=args.pause, timeout=args.timeout,
                                 first_index=args.start)
    meta = {"mode": run_mode, "targets": len(selected), "start": args.start, "stopped": stopped,
            "profile_sha256": _sha256(Path(inputs["profile"])), "pause_s": args.pause}
    # The summary covers every run report in --out, so a batch resumed with --start is summarized whole.
    every = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(out.glob("run-*-report.json"))]
    summary = write_summary(out, every or reports, meta)
    print(json.dumps(summary["aggregate"], indent=2))
    if stopped:
        print(stopped)
        return 3
    ok = all(row["safety_ok"] for row in summary["rows"] if row["verdict"] != "skipped_closed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
