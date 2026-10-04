#!/usr/bin/env python3
"""Gate 1 of docs/RECOVERY_CONTRACT.md: prove the existing dry-run path on plain Linux.

Each run is an independent, freshly spawned Python process that:

1. serves fixtures that already live in the repo over loopback HTTP
   (``tests/fixtures/greenhouse/d2l_7696196.json`` as the boards-API payload and
   ``tests/fixtures/sample_form.html`` as the hosted embed form);
2. points the Greenhouse fetcher at that server with the test-only
   :mod:`app.fixture_origin` allowance (refused unless dry-run only);
3. starts the real v2 FastAPI app under uvicorn on 127.0.0.1;
4. seeds fake answers through ``POST /api/answers`` and one fake job/application;
5. calls ``POST /api/applications/{id}/apply`` (the production dry-run path) with
   ``GREENHOUSE_BROWSER_VERIFY=true`` and ``BROWSER_TRACE_DIR`` set, so the
   production form engine plans against the fixture and Playwright launches its
   own Chromium (``chromium.launch()``) to verify the page with tracing on;
6. checks: Chromium launched by Playwright as our descendant, fixture loaded,
   extracted fields and planned values match, trace zip valid and hashed,
   ledger evidence bound to that hash, zero submit actions and zero non-GET
   requests (browser log *and* the fixture server's own log), no browser or
   child process left after shutdown, and a clean API/task completion.

The parent process runs ``--runs`` (default 3) independent launches, re-checks
that every sampled browser PID is gone after each child exits, and writes
``gate-artifacts/gate1-report.json`` with proven and not-proven claims.

Usage (from ``v2/``)::

    python scripts/gate1.py                # 3 runs, exit 0 only if every check passes
    python scripts/gate1.py --runs 5
    python scripts/gate1.py --variant post_beacon --runs 1   # negative: must fail

Fake fixture data only. Nothing here talks to a real employer site.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import shutil
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gate1_checks import (
    API_FIXTURE,
    API_KEY,
    API_PATH,
    BOARD,
    DEFAULT_OUT,
    EMBED_PATH,
    EMBED_QUERY,
    FAKE_ANSWERS,
    FORM_FIXTURE,
    JOB_ID,
    NEGATIVE_DIR,
    V2_ROOT,
    VARIANTS,
    ProcessSampler,
    build_report,
    expected_fields,
    is_browser,
    live_descendants,
    run_checks,
    run_summary,
    still_running,
    terminate,
)

SPAWN = multiprocessing.get_context("spawn")


# --------------------------------------------------------------------------- fixture server

def _fixture_handler(form_html: bytes, api_json: bytes, log: list[dict[str, Any]], lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        server_version = "gate1-fixtures"

        def log_message(self, *_args: object) -> None:  # quiet
            return

        def _record(self, expected: bool, status: int) -> None:
            parts = urlsplit(self.path)
            with lock:
                log.append({"method": self.command, "path": parts.path, "query": parts.query, "status": status,
                            "expected": expected, "user_agent": self.headers.get("User-Agent", "")[:160],
                            "at": time.time()})

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path == API_PATH and query in ({"questions": ["true"]}, {}):
                self._record(True, 200)
                self._send(200, api_json, "application/json")
            elif parts.path == EMBED_PATH and query == EMBED_QUERY:
                self._record(True, 200)
                self._send(200, form_html, "text/html; charset=utf-8")
            else:
                self._record(False, 404)
                self._send(404, b"not a gate1 fixture", "text/plain")

        def _refuse(self) -> None:
            self._record(False, 405)
            self._send(405, b"gate1 fixtures are GET-only", "text/plain")

        # Only GET is expected: HEAD is not a GET, so it is refused and recorded as unexpected too.
        do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse

    return Handler


def form_html_for(variant: str) -> str:
    html = FORM_FIXTURE.read_text(encoding="utf-8")
    snippet = VARIANTS[variant]
    if snippet is None:
        return html
    injected = (NEGATIVE_DIR / snippet).read_text(encoding="utf-8")
    return html.replace("</body>", injected + "</body>", 1)


# --------------------------------------------------------------------------- one run (child process)

def _seed_job(origin: str) -> tuple[str, str]:
    from app.db import SessionLocal
    from app.models import Application, Job, PipelineStage

    with SessionLocal() as db:
        job = Job(source="greenhouse", external_id=JOB_ID, title="Gate 1 fixture posting", company=BOARD,
                  location="Toronto, Ontario", url=f"{origin}/{BOARD}/jobs/{JOB_ID}", description="Gate 1 fixture",
                  stage=PipelineStage.ready_to_apply.value, platform="greenhouse")
        db.add(job)
        db.commit()
        application = Application(job_id=job.id, mode="dry_run", adapter_name="greenhouse")
        db.add(application)
        db.commit()
        return job.id, application.id


def _start_api() -> tuple[Any, threading.Thread, str]:
    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="gate1-uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, thread, f"http://127.0.0.1:{port}"


def _ledger(application_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Application, PipelineEvent, SubmissionEvidence

    with SessionLocal() as db:
        application = db.get(Application, application_id)
        rows = db.execute(select(SubmissionEvidence).where(SubmissionEvidence.application_id == application_id)).scalars().all()
        events = db.execute(select(PipelineEvent).where(PipelineEvent.job_id == application.job_id)
                            .order_by(PipelineEvent.created_at)).scalars().all()
        return {
            "application_stage": application.stage,
            "answers": json.loads(application.answers_json or "{}"),
            "validation": json.loads(application.validation_json or "{}"),
            "evidence": [{"kind": row.kind, "sufficient": row.sufficient, "payload_hash": row.payload_hash,
                          "confirmation_text": row.confirmation_text, "adapter_name": row.adapter_name,
                          "final_url": row.final_url} for row in rows],
            "events": [f"{event.from_stage}->{event.to_stage}" for event in events],
        }



def _cdp_in_app_code() -> bool:
    return any("connect_over_cdp" in path.read_text(encoding="utf-8") for path in (V2_ROOT / "app").rglob("*.py"))


def _start_fixtures(variant: str, log: list[dict[str, Any]]) -> ThreadingHTTPServer:
    handler = _fixture_handler(form_html_for(variant).encode(), API_FIXTURE.read_bytes(), log, threading.Lock())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, name="gate1-fixtures", daemon=True).start()
    return server


def _drive_api(data: dict[str, Any], origin: str) -> tuple[ProcessSampler, str]:
    """Start the real app, seed fake data, call the production dry-run route, and shut the app down."""
    import httpx

    server, api_thread, api = _start_api()
    with httpx.Client(base_url=api, headers={"X-API-Key": API_KEY}, timeout=240, trust_env=False) as client:
        for key, value in FAKE_ANSWERS.items():
            client.post("/api/answers", json={"key": key, "value": value, "source": "user"}).raise_for_status()
        job_id, application_id = _seed_job(origin)
        with ProcessSampler() as sampler:
            begin = time.monotonic()
            response = client.post(f"/api/applications/{application_id}/apply")
            elapsed = round(time.monotonic() - begin, 3)
        data["job_detail_status"] = client.get(f"/api/jobs/{job_id}").status_code
    is_json = response.headers.get("content-type", "").startswith("application/json")
    data["response"] = {"status_code": response.status_code, "elapsed_s": elapsed,
                        "json": response.json() if is_json else None}
    server.should_exit = True
    api_thread.join(timeout=30)
    data["api_shutdown_clean"] = not api_thread.is_alive()
    return sampler, application_id


def _wait_for_cleanup(baseline: list[dict[str, Any]], patience: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + patience
    leftover = [item for item in live_descendants() if item not in baseline]
    while leftover and time.monotonic() < deadline:
        time.sleep(0.2)
        leftover = [item for item in live_descendants() if item not in baseline]
    return leftover


def run_once(run_dir: Path, variant: str, inject_leak: bool) -> dict[str, Any]:
    """One independent Gate 1 run inside this (child) process."""
    from app import fixture_origin
    from app.config import get_settings

    run_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    log: list[dict[str, Any]] = []
    fixtures = _start_fixtures(variant, log)
    origin = f"http://127.0.0.1:{fixtures.server_address[1]}"
    data: dict[str, Any] = {"fixture_origin": origin, "server_log": log, "expected_fields": expected_fields(form_html_for(variant)),
                            "cdp_in_app_code": _cdp_in_app_code(),
                            "stdin_closed": sys.stdin is None or sys.stdin.closed or not sys.stdin.isatty()}
    leak = None
    try:
        fixture_origin.enable_for_gate1(origin, get_settings(), acknowledgement=fixture_origin.GATE1_ACKNOWLEDGEMENT)
        baseline = live_descendants()
        sampler, application_id = _drive_api(data, origin)
        if inject_leak:
            # Negative test hook: a stray child process the cleanup check must catch.
            leak = SPAWN.Process(target=time.sleep, args=(120,), name="gate1-injected-leak")
            leak.start()
        data["leftover_children"] = _wait_for_cleanup(baseline, 0 if inject_leak else 10)
        data["sampled_processes"] = sampler.processes
        data["leftover_sampled"] = still_running(sampler.processes)
        data["ledger"] = _ledger(application_id)
    finally:
        fixture_origin.disable()
        fixtures.shutdown()
        fixtures.server_close()
        if leak is not None:
            leak.kill()
            leak.join(timeout=10)
    checks = run_checks(data)
    return {
        "variant": variant, "inject_leak": inject_leak, "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(), "pid": os.getpid(), "fixture_origin": origin,
        "fixtures": {"form": str(FORM_FIXTURE.relative_to(V2_ROOT)), "api": str(API_FIXTURE.relative_to(V2_ROOT)),
                     "negative_snippet": VARIANTS[variant]},
        "ok": all(item["ok"] for item in checks.values()),
        "checks": checks,
        "browser_processes": [item for item in data.get("sampled_processes") or [] if is_browser(item)],
    }



# --------------------------------------------------------------------------- orchestration (parent)

def child_env(run_dir: Path) -> dict[str, str]:
    """A minimal, explicit environment: nothing from a developer's .env or shell leaks in."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PLAYWRIGHT_BROWSERS_PATH", "SYSTEMROOT")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update({
        "PYTHONPATH": str(V2_ROOT),
        "PYTHONUNBUFFERED": "1",
        "API_KEY": API_KEY,
        "HOST": "127.0.0.1",
        "DATABASE_PATH": str(run_dir / "data" / "gate1.db"),
        "BACKUP_DIR": str(run_dir / "data" / "backups"),
        "MATERIALS_DIR": str(run_dir / "data" / "materials"),
        "APPLICATION_MODE": "dry_run",
        "AUTOMATION_ENABLED": "true",
        "ALLOW_LIVE_SUBMISSION": "false",
        "CONTINUOUS_RUN_ENABLED": "false",
        "BACKUP_INTERVAL_HOURS": "0",
        "GREENHOUSE_BROWSER_VERIFY": "true",
        "GREENHOUSE_BROWSER_FALLBACK": "false",
        "GREENHOUSE_BROWSER_TIMEOUT": "60",
        "BROWSER_TRACE_DIR": str(run_dir / "traces"),
        "MATERIALS_LLM_ENABLED": "false",
        "LLM_PROVIDER": "none",
        "OLLAMA_BASE_URL": "http://127.0.0.1:9",
    })
    return env


def _child_entry(run_dir: str, variant: str, inject_leak: bool) -> None:
    """Entry point of one spawned run: isolated env, cwd, and output; then one Gate 1 run."""
    folder = Path(run_dir)
    os.environ.clear()
    os.environ.update(child_env(folder))
    os.chdir(folder)
    sys.path.insert(0, str(V2_ROOT))
    with (folder / "child.log").open("w", encoding="utf-8") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        result = run_once(folder, variant, inject_leak)
    (folder / "run.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def supervise(child: Any, timeout: float, grace: float = 5.0) -> dict[str, Any]:
    """Wait for a started run process; on timeout kill it *and* every descendant it started.

    The parent samples the child's whole process tree while it runs, so even
    Chromium (which Playwright starts in its own process group) and processes
    reparented after a crash are found. Anything that outlives the child is an
    orphan: it fails the run and is killed and reaped here.
    """
    with ProcessSampler(interval=0.1, pid=child.pid) as sampler:
        child.join(timeout)
        timed_out = child.is_alive()
        if timed_out:
            tree = live_descendants(child.pid)  # snapshot before killing the child breaks the tree
            child.kill()
            child.join(10)
            terminate(tree)
    seen = sampler.processes
    deadline = time.monotonic() + grace
    orphans = still_running(seen)
    while orphans and not timed_out and time.monotonic() < deadline:
        time.sleep(0.2)
        orphans = still_running(seen)
    survivors = terminate(orphans)
    return {"timed_out": timed_out, "exit_code": child.exitcode, "descendants_seen": len(seen),
            "orphans": orphans, "orphans_not_killed": survivors}


def run_child(index: int, out: Path, variant: str, inject_leak: bool, timeout: float) -> dict[str, Any]:
    run_dir = (out / f"run-{index + 1}").resolve()
    shutil.rmtree(run_dir, ignore_errors=True)  # every run starts from an empty database and trace folder
    run_dir.mkdir(parents=True)
    result_path = run_dir / "run.json"
    begin = time.monotonic()
    child = SPAWN.Process(target=_child_entry, args=(str(run_dir), variant, inject_leak), name=f"gate1-run-{index + 1}")
    child.start()
    supervision = supervise(child, timeout)
    returncode = supervision["exit_code"]
    if supervision["timed_out"] or not result_path.is_file():
        log = run_dir / "child.log"
        reason = f"timed out after {timeout:g}s" if supervision["timed_out"] else f"exited {returncode}"
        return {"run": index + 1, "ok": False, "error": f"run process {reason} without a result",
                "supervision": supervision, "run_dir": str(run_dir),
                "log_tail": log.read_text(encoding="utf-8")[-4000:] if log.is_file() else ""}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # Independent re-check after the run process is gone: no browser it started, and no other
    # descendant the parent saw, may survive it (survivors are killed by supervise()).
    orphans = still_running(result.get("browser_processes") or [])
    result["checks"]["browser_gone_after_child_exit"] = {"ok": not orphans, "detail": {"orphans": orphans}}
    result["checks"]["no_process_outlived_the_run"] = {"ok": not supervision["orphans"], "detail": supervision}
    result.update(run=index + 1, child_exit_code=returncode, wall_s=round(time.monotonic() - begin, 3),
                  run_dir=str(run_dir))
    result["ok"] = returncode == 0 and all(item["ok"] for item in result["checks"].values())
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="clean")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per run")
    parser.add_argument("--report-name", default="gate1-report.json")
    parser.add_argument("--inject-leak", action="store_true", help="negative test: leave a stray child process")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for index in range(args.runs):
        run = run_child(index, out, args.variant, args.inject_leak, args.timeout)
        summary = run_summary(run)
        print(f"run {index + 1}/{args.runs}: {'PASS' if run.get('ok') else 'FAIL'} "
              f"trace={summary['trace_sha256']} browser_requests={summary['browser_request_count']} "
              f"server_requests={summary['fixture_server_requests']} failed={summary['failed_checks'] or run.get('error')}",
              flush=True)
        runs.append(run)
    report = build_report(runs, variant=args.variant, requested=args.runs)
    report_path = out / args.report_name
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"gate1 verdict: {report['verdict'].upper()} ({report['runs_passed']}/{args.runs} runs) -> {report_path}")
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
