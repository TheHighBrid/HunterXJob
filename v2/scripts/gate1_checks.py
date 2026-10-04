"""Gate 1 checks, kept separate from the runner (scripts/gate1.py) so each stays small.

Pure helpers: the fixture-field oracle, trace-zip verification, process-leak
detection, network evaluation, the per-run checks, and the report. See
docs/RECOVERY_CONTRACT.md for what each check proves.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

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
    "iframe_form_submit": "inject_iframe_form_submit.html",
    "head_request": "inject_head_request.html",
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
    if not evidence:
        return ["the API result carries no browser evidence (browser verification did not complete)"]
    problems = []
    if evidence.get("non_get_attempts"):
        problems.append(f"browser attempted {evidence['non_get_attempts']} non-GET request(s)")
    if evidence.get("aborted"):
        problems.append(f"browser router aborted {evidence['aborted']} request(s)")
    if evidence.get("submit_events"):
        problems.append(f"page attempted {len(evidence['submit_events'])} form submission(s)")
    if evidence.get("service_workers") != "block":
        problems.append("service workers were not blocked in the browser context")
    navigations = evidence.get("main_frame_navigations") or []
    if navigations != [embed_url]:
        problems.append(f"main frame navigations {navigations!r} != [{embed_url!r}]")
    if evidence.get("requests_truncated"):
        problems.append("browser request log was truncated")
    return problems


def _request_problems(evidence: dict[str, Any], origin: str) -> list[str]:
    problems = []
    for item in evidence.get("requests") or []:
        if item.get("method") != "GET":
            problems.append(f"non-GET browser request {item.get('method')} {item.get('url')}")
        if not str(item.get("url", "")).startswith(origin + "/"):
            problems.append(f"browser request left the fixture origin: {item.get('url')}")
    return problems


def _server_problems(server_log: list[dict[str, Any]]) -> list[str]:
    problems = []
    for entry in server_log:
        if entry["method"] != "GET":
            problems.append(f"fixture server received {entry['method']} {entry['path']}")
        if not entry["expected"]:
            problems.append(f"fixture server received unexpected request {entry['method']} {entry['path']}?{entry['query']}")
    return problems


def evaluate_network(evidence: dict[str, Any], server_log: list[dict[str, Any]], origin: str, embed_url: str) -> list[str]:
    """Every reason the session cannot count as zero-submit / zero-non-GET (browser log and server log)."""
    return (_browser_problems(evidence, origin, embed_url) + _request_problems(evidence, origin)
            + _server_problems(server_log))


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


def _from_playwright_install(browsers: list[dict[str, Any]], executable: str) -> bool:
    """Every browser process (main, zygote, GPU, renderer, crashpad) comes from Playwright's own install."""
    if not browsers or not executable:
        return False
    folder = str(Path(executable).parent)
    same_folder = all(str(Path(item["exe"]).parent) == folder for item in browsers if item["exe"])
    return same_folder and any(item["exe"] == executable for item in browsers)


def _pipe_not_port(browsers: list[dict[str, Any]]) -> bool:
    """Playwright drives its own browser over a pipe; a debugging port would mean CDP attachment."""
    no_port = all("--remote-debugging-port" not in item["cmdline"] for item in browsers)
    return bool(browsers) and no_port and any("--remote-debugging-pipe" in item["cmdline"] for item in browsers)


def check_launch(ctx: RunContext) -> tuple[bool, dict[str, Any]]:
    executable = str(ctx.evidence.get("executable_path") or "")
    own_binary = _from_playwright_install(ctx.browsers, executable)
    pipe_not_port = _pipe_not_port(ctx.browsers)
    ok = (ctx.evidence.get("launcher") == "playwright.chromium.launch" and own_binary and pipe_not_port
          and "ms-playwright" in executable and not ctx.data.get("cdp_in_app_code"))
    return ok, {"launcher": ctx.evidence.get("launcher"), "browser_version": ctx.evidence.get("browser_version"),
                "executable_path": executable, "browser_processes_seen": len(ctx.browsers),
                "from_playwright_install": own_binary, "sampled_from": "descendants of the gate run process only",
                "remote_debugging_pipe_not_port": pipe_not_port,
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
    return bool(ctx.data.get("stdin_closed")), {"stdin": "not a terminal (spawned run process, stdin is /dev/null)"}


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


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
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
    hashes = [run_summary(run)["trace_sha256"] for run in runs]
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
        "summary": [run_summary(run) for run in runs],
        "runs": runs,
    }
