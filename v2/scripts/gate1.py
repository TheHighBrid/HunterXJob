#!/usr/bin/env python3
"""Gate 1 of docs/RECOVERY_CONTRACT.md: prove the existing dry-run path on plain Linux.

Each run is an independent process that:

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
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

V2_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = V2_ROOT / "tests" / "fixtures"
FORM_FIXTURE = FIXTURES / "sample_form.html"
API_FIXTURE = FIXTURES / "greenhouse" / "d2l_7696196.json"
NEGATIVE_DIR = FIXTURES / "gate1"
BOARD, JOB_ID = "d2l", "7696196"
API_PATH = f"/v1/boards/{BOARD}/jobs/{JOB_ID}"
EMBED_PATH = "/embed/job_app"
EMBED_QUERY = {"for": [BOARD], "token": [JOB_ID]}
DEFAULT_OUT = V2_ROOT / "gate-artifacts"
API_KEY = "gate1-" + "k" * 40  # fake, process-local
VARIANTS = {
    "clean": None,
    "post_beacon": "inject_post_beacon.html",
    "auto_submit_post": "inject_auto_submit_post.html",
    "auto_submit_get": "inject_auto_submit_get.html",
}
BROWSER_NAMES = ("chrome", "chromium", "headless_shell")

# Made-up identity and answers. Keys follow the fixture forms; nothing personal.
FAKE_ANSWERS = {
    "first_name": "Gate",
    "last_name": "Fixture",
    "email": "gate1.fixture@example.test",
    "phone": "555-0100",
    "resume": "gate1-fake-resume.pdf",
    "work_authorization": "Authorized to work in Canada",
    "work_authorization_ca": "Yes",
    "willing_to_relocate": "Yes",
    "salary_expectation": "70K+",
    "referral_source": "Other",
    "time_zone": "GMT-5 :Eastern Standard Time (EST)",
    "question_64283526": "C2: Bilingual/Native speaker",
    "question_64283529": "Yes",
    "question_64283531": "4 - daily SQL for reporting",
    "question_65529047": "No, I have not worked for D2L in any capacity",
    "question_64283538": "Yes",
}

# What the production form engine must plan from FAKE_ANSWERS for this fixture pair:
# the six sample_form.html fields it can fill plus the D2L API questions, keyed by
# form field. Written out by hand so the check does not trust the engine itself.
EXPECTED_PLAN: dict[str, Any] = {
    "first_name": "Gate",
    "last_name": "Fixture",
    "email": "gate1.fixture@example.test",
    "phone": "555-0100",
    "resume": "gate1-fake-resume.pdf",
    "work_authorization": "Authorized to work in Canada",
    "question_64283526": ["C2: Bilingual/Native speaker"],
    "question_64283527": "Yes",
    "question_64283528": "Yes",
    "question_64283529": "Yes",
    "question_64283530": "70K+",
    "question_64283531": "4 - daily SQL for reporting",
    "question_64283535": "Other",
    "question_65529047": "No, I have not worked for D2L in any capacity",
    "question_64283539": "GMT-5 :Eastern Standard Time (EST)",
    "question_64283538": "Yes",
}

NOT_PROVEN = {
    "real_employer_site": False,
    "real_greenhouse_network_or_anti_bot_behaviour": False,
    "captcha_or_bot_challenge_handling": False,
    "in_page_typing_of_values": False,
    "file_upload": False,
    "live_submission": False,
    "discovery_scoring_and_materials_approval_path": False,
    "scheduler_or_continuous_run_path": False,
    "lever_and_ashby_browser_paths": False,
    "android_termux_proot_execution": False,
}
NOT_PROVEN_NOTES = {
    "real_employer_site": "Gate 1 only loads repo fixtures over 127.0.0.1; Gate 2 covers one real public Greenhouse posting.",
    "real_greenhouse_network_or_anti_bot_behaviour": "No real Greenhouse host is contacted.",
    "captcha_or_bot_challenge_handling": "The fixture has no CAPTCHA or interstitial challenge.",
    "in_page_typing_of_values": "The production engine plans values and verifies the rendered page read-only; it never types "
                                "into the page. 'Filled values' here are the planned values read back from the ledger.",
    "file_upload": "Résumé is a planned vault value; nothing is uploaded.",
    "live_submission": "Out of scope by design; live submit stays locked.",
    "discovery_scoring_and_materials_approval_path": "The job and application are seeded at ready_to_apply; answers go "
                                                     "through POST /api/answers.",
    "scheduler_or_continuous_run_path": "The dry-run is triggered by the API route, not by a scheduled cycle.",
    "lever_and_ashby_browser_paths": "Greenhouse only.",
    "android_termux_proot_execution": "Frozen by the recovery contract; not exercised.",
}


# --------------------------------------------------------------------------- helpers (unit-tested)

class _FormFieldParser(HTMLParser):
    """Independent oracle: the fields a fixture page declares, read from its HTML source."""

    def __init__(self) -> None:
        super().__init__()
        self.fields: list[dict[str, Any]] = []
        self._select: dict[str, Any] | None = None
        self._option: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"input", "select", "textarea"}:
            ident = values.get("id") or values.get("name") or ""
            kind = (values.get("type") or "").lower()
            if not ident or kind in {"hidden", "submit", "button", "search"}:
                return
            field = {"key": ident, "tag": tag, "type": kind, "required": "required" in values, "options": []}
            self.fields.append(field)
            if tag == "select":
                self._select = field
        elif tag == "option" and self._select is not None:
            self._option = []

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._select is not None and self._option is not None:
            text = " ".join("".join(self._option).split())
            if text:
                self._select["options"].append(text)
            self._option = None
        elif tag == "select":
            self._select = None


def expected_fields(html: str) -> list[dict[str, Any]]:
    parser = _FormFieldParser()
    parser.feed(html)
    return parser.fields


def compare_fields(expected: list[dict[str, Any]], extracted: list[dict[str, Any]]) -> list[str]:
    """Differences between the fixture's declared fields and what the browser extracted."""
    problems = []
    got = {(item.get("id") or item.get("name") or item.get("key")): item for item in extracted}
    want = {item["key"]: item for item in expected}
    for key in sorted(set(want) - set(got)):
        problems.append(f"missing field {key}")
    for key in sorted(set(got) - set(want)):
        problems.append(f"unexpected field {key}")
    for key in sorted(set(want) & set(got)):
        for attr in ("tag", "type", "required", "options"):
            if want[key][attr] != got[key].get(attr):
                problems.append(f"{key}.{attr}: expected {want[key][attr]!r}, got {got[key].get(attr)!r}")
    return problems


def inspect_trace_zip(path: Path) -> dict[str, Any]:
    """Re-verify a trace zip independently of the app: readable, CRC-clean, has trace + network logs."""
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not result["exists"]:
        return {**result, "ok": False}
    data = path.read_bytes()
    result.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            bad = archive.testzip()
            network = b"".join(archive.read(name) for name in names if name.endswith(".network"))
            result.update(entries=len(names), crc_ok=bad is None,
                          has_trace=any(name.endswith(".trace") for name in names),
                          has_network=any(name.endswith(".network") for name in names),
                          network_mentions_embed=EMBED_PATH.encode() in network)
    except zipfile.BadZipFile as exc:
        return {**result, "ok": False, "error": str(exc)}
    result["ok"] = all(result[key] for key in ("crc_ok", "has_trace", "has_network", "network_mentions_embed"))
    return result


def _proc_info(proc: Any) -> dict[str, Any] | None:
    try:
        with proc.oneshot():
            return {"pid": proc.pid, "name": proc.name(), "create_time": proc.create_time(),
                    "exe": proc.exe(), "cmdline": " ".join(proc.cmdline())[:600], "ppid": proc.ppid()}
    except Exception:  # noqa: BLE001 - a process can vanish or be inaccessible while sampled
        return None


def live_descendants(pid: int | None = None) -> list[dict[str, Any]]:
    """Child processes (recursive) of ``pid`` (default: this process) that are still alive."""
    import psutil

    try:
        children = psutil.Process(pid or os.getpid()).children(recursive=True)
    except psutil.NoSuchProcess:
        return []
    alive = []
    for child in children:
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                continue
        except psutil.NoSuchProcess:
            continue
        info = _proc_info(child)
        if info:
            alive.append(info)
    return alive


def still_running(processes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which of the sampled processes still exist (same PID *and* same start time)."""
    import psutil

    leftover = []
    for item in processes:
        try:
            proc = psutil.Process(item["pid"])
            if abs(proc.create_time() - item["create_time"]) < 0.01 and proc.status() != psutil.STATUS_ZOMBIE:
                leftover.append(item)
        except psutil.NoSuchProcess:
            continue
    return leftover


def is_browser(info: dict[str, Any]) -> bool:
    name = (info.get("name") or "").lower()
    return any(token in name for token in BROWSER_NAMES)


class ProcessSampler:
    """Polls this process's descendants while the dry-run runs, to see the browser Playwright starts."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.seen: dict[tuple[int, float], dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="gate1-sampler", daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            for info in live_descendants():
                self.seen.setdefault((info["pid"], info["create_time"]), info)
            self._stop.wait(self.interval)

    def __enter__(self) -> ProcessSampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    @property
    def processes(self) -> list[dict[str, Any]]:
        return list(self.seen.values())


def _browser_problems(evidence: dict[str, Any], origin: str, embed_url: str) -> list[str]:
    problems = []
    if evidence.get("non_get_attempts"):
        problems.append(f"browser attempted {evidence['non_get_attempts']} non-GET request(s)")
    if evidence.get("aborted"):
        problems.append(f"browser router aborted {evidence['aborted']} request(s)")
    if evidence.get("submit_events"):
        problems.append(f"page fired {len(evidence['submit_events'])} submit event(s)")
    navigations = evidence.get("main_frame_navigations") or []
    if navigations != [embed_url]:
        problems.append(f"main frame navigations {navigations!r} != [{embed_url!r}]")
    if evidence.get("requests_truncated"):
        problems.append("browser request log was truncated")
    return problems


def _request_problems(evidence: dict[str, Any], origin: str) -> list[str]:
    problems = []
    for item in evidence.get("requests") or []:
        if item.get("method") not in {"GET", "HEAD"}:
            problems.append(f"non-GET browser request {item.get('method')} {item.get('url')}")
        if not str(item.get("url", "")).startswith(origin + "/"):
            problems.append(f"browser request left the fixture origin: {item.get('url')}")
    return problems


def _server_problems(server_log: list[dict[str, Any]]) -> list[str]:
    problems = []
    for entry in server_log:
        if entry["method"] not in {"GET", "HEAD"}:
            problems.append(f"fixture server received {entry['method']} {entry['path']}")
        if not entry["expected"]:
            problems.append(f"fixture server received unexpected request {entry['method']} {entry['path']}?{entry['query']}")
    return problems


def evaluate_network(evidence: dict[str, Any], server_log: list[dict[str, Any]], origin: str, embed_url: str) -> list[str]:
    """Every reason the session cannot count as zero-submit / zero-non-GET (browser log and server log)."""
    return (_browser_problems(evidence, origin, embed_url) + _request_problems(evidence, origin)
            + _server_problems(server_log))


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

        do_HEAD = do_GET

        def _refuse(self) -> None:
            self._record(False, 405)
            self._send(405, b"gate1 fixtures are read-only", "text/plain")

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse

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


class RunContext:
    """Everything one run observed, handed to the individual checks."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.response = data.get("response") or {}
        self.result = self.response.get("json") or {}
        self.form = self.result.get("form") or {}
        self.metadata = self.form.get("metadata") or {}
        self.evidence = self.metadata.get("browser_evidence") or {}
        self.ledger = data.get("ledger") or {"answers": {}, "validation": {}, "evidence": [], "events": [],
                                             "application_stage": None}
        self.origin = data["fixture_origin"]
        self.embed_url = f"{self.origin}{EMBED_PATH}?for={BOARD}&token={JOB_ID}"
        self.browsers = [item for item in data.get("sampled_processes") or [] if is_browser(item)]


def check_api(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    result = ctx.result
    ok = (ctx.response.get("status_code") == 200 and result.get("status") == "dry_run_complete"
          and result.get("submitted") is False and bool(ctx.data.get("api_shutdown_clean")))
    return ok, {"status_code": ctx.response.get("status_code"), "status": result.get("status"),
                "reason": result.get("reason"), "detail": result.get("detail"), "submitted": result.get("submitted"),
                "elapsed_s": ctx.response.get("elapsed_s"), "api_shutdown_clean": ctx.data.get("api_shutdown_clean")}


def check_launch(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    executable = str(ctx.evidence.get("executable_path") or "")
    browsers = ctx.browsers
    # Every browser process (main, zygote, GPU, renderer, crashpad) must come from Playwright's own install.
    folder = str(Path(executable).parent) if executable else "<none>"
    own_binary = bool(browsers) and all(str(Path(item["exe"]).parent) == folder for item in browsers if item["exe"]) and any(
        item["exe"] == executable for item in browsers)
    pipe_not_port = (bool(browsers) and all("--remote-debugging-port" not in item["cmdline"] for item in browsers)
                     and any("--remote-debugging-pipe" in item["cmdline"] for item in browsers))
    ok = (ctx.evidence.get("launcher") == "playwright.chromium.launch" and own_binary and pipe_not_port
          and "ms-playwright" in executable and not ctx.data.get("cdp_in_app_code"))
    return ok, {"launcher": ctx.evidence.get("launcher"), "browser_version": ctx.evidence.get("browser_version"),
                "executable_path": executable, "browser_processes_seen": len(browsers),
                "sampled_from": "descendants of the gate run process only", "remote_debugging_pipe_not_port": pipe_not_port,
                "connect_over_cdp_in_app_code": ctx.data.get("cdp_in_app_code")}


def check_fixture_loaded(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    hits = [entry for entry in ctx.data["server_log"] if entry["path"] == EMBED_PATH and entry["expected"]]
    ok = len(hits) == 1 and ctx.metadata.get("dom_url") == ctx.embed_url and ctx.metadata.get("dom_verified") is True
    return ok, {"embed_requests": len(hits), "dom_url": ctx.metadata.get("dom_url"), "title": ctx.form.get("title")}


def check_fields(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    expected = ctx.data["expected_fields"]
    extracted = ctx.evidence.get("dom_fields") or []
    problems = compare_fields(expected, extracted)
    answers = ctx.ledger["answers"]
    wrong = sorted(key for key, value in answers.items() if EXPECTED_PLAN.get(key) != value)
    not_planned = sorted(set(EXPECTED_PLAN) - set(answers))
    unplanned = [item["key"] for item in expected if item["required"] and item["key"] not in answers]
    ok = not (problems or wrong or not_planned or unplanned) and set(answers) == set(ctx.result.get("filled") or [])
    return ok, {"expected_dom_fields": [item["key"] for item in expected],
                "extracted_dom_fields": [item.get("key") for item in extracted], "field_problems": problems,
                "planned_values_read_back": len(answers), "mismatched_values": wrong,
                "expected_but_not_planned": not_planned, "required_dom_fields_not_planned": unplanned}


def check_trace(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    reported = ctx.evidence.get("trace") or {}
    trace = inspect_trace_zip(Path(reported.get("path") or "/nonexistent"))
    ok = bool(trace.get("ok") and reported.get("zip_ok") and reported.get("sha256") == trace.get("sha256"))
    ctx.data["trace_sha256"] = trace.get("sha256")
    return ok, {**trace, "app_reported_sha256": reported.get("sha256")}


def check_ledger(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    from app.evidence import payload_hash
    from app.pipeline import browser_ledger_entry

    rows = ctx.ledger["evidence"]
    expected = {"filled": ctx.ledger["answers"], "form_source": ctx.form.get("source"),
                "materials": (ctx.ledger["validation"] or {}).get("materials") or [],
                "browser": browser_ledger_entry(ctx.form)}
    row = rows[0] if len(rows) == 1 else {}
    ok = (row.get("kind") == "dry_run" and row.get("sufficient") is False
          and row.get("payload_hash") == payload_hash(expected)
          and f"sha256={ctx.data.get('trace_sha256')}" in (row.get("confirmation_text") or "")
          and ctx.ledger["application_stage"] == "validated")
    return ok, {"rows": rows, "recomputed_payload_hash": payload_hash(expected),
                "application_stage": ctx.ledger["application_stage"], "events": ctx.ledger["events"]}


def check_network(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    log = ctx.data["server_log"]
    problems = evaluate_network(ctx.evidence, log, ctx.origin, ctx.embed_url)
    if any(row["kind"] == "submission" for row in ctx.ledger["evidence"]):
        problems.append("a submission evidence row exists")
    methods: dict[str, int] = {}
    for entry in log:
        methods[entry["method"]] = methods.get(entry["method"], 0) + 1
    evidence = ctx.evidence
    return not problems, {"problems": problems, "browser_request_count": evidence.get("request_count"),
                          "browser_requests": evidence.get("requests"),
                          "browser_non_get_attempts": evidence.get("non_get_attempts"),
                          "submit_events": evidence.get("submit_events"),
                          "main_frame_navigations": evidence.get("main_frame_navigations"),
                          "fixture_server_requests": len(log), "fixture_server_methods": methods,
                          "fixture_server_log": log}


def check_cleanup(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    leftover, sampled = ctx.data.get("leftover_children") or [], ctx.data.get("leftover_sampled") or []
    ok = not leftover and not sampled and bool(ctx.browsers)
    return ok, {"browser_pids_seen": [item["pid"] for item in ctx.browsers],
                "all_descendant_pids_seen": [item["pid"] for item in ctx.data.get("sampled_processes") or []],
                "leftover_children": leftover, "leftover_sampled": sampled}


def check_unattended(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    return bool(ctx.data.get("stdin_closed")), {"stdin": "closed (subprocess stdin=DEVNULL)"}


CHECKS = (
    ("api_completed_cleanly", check_api),
    ("chromium_launched_by_playwright", check_launch),
    ("fixture_loaded", check_fixture_loaded),
    ("fields_extracted_and_planned_values_match", check_fields),
    ("trace_zip_valid_and_hashed", check_trace),
    ("evidence_persisted_in_ledger", check_ledger),
    ("zero_submit_and_non_get", check_network),
    ("browser_and_children_gone_after_shutdown", check_cleanup),
    ("no_human_input", check_unattended),
)


def run_checks(data: dict[str, Any]) -> dict[str, Any]:
    ctx = RunContext(data)
    checks = {}
    for name, check in CHECKS:
        ok, detail = check(ctx)
        checks[name] = {"ok": bool(ok), "detail": detail}
    return checks


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
            leak = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])  # nosec B603
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
            leak.wait(timeout=10)
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


def run_child(index: int, out: Path, variant: str, inject_leak: bool, timeout: float) -> dict[str, Any]:
    run_dir = (out / f"run-{index + 1}").resolve()
    shutil.rmtree(run_dir, ignore_errors=True)  # every run starts from an empty database and trace folder
    run_dir.mkdir(parents=True)
    result_path = run_dir / "run.json"
    command = [sys.executable, str(Path(__file__).resolve()), "--child", "--run-dir", str(run_dir), "--variant", variant]
    if inject_leak:
        command.append("--inject-leak")
    begin = time.monotonic()
    try:
        # Re-runs this very script with the current interpreter; no shell, no outside input.
        options: dict[str, Any] = {"cwd": run_dir, "env": child_env(run_dir), "stdin": subprocess.DEVNULL,
                                   "capture_output": True, "text": True, "timeout": timeout, "check": False}
        proc = subprocess.run(command, **options)  # noqa: S603  # nosec B603
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode, stdout, stderr = None, str(exc.stdout or ""), f"timed out after {timeout}s"
    (run_dir / "child.log").write_text(f"$ {' '.join(command)}\n--- stdout\n{stdout}\n--- stderr\n{stderr}\n", encoding="utf-8")
    if not result_path.is_file():
        return {"run": index + 1, "ok": False, "error": f"child exited {returncode} without a result",
                "stderr_tail": stderr[-4000:], "run_dir": str(run_dir)}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    # Independent re-check after the child is gone: no browser it started may survive it.
    orphans = still_running(result.get("browser_processes") or [])
    result["checks"]["browser_gone_after_child_exit"] = {"ok": not orphans, "detail": {"orphans": orphans}}
    result.update(run=index + 1, child_exit_code=returncode, wall_s=round(time.monotonic() - begin, 3),
                  run_dir=str(run_dir))
    result["ok"] = returncode == 0 and all(item["ok"] for item in result["checks"].values())
    return result


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    checks = run.get("checks") or {}
    trace = (checks.get("trace_zip_valid_and_hashed") or {}).get("detail") or {}
    net = (checks.get("zero_submit_and_non_get") or {}).get("detail") or {}
    cleanup = (checks.get("browser_and_children_gone_after_shutdown") or {}).get("detail") or {}
    return {
        "run": run.get("run"), "ok": run.get("ok"), "error": run.get("error"),
        "failed_checks": sorted(name for name, item in checks.items() if not item["ok"]),
        "trace_sha256": trace.get("sha256"), "trace_bytes": trace.get("bytes"),
        "browser_request_count": net.get("browser_request_count"),
        "browser_non_get_attempts": net.get("browser_non_get_attempts"),
        "fixture_server_requests": net.get("fixture_server_requests"),
        "fixture_server_methods": net.get("fixture_server_methods"),
        "browser_pids": cleanup.get("browser_pids_seen"),
        "leftover_processes": len(cleanup.get("leftover_children") or []) + len(cleanup.get("leftover_sampled") or []),
    }


def build_report(runs: list[dict[str, Any]], *, variant: str, requested: int) -> dict[str, Any]:
    passed = [run for run in runs if run.get("ok")]
    green = variant == "clean" and requested >= 3 and len(passed) == len(runs) == requested
    hashes = [_run_summary(run)["trace_sha256"] for run in runs]
    proven = {
        "chromium_launched_by_playwright": green,
        "fixture_loads_over_loopback": green,
        "fields_extracted_and_planned_values_match": green,
        "trace_zip_valid_integrity_checked_and_hashed": green,
        "evidence_persisted_in_ledger": green,
        "zero_submit_actions_and_zero_non_get_requests": green,
        "browser_and_child_processes_gone_after_shutdown": green,
        "api_request_completes_cleanly": green,
        "reproducible_without_a_human": green,
    }
    return {
        "gate": "gate1",
        "contract": "docs/RECOVERY_CONTRACT.md",
        "generated_at": datetime.now(UTC).isoformat(),
        "host": {"platform": sys.platform, "python": sys.version.split()[0], "ci": bool(os.environ.get("CI")),
                 "github_run_id": os.environ.get("GITHUB_RUN_ID")},
        "variant": variant,
        "runs_requested": requested,
        "runs_passed": len(passed),
        "verdict": "pass" if green else "fail",
        "distinct_trace_hashes": len({value for value in hashes if value}),
        "proven": proven,
        "not_proven": NOT_PROVEN,
        "not_proven_notes": NOT_PROVEN_NOTES,
        "summary": [_run_summary(run) for run in runs],
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="clean")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per run")
    parser.add_argument("--report-name", default="gate1-report.json")
    parser.add_argument("--inject-leak", action="store_true", help="negative test: leave a stray child process")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        sys.path.insert(0, str(V2_ROOT))
        result = run_once(args.run_dir, args.variant, args.inject_leak)
        (args.run_dir / "run.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        return 0

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    runs = []
    for index in range(args.runs):
        run = run_child(index, out, args.variant, args.inject_leak, args.timeout)
        summary = _run_summary(run)
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
