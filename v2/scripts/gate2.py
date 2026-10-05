#!/usr/bin/env python3
"""Gate 2 of docs/RECOVERY_CONTRACT.md: one dry-run against one real public Greenhouse posting.

Same runtime as Gate 1 (``scripts/gate1.py``): a freshly spawned process starts
the real v2 FastAPI app under uvicorn on 127.0.0.1, seeds a clearly fake
identity through ``POST /api/answers`` and one job at ``ready_to_apply``, and
calls ``POST /api/applications/{id}/apply`` with ``GREENHOUSE_BROWSER_VERIFY=true``
and ``BROWSER_TRACE_DIR`` set. The production path then probes the posting
(GET), fetches the form from the public boards API (GET), and has Playwright
launch its own Chromium (``chromium.launch()``, never CDP) to load the hosted
form once, read-only: only GET leaves the browser, form submission is refused
in the page, service workers are blocked, and every request is recorded.

The parent checks the result and writes ``gate-artifacts/gate2/gate2-report.json``:
outcome (``pass`` / ``fail`` / ``blocked_by_bot_check``), field mapping, browser
requests by method and host, the trace zip and screenshot with SHA-256 values,
the ledger row, process cleanup, and what is proven and not proven.

A bot check, CAPTCHA interstitial, or block status stops the run safely and is
reported as ``blocked_by_bot_check`` (a valid Gate 2 finding). Nothing retries
the page load and nothing tries to get past a challenge.

Usage (from ``v2/``)::

    python scripts/gate2.py --live --board gitlab --job-id 8773006002   # the real run (local/manual only)
    python scripts/gate2.py --rehearse                                  # same runner on Gate 1's loopback fixtures

``--live`` contacts boards-api.greenhouse.io and job-boards.greenhouse.io once
each (plus the page's own GET subresources). CI only ever runs ``--rehearse``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gate1 import (
    SPAWN,
    _cdp_in_app_code,
    _drive_api,
    _ledger,
    _seed_job,
    _start_fixtures,
    _wait_for_cleanup,
    child_env,
    supervise,
)
from gate1_checks import BOARD, DEFAULT_OUT, JOB_ID, V2_ROOT, live_descendants, still_running
from gate2_checks import (
    FAKE_IDENTITY,
    GREENHOUSE_API_HOST,
    build_report,
    challenge_detected,
    choose_posting,
    field_mapping,
    outcome,
    run_checks,
)

GATE2_OUT = DEFAULT_OUT / "gate2"
USER_AGENT = "HunterXJob-gate2/1.0 (read-only dry-run verification)"


# --------------------------------------------------------------------------- posting (parent, live only)

def fetch_posting(board: str, job_id: str, *, client: Any = None) -> dict[str, Any]:
    """Confirm the posting is open on the public boards API (one GET; one retry on a transient error only)."""
    import httpx

    url = f"https://{GREENHOUSE_API_HOST}/v1/boards/{board}/jobs/{job_id}"
    http = client or httpx.Client(timeout=30, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    try:
        for attempt in (1, 2):
            try:
                response = http.get(url)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(5)
                continue
            if response.status_code >= 500 and attempt == 1:
                time.sleep(5)
                continue
            break
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise SystemExit(f"posting {board}/{job_id} is not open on the boards API (HTTP {response.status_code})")
    posting = choose_posting({"jobs": [response.json()]}, job_id)
    return {**posting, "board": board, "verified_open_at": datetime.now(UTC).isoformat(), "verified_with": f"GET {url}"}


# --------------------------------------------------------------------------- one run (child process)

def _instrument_http(log: list[dict[str, Any]], data: dict[str, Any]) -> None:
    """Record every HTTP request the app (and this runner) sends; requests to the local API are tagged."""
    import httpx

    original = httpx.Client.send

    def send(self: Any, request: Any, **kwargs: Any) -> Any:
        url = request.url
        local = f"{url.scheme}://{url.host}:{url.port}" == data.get("api_base")
        entry = {"method": request.method, "host": url.host, "path": url.path, "query": url.query.decode()[:200],
                 "local_api": local, "at": time.time()}
        try:
            response = original(self, request, **kwargs)
            entry["status"] = response.status_code
            return response
        finally:
            log.append(entry)

    httpx.Client.send = send  # type: ignore[method-assign]


def _seed_live_job(posting: dict[str, Any]) -> tuple[str, str]:
    from app.db import SessionLocal
    from app.models import Application, Job, PipelineStage

    with SessionLocal() as db:
        job = Job(source="greenhouse", external_id=posting["id"], title=posting["title"], company=posting["board"],
                  location=posting.get("location") or "", url=posting["absolute_url"], description="Gate 2 posting",
                  stage=PipelineStage.ready_to_apply.value, platform="greenhouse")
        db.add(job)
        db.commit()
        application = Application(job_id=job.id, mode="dry_run", adapter_name="greenhouse")
        db.add(application)
        db.commit()
        return job.id, application.id


def run_once(run_dir: Path, mode: str, posting: dict[str, Any]) -> dict[str, Any]:
    """One Gate 2 run inside this (child) process."""
    from app import fixture_origin
    from app.config import get_settings

    started = datetime.now(UTC)
    http_log: list[dict[str, Any]] = []
    data: dict[str, Any] = {"http_log": http_log, "job_id": posting["id"], "run_dir": str(run_dir),
                            "api_host": GREENHOUSE_API_HOST, "cdp_in_app_code": _cdp_in_app_code(),
                            "stdin_closed": sys.stdin is None or sys.stdin.closed or not sys.stdin.isatty()}
    fixtures = None
    try:
        if mode == "rehearse":
            server_log: list[dict[str, Any]] = []
            fixtures = _start_fixtures("clean", server_log)
            origin = f"http://127.0.0.1:{fixtures.server_address[1]}"
            fixture_origin.enable_for_gate1(origin, get_settings(), acknowledgement=fixture_origin.GATE1_ACKNOWLEDGEMENT)
            data.update(api_host="127.0.0.1", fixture_server_log=server_log)
            seed = lambda: _seed_job(origin)  # noqa: E731
        else:
            seed = lambda: _seed_live_job(posting)  # noqa: E731
        _instrument_http(http_log, data)
        baseline = live_descendants()
        sampler, application_id = _drive_api(data, seed, FAKE_IDENTITY)
        data["leftover_children"] = _wait_for_cleanup(baseline, 10)
        data["sampled_processes"] = sampler.processes
        data["leftover_sampled"] = still_running(sampler.processes)
        data["ledger"] = _ledger(application_id)
    finally:
        fixture_origin.disable()
        if fixtures is not None:
            fixtures.shutdown()
            fixtures.server_close()
    # Raw observations are kept so the verdict can be re-evaluated later without loading the page again.
    (run_dir / "observations.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return {"mode": mode, "started_at": started.isoformat(), "finished_at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(), "posting": posting, **evaluate(data)}


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    """Checks, outcome, and field mapping from one run's raw observations."""
    checks, ctx = run_checks(data)
    challenge = challenge_detected(ctx)
    validation = dict(ctx.validation)
    if "planned" not in validation:
        validation["planned"] = ctx.ledger.get("answers") or {}
    return {"challenge": challenge, "outcome": outcome(checks, challenge), "checks": checks,
            "field_mapping": field_mapping(ctx.result, validation, ctx.evidence), "browser_processes": ctx.browsers,
            "fixture_server_log": data.get("fixture_server_log")}


def _child_entry(run_dir: str, mode: str, posting: dict[str, Any]) -> None:
    folder = Path(run_dir)
    os.environ.clear()
    os.environ.update(child_env(folder))
    os.chdir(folder)
    sys.path.insert(0, str(V2_ROOT))
    with (folder / "child.log").open("w", encoding="utf-8") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        result = run_once(folder, mode, posting)
    (folder / "run.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


# --------------------------------------------------------------------------- orchestration (parent)

def _after_exit(result: dict[str, Any], supervision: dict[str, Any], orphans: list[dict[str, Any]]) -> dict[str, Any]:
    result["checks"]["browser_gone_after_child_exit"] = {"ok": not orphans, "detail": {"orphans": orphans}}
    result["checks"]["no_process_outlived_the_run"] = {"ok": not supervision["orphans"], "detail": supervision}
    if orphans or supervision["orphans"] or supervision["exit_code"] != 0:
        result["outcome"] = "fail"
    return result


def recheck(out: Path) -> dict[str, Any]:
    """Re-evaluate a finished run from its saved observations (no network, no browser).

    The trace and screenshot files are re-verified on disk; the after-exit process
    checks keep the values recorded when the run finished.
    """
    run_dir = out / "run"
    previous = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if "no_process_outlived_the_run" not in previous["checks"] and (out / "gate2-report.json").is_file():
        previous = json.loads((out / "gate2-report.json").read_text(encoding="utf-8"))["run"]  # older layout
    data = json.loads((run_dir / "observations.json").read_text(encoding="utf-8"))
    os.environ.setdefault("DATABASE_PATH", str(run_dir / "data" / "gate1.db"))
    sys.path.insert(0, str(V2_ROOT))
    result = {**previous, **evaluate(data)}
    supervision = previous["checks"]["no_process_outlived_the_run"]["detail"]
    orphans = previous["checks"]["browser_gone_after_child_exit"]["detail"]["orphans"]
    result = _after_exit(result, supervision, orphans)
    result["rechecked"] = {"at": datetime.now(UTC).isoformat(), "previous_outcome": previous.get("outcome"),
                           "previous_failed_checks": sorted(name for name, item in previous["checks"].items()
                                                            if not item["ok"])}
    return result


def run_child(out: Path, mode: str, posting: dict[str, Any], timeout: float) -> dict[str, Any]:
    run_dir = (out / "run").resolve()
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    child = SPAWN.Process(target=_child_entry, args=(str(run_dir), mode, posting), name="gate2-run")
    child.start()
    supervision = supervise(child, timeout)
    result_path = run_dir / "run.json"
    if supervision["timed_out"] or not result_path.is_file():
        log = run_dir / "child.log"
        reason = f"timed out after {timeout:g}s" if supervision["timed_out"] else f"exited {supervision['exit_code']}"
        return {"ok": False, "outcome": "fail", "error": f"run process {reason} without a result",
                "supervision": supervision, "run_dir": str(run_dir), "checks": {},
                "log_tail": log.read_text(encoding="utf-8")[-4000:] if log.is_file() else ""}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result = _after_exit(result, supervision, still_running(result.get("browser_processes") or []))
    result.update(child_exit_code=supervision["exit_code"], run_dir=str(run_dir))
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="load ONE real public Greenhouse posting (local/manual only)")
    mode.add_argument("--rehearse", action="store_true", help="same runner against Gate 1's loopback fixtures")
    mode.add_argument("--recheck", action="store_true",
                      help="re-evaluate the run in --out from its saved observations (no network, no browser)")
    parser.add_argument("--board", help="Greenhouse board token (live)")
    parser.add_argument("--job-id", help="Greenhouse job id (live)")
    parser.add_argument("--out", type=Path, default=GATE2_OUT)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if args.recheck:
        run = recheck(out)
        report = build_report(run, posting=run["posting"], mode=run["mode"])
        report["rechecked"] = run["rechecked"]
        return _write_report(out, report, run)
    if args.live:
        if os.environ.get("CI"):
            parser.error("--live never runs in CI; it contacts a real employer posting")
        if not (args.board and args.job_id):
            parser.error("--live needs --board and --job-id")
        posting = fetch_posting(args.board, args.job_id)
        run_mode = "live"
    else:
        posting = {"id": JOB_ID, "board": BOARD, "title": "Gate 1 fixture posting (loopback rehearsal)",
                   "absolute_url": None, "location": ""}
        run_mode = "rehearse"
    out.mkdir(parents=True, exist_ok=True)
    run = run_child(out, run_mode, posting, args.timeout)
    return _write_report(out, build_report(run, posting=posting, mode=run_mode), run)


def _write_report(out: Path, report: dict[str, Any], run: dict[str, Any]) -> int:
    posting, run_mode = report["posting"], report["mode"]
    report_path = out / "gate2-report.json"
    if report.get("rechecked") and report_path.is_file():
        first = out / "gate2-report.first-evaluation.json"
        if not first.exists():
            report_path.replace(first)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    counts = report.get("request_counts") or {}
    mapping = report.get("field_mapping") or {}
    print(f"gate2 {run_mode}: {report['outcome'].upper()} posting={posting.get('board')}/{posting.get('id')} "
          f"challenge={report['challenge']} failed={report['failed_checks'] or run.get('error')}")
    print(f"  trace={report['trace_sha256']} requests_by_method={counts.get('by_method')} "
          f"non_get_aborted={counts.get('non_get_aborted')}")
    print(f"  dom_fields={mapping.get('dom_fields_detected')} planned={mapping.get('planned_count')} "
          f"needs_review={mapping.get('needs_review_count')} -> {report_path}")
    expected = "pass"
    return 0 if report["outcome"] == expected else (2 if report["outcome"] == "blocked_by_bot_check" else 1)


if __name__ == "__main__":
    sys.exit(main())
